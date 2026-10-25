import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.observability import tracing


class Recorder:
    def __init__(self, failure=None):
        self.events = []
        self.failure = failure

    def start(self, name, parent, metadata):
        if self.failure == "start":
            raise RuntimeError("secret transport error")
        span = SimpleNamespace(trace_id=parent["trace_id"], id=uuid4().hex[:16])
        self.events.append({"name": name, "parent": parent, "id": span.id, **metadata})
        return span

    def finish(self, span, metadata):
        if self.failure == "finish":
            raise RuntimeError("secret transport error")
        next(e for e in self.events if e["id"] == span.id).update(metadata)


@pytest.fixture
def recorder(monkeypatch):
    backend = Recorder()
    monkeypatch.setenv("AGENT_TRACING_ENABLED", "true")
    monkeypatch.setattr(tracing, "_initialized", True)
    monkeypatch.setattr(tracing, "_backend", backend)
    return backend


def test_disabled_does_not_construct_sdk_or_connect(monkeypatch):
    monkeypatch.delenv("AGENT_TRACING_ENABLED", raising=False)
    monkeypatch.setattr(tracing, "_initialized", False)

    def forbidden():
        pytest.fail("disabled tracing constructed SDK")

    monkeypatch.setattr(tracing, "LangfuseBackend", forbidden)
    with tracing.trace_span("test"):
        assert tracing._parent.get() is None


def test_configuration_failure_is_safe(monkeypatch):
    monkeypatch.setenv("AGENT_TRACING_ENABLED", "true")
    monkeypatch.setattr(tracing, "_initialized", False)
    monkeypatch.setattr(tracing, "_backend", None)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    with tracing.trace_span("test"):
        assert tracing._parent.get() is None


@pytest.mark.parametrize("failure", ["start", "finish"])
async def test_export_failure_preserves_result_and_exception(recorder, failure):
    recorder.failure = failure

    @tracing.traced("test")
    async def business(fail=False):
        if fail:
            raise ValueError("private business error")
        return "unchanged"

    assert await business() == "unchanged"
    with pytest.raises(ValueError, match="private business error"):
        await business(True)
    assert tracing._parent.get() is None


async def test_concurrent_context_and_sensitive_payload(recorder, monkeypatch):
    monkeypatch.setenv("AGENT_TRACE_MODEL_VERSION", "private-model-credential")

    @tracing.traced("child")
    async def child(secret):
        await asyncio.sleep(0)
        return secret

    @tracing.traced("root")
    async def root(secret):
        return await child(secret)

    assert await asyncio.gather(root("employee-password"), root("private-policy")) == [
        "employee-password",
        "private-policy",
    ]
    roots = [e for e in recorder.events if e["name"] == "root"]
    children = [e for e in recorder.events if e["name"] == "child"]
    assert len({e["parent"]["trace_id"] for e in roots}) == 2
    for child_event in children:
        root_event = next(e for e in roots if e["id"] == child_event["parent"]["parent_span_id"])
        assert child_event["parent"]["trace_id"] == root_event["parent"]["trace_id"]
    payload = json.dumps(recorder.events)
    for secret in ["employee-password", "private-policy", "private-model-credential"]:
        assert secret not in payload
    assert tracing._parent.get() is None


async def test_cancellation_restores_context(recorder):
    @tracing.traced("cancelled")
    async def business():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await business()
    assert tracing._parent.get() is None
    assert recorder.events[0]["final_status"] == "error_or_interrupt"


async def test_real_langgraph_trace_without_submission(recorder):
    from app.evaluation.runtime import OfflineIntentClassifier, build_evaluation_runtime

    runtime = build_evaluation_runtime(
        policy_directory="data/policies", intent_classifier=OfflineIntentClassifier()
    )
    result = await runtime.router.route("采购需要哪些材料", session_id="PRIVATE-EMPLOYEE")
    names = {e["name"] for e in recorder.events}
    assert {
        "agent.request",
        "workflow.run",
        "workflow.classify_intent",
        "workflow.check_materials",
        "tool.execute",
    } <= names
    assert "workflow.submit_approval" not in names
    assert len({e["parent"]["trace_id"] for e in recorder.events}) == 1
    assert result is not None
    assert "PRIVATE-EMPLOYEE" not in json.dumps(recorder.events)


@pytest.mark.parametrize("failure", [None, "start", "finish"])
async def test_traced_confirmation_and_idempotency_contract(recorder, failure):
    # Reuse the existing deterministic workflow contract, including side-effect assertions.
    from tests.unit.test_agent_submission import (
        test_repeated_submission_is_idempotent,
        test_submit_before_confirmation_is_rejected_and_reinterrupts,
    )

    recorder.failure = failure
    await test_submit_before_confirmation_is_rejected_and_reinterrupts()
    await test_repeated_submission_is_idempotent()
    assert tracing._parent.get() is None


def test_real_sdk_otel_export_and_ambient_context_isolation(recorder, monkeypatch):
    langfuse = pytest.importorskip("langfuse")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    client = langfuse.Langfuse(
        public_key="pk-test-" + uuid4().hex,
        secret_key="sk-test-only",
        base_url="http://127.0.0.1:1",
        tracer_provider=provider,
        span_exporter=exporter,
    )
    backend = tracing.LangfuseBackend.__new__(tracing.LangfuseBackend)
    backend.client = client
    monkeypatch.setattr(tracing, "_backend", backend)
    with tracing.trace_span("root"):
        with tracing.trace_span("child"):
            pass
    client.flush()
    spans = exporter.get_finished_spans()
    assert len(spans) == 2
    child, root = spans
    assert child.parent.span_id == root.context.span_id
    assert child.context.trace_id == root.context.trace_id
    assert "sk-test-only" not in str([dict(s.attributes) for s in spans])
    assert all("langfuse.observation.input" not in s.attributes for s in spans)
    client.shutdown()


def test_metadata_export_removes_sdk_ambient_release_and_environment(recorder, monkeypatch):
    pytest.importorskip("langfuse")
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    monkeypatch.setenv("GITHUB_SHA", "PRIVATE-CI-REVISION")
    monkeypatch.setenv("LANGFUSE_TRACING_ENVIRONMENT", "private-deployment")
    exporter = InMemorySpanExporter()
    client = tracing.make_langfuse_client(
        public_key="pk-test-" + uuid4().hex,
        secret_key="sk-test-only",
        base_url="http://127.0.0.1:1",
        span_exporter=exporter,
    )
    backend = tracing.LangfuseBackend.__new__(tracing.LangfuseBackend)
    backend.client = client
    monkeypatch.setattr(tracing, "_backend", backend)
    try:
        with tracing.trace_span("agent.request"):
            pass
        client.flush()
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].attributes["langfuse.observation.metadata.final_status"] == "ok"
        assert "langfuse.release" not in spans[0].attributes
        assert "langfuse.environment" not in spans[0].attributes
        assert not spans[0].instrumentation_scope.attributes
        payload = str(spans[0].attributes)
        assert "PRIVATE-CI-REVISION" not in payload and "private-deployment" not in payload
    finally:
        client.shutdown()
