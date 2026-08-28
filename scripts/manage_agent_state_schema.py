from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Sequence

from app.persistence.postgres_schema import (
    AGENT_STATE_SCHEMA_VERSION,
    PostgresAgentStateSchemaManager,
    PostgresStateSchemaStatus,
)

_DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0


def _add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dsn",
        default=None,
        help="PostgreSQL DSN; defaults to AGENT_POSTGRES_DSN. Never printed.",
    )
    parser.add_argument(
        "--connect-timeout-seconds",
        type=float,
        default=_DEFAULT_CONNECT_TIMEOUT_SECONDS,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create or inspect the versioned PostgreSQL Agent runtime state schema."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    setup = subparsers.add_parser("setup", help="Apply pending schema migrations transactionally.")
    status = subparsers.add_parser("status", help="Inspect schema version, tables, and columns.")
    _add_connection_arguments(setup)
    _add_connection_arguments(status)
    return parser


def _resolve_dsn(explicit_dsn: str | None) -> str:
    value = explicit_dsn if explicit_dsn is not None else os.getenv("AGENT_POSTGRES_DSN")
    normalized = value.strip() if value is not None else ""
    if not normalized:
        raise ValueError("AGENT_POSTGRES_DSN is required when --dsn is not provided")
    return normalized


def _payload(action: str, status: PostgresStateSchemaStatus) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "phase": 38,
        "step": 2,
        "action": action,
        "passed": status.ready,
        **status.to_dict(),
        "runtime_backend_switched": False,
        "sqlite_data_migrated": False,
    }


def _run(
    args: argparse.Namespace,
    *,
    manager_factory: Callable[..., PostgresAgentStateSchemaManager] | None = None,
) -> dict[str, object]:
    dsn = _resolve_dsn(args.dsn)
    factory = manager_factory or PostgresAgentStateSchemaManager.from_dsn
    manager = factory(
        dsn,
        connect_timeout_seconds=args.connect_timeout_seconds,
    )
    status = manager.setup() if args.command == "setup" else manager.status()
    return _payload(args.command, status)


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    dsn = ""
    try:
        dsn = _resolve_dsn(args.dsn)
        result = _run(args)
    except Exception as exc:
        safe_message = str(exc).replace(dsn, "<redacted-dsn>") if dsn else str(exc)
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "phase": 38,
                    "step": 2,
                    "action": args.command,
                    "passed": False,
                    "supported_version": AGENT_STATE_SCHEMA_VERSION,
                    "error_type": type(exc).__name__,
                    "error": safe_message,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
