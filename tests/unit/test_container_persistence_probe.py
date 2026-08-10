from __future__ import annotations

from pathlib import Path

import pytest

from scripts.container_persistence_probe import (
    _validate_probe_id,
    delete_probe,
    read_probe,
    write_probe,
)


@pytest.mark.asyncio
async def test_container_probe_round_trip_uses_sqlite_session_store(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "container-volume.db"
    probe_id = "DAY17-PROBE-ROUND-TRIP"

    write_result = await write_probe(database_path, probe_id)
    read_result = await read_probe(database_path, probe_id)
    delete_result = await delete_probe(database_path, probe_id)

    assert write_result["persisted"] is True
    assert read_result["persisted"] is True
    assert delete_result["deleted"] is True

    with pytest.raises(RuntimeError, match="not found"):
        await read_probe(database_path, probe_id)


@pytest.mark.parametrize(
    "probe_id",
    [
        "",
        "contains spaces",
        "x" * 65,
        "unsafe/path",
    ],
)
def test_container_probe_rejects_unsafe_session_ids(
    probe_id: str,
) -> None:
    with pytest.raises(ValueError, match="probe_id"):
        _validate_probe_id(probe_id)
