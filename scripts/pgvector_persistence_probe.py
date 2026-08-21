from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Sequence

from app.rag.pgvector_index import PgVectorIndex
from app.rag.vector_index import VectorRecord

_DIMENSION = 512


def _collection_name(probe_id: str) -> str:
    digest = hashlib.sha256(probe_id.encode("utf-8")).hexdigest()[:16]
    return f"phase29-probe-{digest}"


def _open_index(probe_id: str) -> PgVectorIndex:
    dsn = os.environ.get("RAG_PGVECTOR_DSN", "").strip()
    if not dsn:
        raise RuntimeError("RAG_PGVECTOR_DSN is required")
    index = PgVectorIndex.from_dsn(
        dsn,
        dimension=_DIMENSION,
        collection_name=_collection_name(probe_id),
        min_pool_size=1,
        max_pool_size=2,
        connect_timeout_seconds=5.0,
    )
    try:
        index.initialize_schema()
    except BaseException:
        index.close()
        raise
    return index


def run(operation: str, probe_id: str) -> dict[str, object]:
    index = _open_index(probe_id)
    try:
        if operation == "write":
            index.upsert(
                [
                    VectorRecord(
                        record_id=probe_id,
                        text="Phase 29 pgvector persistence probe",
                        vector=[1.0, *([0.0] * (_DIMENSION - 1))],
                        metadata={"probe": "true"},
                    )
                ]
            )
            return {"operation": operation, "written": index.size == 1}
        if operation == "read":
            results = index.search(
                [1.0, *([0.0] * (_DIMENSION - 1))],
                top_k=1,
                allowed_record_ids={probe_id},
            )
            persisted = bool(results and results[0].record.record_id == probe_id)
            return {"operation": operation, "persisted": persisted}
        if operation == "delete":
            index.delete_collection()
            return {"operation": operation, "deleted": index.size == 0}
        raise ValueError(f"unsupported operation: {operation}")
    finally:
        index.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe pgvector volume persistence.")
    parser.add_argument("operation", choices=("write", "read", "delete"))
    parser.add_argument("--probe-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = run(args.operation, args.probe_id)
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "passed": False,
                    "operation": args.operation,
                    "error_type": type(exc).__name__,
                    "message": "pgvector persistence probe failed",
                },
                ensure_ascii=False,
            )
        )
        return 1
    passed = bool(result.get("written") or result.get("persisted") or result.get("deleted"))
    print(json.dumps({"passed": passed, **result}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
