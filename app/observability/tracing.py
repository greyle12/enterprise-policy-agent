"""Opt-in, metadata-only tracing. No arguments, results or exceptions are exported."""

from __future__ import annotations

import atexit
import hashlib
import inspect
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from time import perf_counter
from uuid import uuid4

_parent = ContextVar("policy_trace_parent", default=None)
_backend = None
_initialized = False
_lock = threading.Lock()
_VERSION_KEYS = (
    "dataset_version",
    "model_version",
    "prompt_version",
    "corpus_fingerprint",
    "git_commit",
)


def metadata_only_provider():
    """Use a private provider without ambient process/resource identifiers."""
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    return TracerProvider(resource=Resource({"service.name": "enterprise-policy-agent"}))


def make_langfuse_client(
    *, public_key, secret_key, base_url, tracer_provider=None, span_exporter=None, **kwargs
):
    """Remove SDK credentials and ambient attributes after project-isolation filtering."""
    import base64

    from langfuse import Langfuse
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.trace.export import SpanExporter
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope

    delegate = (
        span_exporter
        if span_exporter is not None
        else OTLPSpanExporter(
            endpoint=base_url.rstrip("/") + "/api/public/otel/v1/traces",
            headers={
                "Authorization": "Basic "
                + base64.b64encode(f"{public_key}:{secret_key}".encode()).decode("ascii")
            },
            timeout=2,
        )
    )

    class MetadataExporter(SpanExporter):
        def export(self, spans):
            clean = []
            for span in spans:
                scope = span.instrumentation_scope
                clean.append(
                    ReadableSpan(
                        name=span.name,
                        context=span.context,
                        parent=span.parent,
                        resource=span.resource,
                        attributes={
                            key: value
                            for key, value in span.attributes.items()
                            if key
                            in {
                                "langfuse.observation.type",
                                "langfuse.internal.as_root",
                                "langfuse.internal.is_app_root",
                            }
                            or key.startswith("langfuse.observation.metadata.")
                        },
                        events=span.events,
                        links=span.links,
                        kind=span.kind,
                        status=span.status,
                        start_time=span.start_time,
                        end_time=span.end_time,
                        instrumentation_scope=InstrumentationScope(
                            scope.name, scope.version, scope.schema_url, attributes={}
                        ),
                    )
                )
            return delegate.export(clean)

        def shutdown(self):
            delegate.shutdown()

        def force_flush(self, timeout_millis=30000):
            return delegate.force_flush(timeout_millis=timeout_millis)

    return Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        base_url=base_url,
        tracer_provider=tracer_provider or metadata_only_provider(),
        span_exporter=MetadataExporter(),
        **kwargs,
    )


class LangfuseBackend:
    """Private OTel provider prevents unrelated automatic spans from being exported."""

    def __init__(self):
        self.client = make_langfuse_client(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            base_url=os.environ["LANGFUSE_BASE_URL"],
            tracer_provider=metadata_only_provider(),
            timeout=2,
            debug=False,
        )

    def start(self, name, parent, metadata):
        return self.client.start_observation(name=name, trace_context=parent, metadata=metadata)

    def finish(self, span, metadata):
        try:
            span.update(metadata=metadata)
        finally:
            span.end()

    def shutdown(self):
        self.client.shutdown()


def get_backend():
    global _backend, _initialized
    if os.getenv("AGENT_TRACING_ENABLED", "false").lower() != "true":
        return None
    with _lock:
        if not _initialized:
            _initialized = True
            try:
                _backend = LangfuseBackend()
                atexit.register(shutdown_tracing)
            except Exception:
                # Neither SDK errors nor credential values belong in business logs.
                _backend = None
    return _backend


def shutdown_tracing():
    if _backend is not None:
        try:
            _backend.shutdown()
        except Exception:
            pass


@contextmanager
def trace_span(name):
    """Correlate sync/async children without modifying ambient OTel context.

    Names are developer constants. Only digests of configured versions leave the
    process; session IDs, input, output, exception messages and state never do.
    """
    backend = get_backend()
    if backend is None:
        yield
        return
    parent = _parent.get()
    metadata = {
        key: hashlib.sha256(os.environ[f"AGENT_TRACE_{key.upper()}"].encode()).hexdigest()
        for key in _VERSION_KEYS
        if os.environ.get(f"AGENT_TRACE_{key.upper()}")
    }
    started = perf_counter()
    span = None
    token = None
    status = "ok"
    try:
        try:
            span = backend.start(name, parent or {"trace_id": uuid4().hex}, metadata)
            token = _parent.set({"trace_id": span.trace_id, "parent_span_id": span.id})
        except Exception:
            pass
        try:
            yield
        except BaseException:
            status = "error_or_interrupt"
            raise
    finally:
        if token is not None:
            _parent.reset(token)
        if span is not None:
            try:
                backend.finish(
                    span,
                    {
                        **metadata,
                        "final_status": status,
                        "latency_ms": round((perf_counter() - started) * 1000, 3),
                    },
                )
            except Exception:
                pass


def traced(name):
    """Wrap an existing boundary without inspecting its arguments or return value."""

    def decorate(function):
        if inspect.iscoroutinefunction(function):

            @wraps(function)
            async def asynchronous(*args, **kwargs):
                with trace_span(name):
                    return await function(*args, **kwargs)

            return asynchronous

        @wraps(function)
        def synchronous(*args, **kwargs):
            with trace_span(name):
                return function(*args, **kwargs)

        return synchronous

    return decorate
