from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.persistence import runtime_provider as runtime_provider_module
from app.persistence import (
    POSTGRES_ACTIVATION_BLOCKER,
    AgentStateProviderName,
    RuntimeProviderActivationBlockedError,
    RuntimeProviderPreparationError,
    prepare_agent_runtime_providers,
)
from app.persistence.postgres_checkpointer import PostgresCheckpointStatus
from app.persistence.postgres_memory import PostgresConversationMemoryStore
from app.persistence.postgres_runtime import PostgresAgentStateStore
from app.persistence.postgres_submission import PostgresMockApprovalSubmitter


def _settings(
    provider: AgentStateProviderName,
    *,
    database_path: Path,
) -> SimpleNamespace:
    return SimpleNamespace(
        agent_state_provider=provider,
        sqlite_database_path=database_path,
        agent_postgres_dsn=SecretStr(
            "postgresql://agent:runtime-secret@postgres/agent_runtime_test"
        ),
        agent_postgres_min_pool_size=2,
        agent_postgres_max_pool_size=6,
        agent_postgres_connect_timeout_seconds=7.0,
    )


class _Cursor:
    def __init__(self, row) -> None:
        self._row = row

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return ()


class _Connection:
    async def execute(self, query: str, params=None):
        del params
        normalized = " ".join(query.split())
        if normalized == "SELECT 1":
            return _Cursor((1,))
        if "MAX(version)" in normalized:
            return _Cursor((1,))
        raise AssertionError(f"unexpected SQL: {normalized}")


class _Pool:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.opened = False
        self.closed = False
        self.wait_timeout = None

    async def open(self) -> None:
        self.opened = True

    async def wait(self, timeout: float = 30.0) -> None:
        self.wait_timeout = timeout

    async def close(self) -> None:
        self.closed = True

    @asynccontextmanager
    async def connection(self, timeout=None):
        del timeout
        yield _Connection()


class _Checkpointer:
    backend_name = "postgresql"
    survives_process_restart = True


class _SQLiteComponent:
    backend_name = "sqlite"
    survives_process_restart = True

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path


class _CheckpointRuntime:
    instances: list[_CheckpointRuntime] = []

    def __init__(self, dsn: str, **kwargs) -> None:
        self.dsn = dsn
        self.kwargs = kwargs
        self.checkpointer = _Checkpointer()
        self.closed = False
        self.instances.append(self)

    async def setup(self) -> PostgresCheckpointStatus:
        return PostgresCheckpointStatus(
            schema_name="agent_runtime",
            supported_version=9,
            current_version=9,
            tables=(
                "checkpoint_blobs",
                "checkpoint_migrations",
                "checkpoint_writes",
                "checkpoints",
            ),
            missing_tables=(),
        )

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_prepares_existing_sqlite_bundle_without_external_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_provider_module, "SQLiteAgentStateStore", _SQLiteComponent)
    monkeypatch.setattr(runtime_provider_module, "SQLiteConversationMemoryStore", _SQLiteComponent)
    monkeypatch.setattr(runtime_provider_module, "SQLiteMockApprovalSubmitter", _SQLiteComponent)
    monkeypatch.setattr(runtime_provider_module, "SQLiteCheckpointSaver", _SQLiteComponent)
    runtime = await prepare_agent_runtime_providers(
        _settings(AgentStateProviderName.SQLITE, database_path=tmp_path / "runtime.db")
    )

    assert isinstance(runtime.state_store, _SQLiteComponent)
    assert isinstance(runtime.memory_store, _SQLiteComponent)
    assert isinstance(runtime.submission_service, _SQLiteComponent)
    assert isinstance(runtime.checkpointer, _SQLiteComponent)
    assert runtime.status.to_dict() == {
        "provider": AgentStateProviderName.SQLITE,
        "prepared": True,
        "shared_state": False,
        "activation_ready": True,
        "activation_blocker": None,
        "state_backend": "sqlite",
        "memory_backend": "sqlite",
        "submission_backend": "sqlite",
        "checkpoint_backend": "sqlite",
    }
    runtime.require_activation_ready()
    await runtime.close()
    await runtime.close()
    assert runtime.is_closed is True


@pytest.mark.asyncio
async def test_prepares_one_postgres_bundle_but_blocks_activation_until_redis(
    tmp_path: Path,
) -> None:
    del tmp_path
    pools: list[_Pool] = []
    _CheckpointRuntime.instances.clear()

    def pool_factory(**kwargs):
        pool = _Pool(**kwargs)
        pools.append(pool)
        return pool

    runtime = await prepare_agent_runtime_providers(
        _settings(AgentStateProviderName.POSTGRESQL, database_path=Path("unused.db")),
        pool_factory=pool_factory,
        checkpoint_runtime_factory=_CheckpointRuntime,
    )

    assert isinstance(runtime.state_store, PostgresAgentStateStore)
    assert isinstance(runtime.memory_store, PostgresConversationMemoryStore)
    assert isinstance(runtime.submission_service, PostgresMockApprovalSubmitter)
    assert runtime.checkpointer.backend_name == "postgresql"
    assert runtime.status.prepared is True
    assert runtime.status.shared_state is True
    assert runtime.status.activation_ready is False
    assert runtime.status.activation_blocker == POSTGRES_ACTIVATION_BLOCKER
    with pytest.raises(RuntimeProviderActivationBlockedError, match=POSTGRES_ACTIVATION_BLOCKER):
        runtime.require_activation_ready()

    assert pools[0].opened is True
    assert pools[0].wait_timeout == 7.0
    assert pools[0].kwargs == {
        "conninfo": "postgresql://agent:runtime-secret@postgres/agent_runtime_test",
        "kwargs": {"autocommit": False, "prepare_threshold": 0},
        "min_size": 2,
        "max_size": 6,
        "timeout": 7.0,
        "open": False,
        "name": "agent-state",
    }
    assert _CheckpointRuntime.instances[0].kwargs == {
        "min_pool_size": 2,
        "max_pool_size": 6,
        "connect_timeout_seconds": 7.0,
    }
    await runtime.close()
    assert pools[0].closed is True
    assert _CheckpointRuntime.instances[0].closed is True


@pytest.mark.asyncio
async def test_postgres_preparation_failure_closes_pool_without_sqlite_fallback(
    tmp_path: Path,
) -> None:
    del tmp_path

    class _FailingPool(_Pool):
        async def wait(self, timeout: float = 30.0) -> None:
            del timeout
            raise TimeoutError("postgres unavailable")

    pool = _FailingPool()
    settings = _settings(
        AgentStateProviderName.POSTGRESQL,
        database_path=Path("must-not-be-created.db"),
    )

    with pytest.raises(RuntimeProviderPreparationError) as captured:
        await prepare_agent_runtime_providers(
            settings,
            pool_factory=lambda **kwargs: pool,
            checkpoint_runtime_factory=_CheckpointRuntime,
        )

    assert isinstance(captured.value.__cause__, TimeoutError)
    assert pool.closed is True
    assert settings.sqlite_database_path.exists() is False


@pytest.mark.asyncio
async def test_rejects_unknown_provider(tmp_path: Path) -> None:
    settings = _settings(AgentStateProviderName.SQLITE, database_path=tmp_path / "runtime.db")
    settings.agent_state_provider = "automatic"

    with pytest.raises(RuntimeProviderPreparationError, match="unsupported"):
        await prepare_agent_runtime_providers(settings)
