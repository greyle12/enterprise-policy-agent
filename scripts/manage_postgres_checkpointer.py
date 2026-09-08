from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence

from app.persistence.postgres_checkpointer import PostgresCheckpointRuntime


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Set up or inspect the official LangGraph PostgreSQL checkpoint schema."
    )
    parser.add_argument("action", choices=("setup", "status"))
    parser.add_argument(
        "--dsn", default=None, help="Defaults to AGENT_POSTGRES_DSN; never printed."
    )
    parser.add_argument("--min-pool-size", type=int, default=1)
    parser.add_argument("--max-pool-size", type=int, default=4)
    parser.add_argument("--connect-timeout-seconds", type=float, default=5.0)
    return parser


def _resolve_dsn(explicit_dsn: str | None) -> str:
    value = explicit_dsn if explicit_dsn is not None else os.getenv("AGENT_POSTGRES_DSN")
    normalized = value.strip() if value is not None else ""
    if not normalized:
        raise ValueError("AGENT_POSTGRES_DSN is required when --dsn is not provided")
    return normalized


async def _run(args: argparse.Namespace) -> dict[str, object]:
    dsn = _resolve_dsn(args.dsn)
    runtime = PostgresCheckpointRuntime(
        dsn,
        min_pool_size=args.min_pool_size,
        max_pool_size=args.max_pool_size,
        connect_timeout_seconds=args.connect_timeout_seconds,
    )
    try:
        status = await runtime.setup() if args.action == "setup" else await runtime.status()
        return {
            "schema_version": "1.0",
            "phase": 38,
            "step": 4,
            "action": args.action,
            "passed": status.ready,
            **status.to_dict(),
            "official_saver": "AsyncPostgresSaver",
            "runtime_backend_switched": False,
            "sqlite_data_migrated": False,
        }
    finally:
        await runtime.close()


def _run_with_compatible_loop(args: argparse.Namespace) -> dict[str, object]:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(_run(args))


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    dsn = ""
    try:
        dsn = _resolve_dsn(args.dsn)
        result = _run_with_compatible_loop(args)
    except Exception as exc:
        safe_message = str(exc).replace(dsn, "<redacted-dsn>") if dsn else str(exc)
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "phase": 38,
                    "step": 4,
                    "action": args.action,
                    "passed": False,
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
