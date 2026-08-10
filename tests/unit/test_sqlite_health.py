from __future__ import annotations

from pathlib import Path

import pytest

from app.persistence import SQLiteAgentStateStore
from app.persistence.sqlite_schema import connect_database


@pytest.mark.asyncio
async def test_sqlite_state_store_ping_accepts_expected_schema(
    tmp_path: Path,
) -> None:
    store = SQLiteAgentStateStore(tmp_path / "ready.db")

    await store.ping()


@pytest.mark.asyncio
async def test_sqlite_state_store_ping_rejects_schema_drift(
    tmp_path: Path,
) -> None:
    store = SQLiteAgentStateStore(tmp_path / "wrong-schema.db")
    connection = connect_database(store.database_path)
    try:
        connection.execute("PRAGMA user_version = 2")
    finally:
        connection.close()

    with pytest.raises(
        RuntimeError,
        match="schema version does not match",
    ):
        await store.ping()
