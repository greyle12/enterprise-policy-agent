from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.base import SerializerProtocol
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.persistence.postgres_schema import (
    AGENT_STATE_MIGRATION_TABLE,
    AGENT_STATE_SCHEMA,
    AGENT_STATE_SCHEMA_VERSION,
)

POSTGRES_CHECKPOINT_TABLES = frozenset(
    {
        "checkpoint_migrations",
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
    }
)
_CHECKPOINT_SETUP_LOCK_KEY = 3_804_004_234_800_004


class _CursorLike(Protocol):
    async def fetchone(self) -> Sequence[Any] | Mapping[str, Any] | None: ...

    async def fetchall(self) -> Sequence[Sequence[Any] | Mapping[str, Any]]: ...


class _ConnectionLike(Protocol):
    async def execute(
        self,
        query: str,
        params: Sequence[Any] | None = None,
    ) -> _CursorLike: ...


class _PoolLike(Protocol):
    def connection(self) -> AbstractAsyncContextManager[_ConnectionLike]: ...

    async def open(self) -> None: ...

    async def wait(self, timeout: float = 30.0) -> None: ...

    async def close(self) -> None: ...


class PostgresCheckpointError(RuntimeError):
    """Raised when the official LangGraph checkpoint backend is unavailable or drifted."""


@dataclass(frozen=True, slots=True)
class PostgresCheckpointStatus:
    schema_name: str
    supported_version: int
    current_version: int
    tables: tuple[str, ...]
    missing_tables: tuple[str, ...]

    @property
    def initialized(self) -> bool:
        return self.current_version >= 0

    @property
    def ready(self) -> bool:
        return self.current_version == self.supported_version and not self.missing_tables

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "initialized": self.initialized, "ready": self.ready}


def _row_value(row: Sequence[Any] | Mapping[str, Any], key: str) -> Any:
    return row[key] if isinstance(row, Mapping) else row[0]


async def _configure_checkpoint_connection(connection: _ConnectionLike) -> None:
    await connection.execute(f"SET search_path TO {AGENT_STATE_SCHEMA}")


def _official_saver_factory(connection: object, *, serde: SerializerProtocol) -> object:
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ImportError as exc:  # pragma: no cover - exercised by packaging verification
        raise PostgresCheckpointError(
            "langgraph-checkpoint-postgres is required for the PostgreSQL checkpointer"
        ) from exc
    return AsyncPostgresSaver(connection, serde=serde)


