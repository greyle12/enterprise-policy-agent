from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from app.persistence.postgres_checkpointer import (
    POSTGRES_CHECKPOINT_TABLES,
    PostgresCheckpointError,
    PostgresCheckpointRuntime,
)


class _Cursor:
    def __init__(self, *, one=None, rows=()) -> None:
        self._one = one
        self._rows = tuple(rows)

    async def fetchone(self):
        return self._one

    async def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self, *, current_version: int = 2, relation_exists: bool = True) -> None:
        self.current_version = current_version
        self.relation_exists = relation_exists
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, params=None):
        normalized = " ".join(query.split())
        values = tuple(params or ())
        self.executed.append((normalized, values))
        if "to_regnamespace" in normalized:
            return _Cursor(one={"schema_name": "agent_runtime"})
        if "MAX(version)" in normalized:
            return _Cursor(one={"version": 1})
        if "pg_advisory_" in normalized:
            return _Cursor(one=(True,))
        if "to_regclass" in normalized:
            relation = "agent_runtime.checkpoint_migrations" if self.relation_exists else None
            return _Cursor(one={"relation_name": relation})
        if "information_schema.tables" in normalized:
            return _Cursor(
                rows=tuple({"table_name": table} for table in sorted(POSTGRES_CHECKPOINT_TABLES))
            )
        if "MAX(v)" in normalized:
            return _Cursor(one={"version": self.current_version})
        if normalized.startswith("SET search_path"):
            return _Cursor()
        raise AssertionError(f"unexpected SQL: {normalized}")


class _Pool:
    def __init__(self, connection: _Connection, **kwargs) -> None:
        self.connection_instance = connection
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
    async def connection(self) -> AsyncIterator[_Connection]:
        yield self.connection_instance


class _Saver:
    MIGRATIONS = ("m0", "m1", "m2")

    def __init__(self, connection, *, serde) -> None:
        self.connection = connection
        self.serde = serde
        self.setup_called = False

    async def setup(self) -> None:
        self.setup_called = True


@pytest.mark.asyncio
async def test_setup_owns_pool_lifecycle_and_serializes_official_migrations() -> None:
    connection = _Connection()
    pools: list[_Pool] = []
    savers: list[_Saver] = []

    def pool_factory(**kwargs):
        pool = _Pool(connection, **kwargs)
        pools.append(pool)
        return pool

    def saver_factory(value, *, serde):
        saver = _Saver(value, serde=serde)
        savers.append(saver)
        return saver

    runtime = PostgresCheckpointRuntime(
        "postgresql://agent:secret@localhost/agent_test",
        min_pool_size=2,
        max_pool_size=6,
        connect_timeout_seconds=7.0,
        serde=object(),
        pool_factory=pool_factory,
        saver_factory=saver_factory,
    )

    status = await runtime.setup()

    assert status.ready is True
    assert status.current_version == status.supported_version == 2
    assert runtime.checkpointer.backend_name == "postgresql"
    assert runtime.checkpointer.survives_process_restart is True
    assert pools[0].opened is True
    assert pools[0].wait_timeout == 7.0
    assert pools[0].kwargs["min_size"] == 2
    assert pools[0].kwargs["max_size"] == 6
    assert pools[0].kwargs["kwargs"]["autocommit"] is True
    assert pools[0].kwargs["kwargs"]["prepare_threshold"] == 0
    assert savers[1].connection is connection
    assert savers[1].setup_called is True
    sql = [query for query, _ in connection.executed]
    assert any("pg_advisory_lock" in query for query in sql)
    assert any("pg_advisory_unlock" in query for query in sql)

    await runtime.close()
    assert pools[0].closed is True
    assert runtime.is_open is False


@pytest.mark.asyncio
async def test_status_reports_uninitialized_official_schema() -> None:
    connection = _Connection(relation_exists=False)
    pool = _Pool(connection)
    runtime = PostgresCheckpointRuntime(
        "postgresql://agent:secret@localhost/agent_test",
        serde=object(),
        pool_factory=lambda **kwargs: pool,
        saver_factory=lambda value, *, serde: _Saver(value, serde=serde),
    )

    status = await runtime.status()

    assert status.initialized is False
    assert status.ready is False
    assert status.current_version == -1
    assert set(status.missing_tables) == POSTGRES_CHECKPOINT_TABLES
    await runtime.close()


@pytest.mark.asyncio
async def test_status_rejects_database_newer_than_installed_saver() -> None:
    connection = _Connection(current_version=3)
    runtime = PostgresCheckpointRuntime(
        "postgresql://agent:secret@localhost/agent_test",
        serde=object(),
        pool_factory=lambda **kwargs: _Pool(connection),
        saver_factory=lambda value, *, serde: _Saver(value, serde=serde),
    )

    with pytest.raises(PostgresCheckpointError, match="newer"):
        await runtime.status()
    await runtime.close()


@pytest.mark.parametrize(
    ("dsn", "minimum", "maximum", "timeout", "message"),
    [
        (" ", 1, 2, 5.0, "DSN"),
        ("postgresql://test", 0, 2, 5.0, "pool sizes"),
        ("postgresql://test", 3, 2, 5.0, "pool sizes"),
        ("postgresql://test", 1, 2, 0.0, "timeout"),
    ],
)
def test_rejects_invalid_configuration(dsn, minimum, maximum, timeout, message) -> None:
    with pytest.raises(ValueError, match=message):
        PostgresCheckpointRuntime(
            dsn,
            min_pool_size=minimum,
            max_pool_size=maximum,
            connect_timeout_seconds=timeout,
            serde=object(),
        )


def test_checkpointer_is_unavailable_before_open() -> None:
    runtime = PostgresCheckpointRuntime("postgresql://test", serde=object())

    with pytest.raises(PostgresCheckpointError, match="not open"):
        _ = runtime.checkpointer
