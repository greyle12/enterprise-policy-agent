"""Run a synthetic, non-submitting LangGraph request; export only with --export."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from app.evaluation.runtime import OfflineIntentClassifier, build_evaluation_runtime
from app.observability import tracing


async def run(export=False):
    exporter = None
    if not export:
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        exporter = InMemorySpanExporter()
        backend = tracing.LangfuseBackend.__new__(tracing.LangfuseBackend)
        backend.client = tracing.make_langfuse_client(
            public_key="pk-demo-" + uuid4().hex,
            secret_key="sk-synthetic-demo",
            base_url="http://127.0.0.1:1",
            tracer_provider=tracing.metadata_only_provider(),
            span_exporter=exporter,
        )
        tracing._backend = backend
        tracing._initialized = True
    os.environ["AGENT_TRACING_ENABLED"] = "true"
    backend = tracing.get_backend()
    if backend is None:
        raise RuntimeError("Tracing unavailable; check optional SDK and configuration")
    try:
        runtime = build_evaluation_runtime(
            policy_directory=Path(__file__).resolve().parents[1] / "data/policies",
            intent_classifier=OfflineIntentClassifier(),
        )
        await runtime.router.route("采购需要哪些材料")
        backend.client.flush()
        if exporter is not None:
            return [
                {
                    "name": span.name,
                    "trace_id": format(span.context.trace_id, "032x"),
                    "span_id": format(span.context.span_id, "016x"),
                    "parent_span_id": format(span.parent.span_id, "016x") if span.parent else None,
                    "attributes": dict(span.attributes),
                }
                for span in exporter.get_finished_spans()
            ]
        return {"export_requested": True, "server_receipt_verified": False}
    finally:
        tracing.shutdown_tracing()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.export)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