class PostgresCheckpointRuntime:
    """Own the official AsyncPostgresSaver pool without switching FastAPI runtime."""

    backend_name = "postgresql"
    survives_process_restart = True

    def __init__(
        self,
        dsn: str,
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 8,
        connect_timeout_seconds: float = 5.0,
        serde: SerializerProtocol | None = None,
        pool_factory: Callable[..., _PoolLike] = AsyncConnectionPool,
        saver_factory: Callable[..., object] = _official_saver_factory,
    ) -> None:
        normalized_dsn = dsn.strip()
        if not normalized_dsn:
            raise ValueError("PostgreSQL checkpoint DSN must not be blank")
        if min_pool_size < 1 or max_pool_size < min_pool_size:
            raise ValueError("PostgreSQL checkpoint pool sizes are invalid")
        if connect_timeout_seconds <= 0:
            raise ValueError("PostgreSQL checkpoint connect timeout must be positive")
        self._dsn = normalized_dsn
        self._min_pool_size = min_pool_size
        self._max_pool_size = max_pool_size
        self._connect_timeout_seconds = connect_timeout_seconds
        self._serde = serde or JsonPlusSerializer(allowed_msgpack_modules=())
        self._pool_factory = pool_factory
        self._saver_factory = saver_factory
        self._pool: _PoolLike | None = None
        self._checkpointer: object | None = None

    @property
    def is_open(self) -> bool:
        return self._pool is not None

    @property
    def checkpointer(self) -> BaseCheckpointSaver[Any]:
        if self._checkpointer is None:
            raise PostgresCheckpointError("PostgreSQL checkpoint runtime is not open")
        return self._checkpointer  # type: ignore[return-value]

    async def open(self) -> None:
        if self._pool is not None:
            return
        pool = self._pool_factory(
            conninfo=self._dsn,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
            min_size=self._min_pool_size,
            max_size=self._max_pool_size,
            timeout=self._connect_timeout_seconds,
            open=False,
            configure=_configure_checkpoint_connection,
            name="agent-checkpoint",
        )
        try:
            await pool.open()
            await pool.wait(timeout=self._connect_timeout_seconds)
            checkpointer = self._saver_factory(pool, serde=self._serde)
        except BaseException:
            await pool.close()
            raise
        setattr(checkpointer, "backend_name", self.backend_name)
        setattr(checkpointer, "survives_process_restart", self.survives_process_restart)
        self._pool = pool
        self._checkpointer = checkpointer

    async def _validate_agent_schema(self, connection: _ConnectionLike) -> None:
        schema_row = await (
            await connection.execute(
                "SELECT to_regnamespace(%s) AS schema_name", (AGENT_STATE_SCHEMA,)
            )
        ).fetchone()
        if schema_row is None or _row_value(schema_row, "schema_name") is None:
            raise PostgresCheckpointError("Phase 38 Step 2 agent_runtime schema is required")
        version_row = await (
            await connection.execute(
                f"SELECT COALESCE(MAX(version), 0) AS version FROM {AGENT_STATE_MIGRATION_TABLE}"
            )
        ).fetchone()
        version = int(_row_value(version_row, "version")) if version_row is not None else 0
        if version != AGENT_STATE_SCHEMA_VERSION:
            raise PostgresCheckpointError(
                "Agent runtime schema version does not match the application: "
                f"{version} != {AGENT_STATE_SCHEMA_VERSION}"
            )

    async def setup(self) -> PostgresCheckpointStatus:
        await self.open()
        assert self._pool is not None
        async with self._pool.connection() as connection:
            await self._validate_agent_schema(connection)
            await connection.execute("SELECT pg_advisory_lock(%s)", (_CHECKPOINT_SETUP_LOCK_KEY,))
            try:
                setup_saver = self._saver_factory(connection, serde=self._serde)
                await setup_saver.setup()  # type: ignore[attr-defined]
            finally:
                await connection.execute(
                    "SELECT pg_advisory_unlock(%s)", (_CHECKPOINT_SETUP_LOCK_KEY,)
                )
        status = await self.status()
        if not status.ready:
            raise PostgresCheckpointError("PostgreSQL checkpoint schema is incomplete or drifted")
        return status

    async def status(self) -> PostgresCheckpointStatus:
        await self.open()
        assert self._pool is not None
        supported_version = len(self.checkpointer.MIGRATIONS) - 1  # type: ignore[attr-defined]
        async with self._pool.connection() as connection:
            relation_row = await (
                await connection.execute(
                    "SELECT to_regclass(%s) AS relation_name",
                    (f"{AGENT_STATE_SCHEMA}.checkpoint_migrations",),
                )
            ).fetchone()
            if relation_row is None or _row_value(relation_row, "relation_name") is None:
                return PostgresCheckpointStatus(
                    schema_name=AGENT_STATE_SCHEMA,
                    supported_version=supported_version,
                    current_version=-1,
                    tables=(),
                    missing_tables=tuple(sorted(POSTGRES_CHECKPOINT_TABLES)),
                )
            rows = await (
                await connection.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = %s
                      AND table_name = ANY(%s)
                    ORDER BY table_name
                    """,
                    (AGENT_STATE_SCHEMA, list(POSTGRES_CHECKPOINT_TABLES)),
                )
            ).fetchall()
            tables = tuple(str(_row_value(row, "table_name")) for row in rows)
            version_row = await (
                await connection.execute(
                    f"SELECT COALESCE(MAX(v), -1) AS version "
                    f"FROM {AGENT_STATE_SCHEMA}.checkpoint_migrations"
                )
            ).fetchone()
        current_version = int(_row_value(version_row, "version")) if version_row is not None else -1
        if current_version > supported_version:
            raise PostgresCheckpointError(
                "PostgreSQL checkpoint schema is newer than the installed application"
            )
        return PostgresCheckpointStatus(
            schema_name=AGENT_STATE_SCHEMA,
            supported_version=supported_version,
            current_version=current_version,
            tables=tables,
            missing_tables=tuple(sorted(POSTGRES_CHECKPOINT_TABLES.difference(tables))),
        )

    async def close(self) -> None:
        pool = self._pool
        self._pool = None
        self._checkpointer = None
        if pool is not None:
            await pool.close()

    async def __aenter__(self) -> PostgresCheckpointRuntime:
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        await self.close()


__all__ = [
    "POSTGRES_CHECKPOINT_TABLES",
    "PostgresCheckpointError",
    "PostgresCheckpointRuntime",
    "PostgresCheckpointStatus",
]
