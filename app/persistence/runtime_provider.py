from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver
from psycopg_pool import AsyncConnectionPool

from app.agent.workflow_models import AgentSessionInfo
from app.memory.conversation import ConversationMemoryStore
from app.persistence.postgres_checkpointer import (
    PostgresCheckpointRuntime,
    PostgresCheckpointStatus,
)
from app.persistence.postgres_connection import PostgresStateConnectionPool
from app.persistence.postgres_memory import PostgresConversationMemoryStore
from app.persistence.postgres_runtime import PostgresAgentStateStore
from app.persistence.postgres_submission import PostgresMockApprovalSubmitter
from app.persistence.sqlite_checkpointer import SQLiteCheckpointSaver
from app.persistence.sqlite_memory import SQLiteConversationMemoryStore
from app.persistence.sqlite_runtime import (
    SQLiteAgentStateStore,
    SQLiteMockApprovalSubmitter,
)
from app.persistence.state_provider import AgentStateProviderName
from app.tools.draft_models import ApplicationDraft, DraftGenerationResult, DraftUserContext
from app.tools.submission_models import MockApprovalSubmissionResult

POSTGRES_ACTIVATION_BLOCKER = "redis_session_coordination_required"


class _SecretValue(Protocol):
    def get_secret_value(self) -> str: ...


class AgentRuntimeProviderSettings(Protocol):
    agent_state_provider: AgentStateProviderName
    sqlite_database_path: Path
    agent_postgres_dsn: _SecretValue
    agent_postgres_min_pool_size: int
    agent_postgres_max_pool_size: int
    agent_postgres_connect_timeout_seconds: float


class AgentRuntimeStateStore(Protocol):
    backend_name: str
    survives_process_restart: bool

    async def save_route_state(
        self,
        session: AgentSessionInfo,
        active_draft: DraftGenerationResult | None,
    ) -> None: ...

    async def delete_session(self, session_id: str) -> None: ...


class AgentRuntimeSubmissionService(Protocol):
    backend_name: str
    survives_process_restart: bool

    async def submit(
        self,
        draft: ApplicationDraft,
        *,
        confirmation_text: str,
        user_context: DraftUserContext,
        session_id: str,
        request_id: str,
        submission_idempotency_key: str,
    ) -> MockApprovalSubmissionResult: ...


class _ManagedPool(PostgresStateConnectionPool, Protocol):
    async def open(self) -> None: ...

    async def wait(self, timeout: float = 30.0) -> None: ...

    async def close(self) -> None: ...


class RuntimeProviderPreparationError(RuntimeError):
    """Raised when one explicitly selected runtime backend cannot be prepared safely."""


class RuntimeProviderActivationBlockedError(RuntimeError):
    """Raised when a prepared backend is missing a mandatory cutover dependency."""


