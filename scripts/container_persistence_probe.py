from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from collections.abc import Sequence
from pathlib import Path

from app.agent.workflow_models import AgentSessionInfo, AgentSessionPhase
from app.persistence import SQLiteAgentStateStore

_DEFAULT_DATABASE_PATH = Path(
    "/app/data/runtime/enterprise_policy_agent.db"
)
_DEFAULT_PROBE_ID = "DAY17-CONTAINER-PROBE"
_PROBE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


def _database_path() -> Path:
    return Path(
        os.environ.get(
            "SQLITE_DATABASE_PATH",
            str(_DEFAULT_DATABASE_PATH),
        )
    )


def _validate_probe_id(probe_id: str) -> str:
    normalized = probe_id.strip()
    if not _PROBE_ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            "probe_id must be 1-64 safe session identifier characters"
        )
    return normalized


async def write_probe(
    database_path: Path,
    probe_id: str,
) -> dict[str, object]:
    store = SQLiteAgentStateStore(database_path)
    await store.save_route_state(
        AgentSessionInfo(
            session_id=probe_id,
            turn_number=17,
            phase=AgentSessionPhase.IDLE,
            active_draft_id=None,
            draft_revision=None,
            pending_confirmation=False,
            checkpoint_backend="sqlite",
            survives_process_restart=True,
        ),
        None,
    )
    return {
        "operation": "write",
        "probe_id": probe_id,
        "persisted": True,
    }


async def read_probe(
    database_path: Path,
    probe_id: str,
) -> dict[str, object]:
    store = SQLiteAgentStateStore(database_path)
    session = await store.get_session(probe_id)
    persisted = (
        session is not None
        and session.turn_number == 17
        and session.checkpoint_backend == "sqlite"
    )
    if not persisted:
        raise RuntimeError(
            "persistence probe was not found after container recreation"
        )
    return {
        "operation": "read",
        "probe_id": probe_id,
        "persisted": True,
    }


async def delete_probe(
    database_path: Path,
    probe_id: str,
) -> dict[str, object]:
    store = SQLiteAgentStateStore(database_path)
    await store.delete_session(probe_id)
    return {
        "operation": "delete",
        "probe_id": probe_id,
        "deleted": True,
    }


async def execute_probe(
    operation: str,
    *,
    database_path: Path,
    probe_id: str,
) -> dict[str, object]:
    if operation == "write":
        return await write_probe(database_path, probe_id)
    if operation == "read":
        return await read_probe(database_path, probe_id)
    if operation == "delete":
        return await delete_probe(database_path, probe_id)
    raise ValueError(f"unsupported probe operation: {operation}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify SQLite data across a Docker container recreation.",
    )
    parser.add_argument(
        "operation",
        choices=("write", "read", "delete"),
    )
    parser.add_argument(
        "--probe-id",
        default=_DEFAULT_PROBE_ID,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        probe_id = _validate_probe_id(args.probe_id)
        result = asyncio.run(
            execute_probe(
                args.operation,
                database_path=_database_path(),
                probe_id=probe_id,
            )
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(
            json.dumps(
                {
                    "passed": False,
                    "error": str(error),
                },
                ensure_ascii=False,
            )
        )
        return 1

    print(
        json.dumps(
            {
                "passed": True,
                **result,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
