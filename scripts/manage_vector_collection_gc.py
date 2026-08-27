from __future__ import annotations

import argparse
import json
import os
import sys

from app.rag.collection_gc import CollectionGCError, PgVectorCollectionGCManager

_DEFAULT_DSN = "postgresql://policy_agent:local-development-only@127.0.0.1:5432/policy_agent"
_DEFAULT_RETENTION_DAYS = 7
_DEFAULT_SWEEP_GRACE_SECONDS = 3600


def _non_negative_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return parsed


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan, mark, inspect, or sweep retired pgvector collections."
    )
    parser.add_argument("--dsn", default=os.getenv("RAG_PGVECTOR_DSN", _DEFAULT_DSN))
    parser.add_argument("--connect-timeout-seconds", type=float, default=5.0)
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="Read-only dry-run; never marks or deletes.")
    plan.add_argument(
        "--retention-days",
        type=_non_negative_integer,
        default=_DEFAULT_RETENTION_DAYS,
    )

    mark = subparsers.add_parser("mark", help="Create a fenced mark for one collection.")
    mark.add_argument("--collection", required=True)
    mark.add_argument(
        "--retention-days",
        type=_non_negative_integer,
        default=_DEFAULT_RETENTION_DAYS,
    )
    mark.add_argument(
        "--sweep-grace-seconds",
        type=_positive_integer,
        default=_DEFAULT_SWEEP_GRACE_SECONDS,
    )

    status = subparsers.add_parser("status", help="Inspect one collection GC mark.")
    status.add_argument("--collection", required=True)

    sweep = subparsers.add_parser("sweep", help="Delete one ready, unchanged marked collection.")
    sweep.add_argument("--collection", required=True)
    sweep.add_argument("--mark-token", required=True)
    return parser.parse_args(argv)


def _run(args: argparse.Namespace) -> int:
    manager = PgVectorCollectionGCManager.from_dsn(
        args.dsn,
        connect_timeout_seconds=args.connect_timeout_seconds,
    )
    try:
        manager.initialize_schema()
        if args.command == "plan":
            retention_seconds = args.retention_days * 86_400
            entries = manager.plan(retention_seconds=retention_seconds)
            payload = {
                "schema_version": "1.0",
                "phase": 37,
                "action": "plan",
                "dry_run": True,
                "retention_days": args.retention_days,
                "eligible_collection_count": sum(entry.eligible for entry in entries),
                "collections": [entry.to_dict() for entry in entries],
            }
        elif args.command == "mark":
            mark = manager.mark(
                collection_name=args.collection,
                retention_seconds=args.retention_days * 86_400,
                sweep_grace_seconds=args.sweep_grace_seconds,
            )
            payload = {
                "schema_version": "1.0",
                "phase": 37,
                "action": "mark",
                "passed": True,
                "dry_run": False,
                "mark": mark.to_dict(),
            }
        elif args.command == "status":
            mark = manager.status(args.collection)
            payload = {
                "schema_version": "1.0",
                "phase": 37,
                "action": "status",
                "marked": mark is not None,
                "mark": None if mark is None else mark.to_dict(),
            }
        else:
            mark = manager.sweep(
                collection_name=args.collection,
                mark_token=args.mark_token,
            )
            payload = {
                "schema_version": "1.0",
                "phase": 37,
                "action": "sweep",
                "passed": True,
                "dry_run": False,
                "mark": mark.to_dict(),
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        manager.close()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return _run(args)
    except (CollectionGCError, RuntimeError, ValueError) as exc:
        print(f"Vector collection GC failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