@dataclass(frozen=True, slots=True)
class AgentRuntimeProviderStatus:
    provider: AgentStateProviderName
    prepared: bool
    shared_state: bool
    activation_ready: bool
    activation_blocker: str | None
    state_backend: str
    memory_backend: str
    submission_backend: str
    checkpoint_backend: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class AgentRuntimeProviders:
    """One coherent set of Agent persistence dependencies and owned resources."""

    status: AgentRuntimeProviderStatus
    state_store: AgentRuntimeStateStore
    memory_store: ConversationMemoryStore
    submission_service: AgentRuntimeSubmissionService
    checkpointer: BaseCheckpointSaver[Any]
    _state_pool: _ManagedPool | None = field(default=None, repr=False)
    _checkpoint_runtime: PostgresCheckpointRuntime | None = field(default=None, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    @property
    def is_closed(self) -> bool:
        return self._closed

    def require_activation_ready(self) -> None:
        if not self.status.activation_ready:
            blocker = self.status.activation_blocker or "unknown_runtime_dependency"
            raise RuntimeProviderActivationBlockedError(
                f"Agent runtime provider activation is blocked: {blocker}"
            )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: BaseException | None = None
        if self._checkpoint_runtime is not None:
            try:
                await self._checkpoint_runtime.close()
            except BaseException as exc:  # pragma: no cover - defensive shutdown path
                first_error = exc
        if self._state_pool is not None:
            try:
                await self._state_pool.close()
            except BaseException as exc:  # pragma: no cover - defensive shutdown path
                first_error = first_error or exc
        if first_error is not None:
            raise first_error

    async def __aenter__(self) -> AgentRuntimeProviders:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        await self.close()


def _backend_name(component: object) -> str:
    value = getattr(component, "backend_name", "")
    if not isinstance(value, str) or not value:
        raise RuntimeProviderPreparationError("runtime component is missing backend metadata")
    return value


def _sqlite_runtime(settings: AgentRuntimeProviderSettings) -> AgentRuntimeProviders:
    database_path = settings.sqlite_database_path
    state_store = SQLiteAgentStateStore(database_path)
    memory_store = SQLiteConversationMemoryStore(database_path)
    submission_service = SQLiteMockApprovalSubmitter(database_path)
    checkpointer = SQLiteCheckpointSaver(database_path)
    return AgentRuntimeProviders(
        status=AgentRuntimeProviderStatus(
            provider=AgentStateProviderName.SQLITE,
            prepared=True,
            shared_state=False,
            activation_ready=True,
            activation_blocker=None,
            state_backend=_backend_name(state_store),
            memory_backend=_backend_name(memory_store),
            submission_backend=_backend_name(submission_service),
            checkpoint_backend=_backend_name(checkpointer),
        ),
        state_store=state_store,
        memory_store=memory_store,
        submission_service=submission_service,
        checkpointer=checkpointer,
    )


async def _postgres_runtime(
    settings: AgentRuntimeProviderSettings,
    *,
    pool_factory: Callable[..., _ManagedPool],
    checkpoint_runtime_factory: Callable[..., PostgresCheckpointRuntime],
) -> AgentRuntimeProviders:
    dsn = settings.agent_postgres_dsn.get_secret_value().strip()
    if not dsn:
        raise RuntimeProviderPreparationError("PostgreSQL Agent state DSN must not be blank")
    timeout = settings.agent_postgres_connect_timeout_seconds
    state_pool = pool_factory(
        conninfo=dsn,
        kwargs={"autocommit": False, "prepare_threshold": 0},
        min_size=settings.agent_postgres_min_pool_size,
        max_size=settings.agent_postgres_max_pool_size,
        timeout=timeout,
        open=False,
        name="agent-state",
    )
    checkpoint_runtime: PostgresCheckpointRuntime | None = None
    try:
        await state_pool.open()
        await state_pool.wait(timeout=timeout)
        state_store = PostgresAgentStateStore(pool=state_pool)
        await state_store.ping()
        checkpoint_runtime = checkpoint_runtime_factory(
            dsn,
            min_pool_size=settings.agent_postgres_min_pool_size,
            max_pool_size=settings.agent_postgres_max_pool_size,
            connect_timeout_seconds=timeout,
        )
        checkpoint_status: PostgresCheckpointStatus = await checkpoint_runtime.setup()
        if not checkpoint_status.ready:
            raise RuntimeProviderPreparationError(
                "PostgreSQL checkpoint schema is not ready for runtime preparation"
            )
        memory_store = PostgresConversationMemoryStore(pool=state_pool)
        submission_service = PostgresMockApprovalSubmitter(pool=state_pool)
        checkpointer = checkpoint_runtime.checkpointer
        return AgentRuntimeProviders(
            status=AgentRuntimeProviderStatus(
                provider=AgentStateProviderName.POSTGRESQL,
                prepared=True,
                shared_state=True,
                activation_ready=False,
                activation_blocker=POSTGRES_ACTIVATION_BLOCKER,
                state_backend=_backend_name(state_store),
                memory_backend=_backend_name(memory_store),
                submission_backend=_backend_name(submission_service),
                checkpoint_backend=_backend_name(checkpointer),
            ),
            state_store=state_store,
            memory_store=memory_store,
            submission_service=submission_service,
            checkpointer=checkpointer,
            _state_pool=state_pool,
            _checkpoint_runtime=checkpoint_runtime,
        )
    except BaseException as exc:
        if checkpoint_runtime is not None:
            await checkpoint_runtime.close()
        await state_pool.close()
        if isinstance(exc, RuntimeProviderPreparationError):
            raise
        raise RuntimeProviderPreparationError(
            "selected PostgreSQL Agent runtime provider could not be prepared"
        ) from exc


async def prepare_agent_runtime_providers(
    settings: AgentRuntimeProviderSettings,
    *,
    pool_factory: Callable[..., _ManagedPool] = AsyncConnectionPool,
    checkpoint_runtime_factory: Callable[..., PostgresCheckpointRuntime] = (
        PostgresCheckpointRuntime
    ),
) -> AgentRuntimeProviders:
    """Prepare exactly one configured backend without changing FastAPI wiring."""

    if settings.agent_state_provider is AgentStateProviderName.SQLITE:
        return _sqlite_runtime(settings)
    if settings.agent_state_provider is AgentStateProviderName.POSTGRESQL:
        return await _postgres_runtime(
            settings,
            pool_factory=pool_factory,
            checkpoint_runtime_factory=checkpoint_runtime_factory,
        )
    raise RuntimeProviderPreparationError(
        f"unsupported Agent state provider: {settings.agent_state_provider!s}"
    )


__all__ = [
    "POSTGRES_ACTIVATION_BLOCKER",
    "AgentRuntimeProviderSettings",
    "AgentRuntimeProviderStatus",
    "AgentRuntimeProviders",
    "AgentRuntimeStateStore",
    "AgentRuntimeSubmissionService",
    "RuntimeProviderActivationBlockedError",
    "RuntimeProviderPreparationError",
    "prepare_agent_runtime_providers",
]
