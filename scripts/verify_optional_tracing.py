"""Synthetic LangGraph tracing probe. Default: in-memory only. --export opts in."""

import argparse
import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from app.evaluation.runtime import OfflineIntentClassifier, build_evaluation_runtime
from app.observability import tracing
from app.observability.receipt import verify_trace_receipt

CONFIG_KEYS = ("LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")


def configuration_status():
    """Check process env only; do not load application credentials or reveal values."""
    missing = [key for key in CONFIG_KEYS if not os.environ.get(key, "").strip()]
    try:
        endpoint = urlsplit(os.environ.get("LANGFUSE_BASE_URL", ""))
        port = endpoint.port
    except ValueError:
        return {"missing": missing, "endpoint_valid": False, "ready": False}
    valid = (
        (
            endpoint.scheme == "https"
            or endpoint.scheme == "http"
            and endpoint.hostname in {"localhost", "127.0.0.1", "::1"}
        )
        and bool(endpoint.hostname)
        and (port is None or port > 0)
        and not (endpoint.username or endpoint.password or endpoint.query or endpoint.fragment)
    )
    return {"missing": missing, "endpoint_valid": bool(valid), "ready": not missing and bool(valid)}


def span_manifest(spans):
    rows = []
    prefix = "langfuse.observation.metadata."
    for span in spans:
        attributes = dict(span.attributes)
        if any(
            key
            not in {
                "langfuse.observation.type",
                "langfuse.internal.as_root",
                "langfuse.internal.is_app_root",
            }
            and not key.startswith(prefix)
            for key in attributes
        ):
            raise ValueError("unexpected exported attribute")
        rows.append(
            {
                "name": span.name,
                "trace_id": format(span.context.trace_id, "032x"),
                "span_id": format(span.context.span_id, "016x"),
                "parent_span_id": format(span.parent.span_id, "016x") if span.parent else None,
                "start_time": datetime.fromtimestamp(span.start_time / 1e9, UTC).isoformat(),
                "metadata": {
                    key[len(prefix) :]: str(value)
                    for key, value in attributes.items()
                    if key.startswith(prefix)
                },
                "sdk_metadata": {
                    "scope.name": span.instrumentation_scope.name,
                    "scope.version": span.instrumentation_scope.version,
                    "resourceAttributes.service.name": span.resource.attributes["service.name"],
                }
                if hasattr(span, "instrumentation_scope")
                else {},
            }
        )
    if not rows or "agent.request" not in {row["name"] for row in rows}:
        raise ValueError("application trace missing")
    return rows


async def run_probe(*, export=False):
    config = configuration_status()
    report = {
        "mode": "server_readback" if export else "offline_in_memory",
        "configuration": config,
        "model_calls": 0,
        "export_requested": False,
        "server_receipt_verified": False,
    }
    if export and not config["ready"]:
        return {**report, "status": "configuration_required"}
    try:
        __import__("langfuse")
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    except ImportError:
        return {**report, "status": "optional_sdk_required"}
    exporter = InMemorySpanExporter()
    provider = tracing.metadata_only_provider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    kwargs = {
        "public_key": "pk-probe-" + uuid4().hex,
        "secret_key": "sk-synthetic",
        "base_url": "http://127.0.0.1:1",
        "span_exporter": InMemorySpanExporter(),
    }
    if export:
        kwargs = {
            "public_key": os.environ["LANGFUSE_PUBLIC_KEY"],
            "secret_key": os.environ["LANGFUSE_SECRET_KEY"],
            "base_url": os.environ["LANGFUSE_BASE_URL"],
        }
    saved = tracing._backend, tracing._initialized, os.environ.get("AGENT_TRACING_ENABLED")
    backend = None
    try:
        backend = tracing.LangfuseBackend.__new__(tracing.LangfuseBackend)
        backend.client = tracing.make_langfuse_client(
            **kwargs, tracer_provider=provider, timeout=2, debug=False
        )
        report["export_requested"] = export
        tracing._backend, tracing._initialized = backend, True
        os.environ["AGENT_TRACING_ENABLED"] = "true"
        runtime = build_evaluation_runtime(
            policy_directory=Path(__file__).resolve().parents[1] / "data/policies",
            intent_classifier=OfflineIntentClassifier(),
        )
        await runtime.router.route("采购需要哪些材料", session_id="SYNTHETIC-RECEIPT-PROBE")
        backend.client.flush()
        spans = span_manifest(exporter.get_finished_spans())
        report.update(
            {
                "sdk_version": version("langfuse"),
                "spans": spans,
                "local_trace_verified": True,
                "export_requested": export,
            }
        )
        report["status"] = "offline_verified"
        if export:
            report.update(verify_trace_receipt(backend.client, spans))
        return report
    except Exception:
        return {**report, "status": "probe_failed"}
    finally:
        if backend is not None and hasattr(backend, "client"):
            try:
                backend.client.shutdown()
            except Exception:
                pass
        tracing._backend, tracing._initialized, enabled = saved
        if enabled is None:
            os.environ.pop("AGENT_TRACING_ENABLED", None)
        else:
            os.environ["AGENT_TRACING_ENABLED"] = enabled


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    report = asyncio.run(run_probe(export=args.export))
    path = args.output_dir / "receipt-report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {str(path.name): hashlib.sha256(path.read_bytes()).hexdigest()}
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in report.items() if key != "spans"}, indent=2))
    return 0 if report["status"] in {"offline_verified", "receipt_verified"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
