from __future__ import annotations

import argparse
import json
import os
import sys

from app.rag.indexing_lease import IndexingLeaseError, PgVectorIndexingLeaseManager

_DEFAULT_DSN = "postgresql://policy_agent:local-development-only@127.0.0.1:5432/policy_agent"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect a pgvector collection indexing lease.")
    parser.add_argument("--dsn", default=os.getenv("RAG_PGVECTOR_DSN", _DEFAULT_DSN))
    parser.add_argument("--connect-timeout-seconds", type=float, default=5.0)
    parser.add_argument("command", choices=("status",))
    parser.add_argument("--collection", required=True)
    return parser.parse_args(argv)


def _run(args: argparse.Namespace) -> int:
    manager = PgVectorIndexingLeaseManager.from_dsn(
        args.dsn,
        connect_timeout_seconds=args.connect_timeout_seconds,
    )
    try:
        manager.initialize_schema()
        status = manager.status(args.collection)
        payload = {
            "schema_version": "1.0",
            "phase": 36,
            "collection_name": args.collection,
            "lease": (
                None
                if status is None
                else {
                    "owner_id": status.owner_id,
                    "fencing_token": status.fencing_token,
                    "expires_at": None if status.expires_at is None else str(status.expires_at),
                    "active": status.active,
                }
            ),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        manager.close()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return _run(args)
    except (IndexingLeaseError, RuntimeError, ValueError) as exc:
        print(f"Indexing lease operation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
