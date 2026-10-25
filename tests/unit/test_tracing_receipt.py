import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.observability.receipt import verify_trace_receipt
from scripts.verify_optional_tracing import configuration_status, run_probe


@pytest.fixture
def expected():
    return [
        {
            "trace_id": "a" * 32,
            "span_id": "b" * 16,
            "parent_span_id": None,
            "name": "agent.request",
            "start_time": "2026-10-24T00:00:00+00:00",
            "metadata": {"final_status": "ok", "latency_ms": "1.0"},
        },
        {
            "trace_id": "a" * 32,
            "span_id": "c" * 16,
            "parent_span_id": "b" * 16,
            "name": "workflow.run",
            "start_time": "2026-10-24T00:00:00+00:00",
            "metadata": {"final_status": "ok", "latency_ms": "0.5"},
        },
    ]


def trace_response(expected):
    return {
        "meta": {"cursor": None},
        "data": [
            {
                "id": row["span_id"],
                "traceId": row["trace_id"],
                "name": row["name"],
                "parentObservationId": row["parent_span_id"],
                "metadata": {**row["metadata"], **row.get("sdk_metadata", {})},
                "projectId": "synthetic-project",
                "public": False,
                "type": "SPAN",
                "startTime": "2026-10-24T00:00:00Z",
                "endTime": "2026-10-24T00:00:01Z",
                "modelParameters": None,
                "input": None,
                "output": None,
                "usageDetails": {},
                "costDetails": {},
                "level": "DEFAULT",
                "environment": "default",
            }
            for row in expected
        ],
    }


@pytest.fixture
def sdk_client():
    pytest.importorskip("langfuse")
    import httpx
    from langfuse.api.client import LangfuseAPI

    clients = []

    def make(handler):
        http = httpx.Client(transport=httpx.MockTransport(handler))
        client = LangfuseAPI(
            x_langfuse_sdk_name="synthetic-probe",
            x_langfuse_sdk_version="test",
            x_langfuse_public_key="pk-test",
            username="pk-test",
            password="sk-synthetic",
            base_url="http://127.0.0.1:1",
            httpx_client=http,
        )
        clients.append(http)
        return SimpleNamespace(api=client)

    yield make
    for client in clients:
        client.close()


def test_real_sdk_readback_retries_pending_without_sdk_retries(expected, sdk_client):
    import httpx

    calls = []

    def handler(request):
        calls.append(str(request.url))
        assert request.url.path == "/api/public/v2/observations"
        assert request.url.params["traceId"] == "a" * 32
        assert request.url.params["fields"] == "core,basic,io,metadata"
        assert "fromStartTime" in request.url.params and "toStartTime" in request.url.params
        if len(calls) == 1:
            return httpx.Response(404, json={"message": "not indexed"})
        return httpx.Response(200, json=trace_response(expected))

    result = verify_trace_receipt(sdk_client(handler), expected, attempts=2, delay_seconds=0)
    assert result["server_receipt_verified"] is True
    assert result["readback_attempts"] == len(calls) == 2
    assert "observations" not in result


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("missing", "observations_pending"),
        ("parent", "observation_mismatch"),
        ("name", "observation_mismatch"),
        ("trace", "trace_mismatch"),
        ("duplicate", "unexpected_observations"),
        ("extra", "unexpected_observations"),
        ("input", "privacy_failed"),
        ("output", "privacy_failed"),
        ("user", "privacy_failed"),
        ("session", "privacy_failed"),
        ("status_message", "privacy_failed"),
        ("public", "privacy_failed"),
        ("metadata", "privacy_failed"),
        ("sdk_key", "privacy_failed"),
        ("pagination", "pagination_unverified"),
        ("unfinished", "observations_pending"),
        ("late_metadata", "metadata_pending"),
    ],
)
def test_real_sdk_rejects_incomplete_mismatched_or_sensitive_receipt(
    expected, sdk_client, fault, reason
):
    import httpx

    body = copy.deepcopy(trace_response(expected))
    row = body["data"][1]
    if fault == "missing":
        body["data"].pop()
    elif fault == "parent":
        row["parentObservationId"] = "d" * 16
    elif fault == "name":
        row["name"] = "wrong"
    elif fault == "trace":
        row["traceId"] = "e" * 32
    elif fault == "duplicate":
        body["data"].append(row.copy())
    elif fault == "extra":
        body["data"].append({**row, "id": "d" * 16})
    elif fault in {"input", "output"}:
        row[fault] = "SENSITIVE-NEVER-PERSIST"
    elif fault == "user":
        row["userId"] = "SENSITIVE-NEVER-PERSIST"
    elif fault == "session":
        row["sessionId"] = "SENSITIVE-NEVER-PERSIST"
    elif fault == "status_message":
        row["statusMessage"] = "SENSITIVE-NEVER-PERSIST"
    elif fault == "public":
        row["public"] = True
    elif fault == "metadata":
        row["metadata"]["employee"] = "SENSITIVE-NEVER-PERSIST"
    elif fault == "sdk_key":
        row["metadata"]["scope.attributes.public_key"] = "SENSITIVE-NEVER-PERSIST"
    elif fault == "pagination":
        body["meta"]["cursor"] = "next-page"
    elif fault == "unfinished":
        row["endTime"] = None
    elif fault == "late_metadata":
        row["metadata"] = {}
    result = verify_trace_receipt(
        sdk_client(lambda request: httpx.Response(200, json=body)),
        expected,
        attempts=1,
        delay_seconds=0,
    )
    assert result["reason"] == reason
    assert result["server_receipt_verified"] is False
    assert "SENSITIVE-NEVER-PERSIST" not in json.dumps(result)


def test_server_empty_identifiers_and_exact_sdk_metadata_are_verified(expected, sdk_client):
    import httpx

    for span in expected:
        span["sdk_metadata"] = {
            "scope.name": "langfuse-sdk",
            "scope.version": "4.18.0",
            "resourceAttributes.service.name": "enterprise-policy-agent",
        }
    body = trace_response(expected)
    for row in body["data"]:
        row.update(userId="", sessionId="", statusMessage="")
    client = sdk_client(lambda request: httpx.Response(200, json=body))
    assert verify_trace_receipt(client, expected, attempts=1)["server_receipt_verified"]
    body["data"][0]["metadata"]["scope.version"] = "unexpected-version"
    assert verify_trace_receipt(client, expected, attempts=1)["reason"] == "metadata_pending"


@pytest.mark.parametrize("status", [401, 403, 500])
def test_http_errors_do_not_become_receipts_or_leak_messages(expected, sdk_client, status):
    import httpx

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"message": "SECRET-ERROR-BODY"})

    result = verify_trace_receipt(sdk_client(handler), expected, attempts=1)
    assert result["reason"] == "readback_unavailable"
    assert not result["server_receipt_verified"]
    assert len(calls) == 1
    assert "SECRET-ERROR-BODY" not in json.dumps(result)


def test_empty_or_duplicate_expectations_cannot_certify_receipt(expected):
    with pytest.raises(ValueError):
        verify_trace_receipt(None, [])
    with pytest.raises(ValueError):
        verify_trace_receipt(None, expected + expected)


@pytest.mark.parametrize(
    "endpoint,valid",
    [
        ("http://127.0.0.1:3000", True),
        ("https://cloud.langfuse.com", True),
        ("http://remote.invalid", False),
        ("https://user:secret@host.invalid", False),
        ("https://host.invalid?key=secret", False),
        ("http://[broken", False),
        ("https://host.invalid:bad", False),
    ],
)
def test_export_configuration_is_explicit_and_never_includes_values(monkeypatch, endpoint, valid):
    monkeypatch.setenv("LANGFUSE_BASE_URL", endpoint)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "PRIVATE-PUBLIC-KEY")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "PRIVATE-SECRET-KEY")
    result = configuration_status()
    assert result["ready"] == valid
    assert "PRIVATE" not in json.dumps(result)


async def test_unconfigured_export_does_not_construct_client(monkeypatch):
    for name in ("LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    result = await run_probe(export=True)
    assert result["status"] == "configuration_required"
    assert not result["export_requested"]
    assert not result["server_receipt_verified"]


async def test_offline_probe_never_uses_configured_destination_and_restores_tracing(monkeypatch):
    pytest.importorskip("langfuse")
    from app.observability import tracing

    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://do-not-contact.invalid")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "PRIVATE-PUBLIC-KEY")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "PRIVATE-SECRET-KEY")
    monkeypatch.setenv("AGENT_TRACE_MODEL_VERSION", "PRIVATE-MODEL")
    monkeypatch.setenv("AGENT_TRACING_ENABLED", "false")
    original = tracing._backend, tracing._initialized
    result = await run_probe()
    assert result["status"] == "offline_verified"
    assert not result["export_requested"] and not result["server_receipt_verified"]
    assert result["model_calls"] == 0
    assert (tracing._backend, tracing._initialized) == original
    import os

    assert os.environ["AGENT_TRACING_ENABLED"] == "false"
    assert "PRIVATE-" not in json.dumps(result)
    assert "SYNTHETIC-RECEIPT-PROBE" not in json.dumps(result)


async def test_export_probe_uses_actual_readback_contract_with_mock_service(monkeypatch):
    langfuse = pytest.importorskip("langfuse")
    import httpx
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from scripts.verify_optional_tracing import span_manifest

    real_client = langfuse.Langfuse
    exported = InMemorySpanExporter()
    calls = []
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test-" + uuid4().hex)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-synthetic")

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, json=trace_response(span_manifest(exported.get_finished_spans()))
        )

    def factory(**kwargs):
        kwargs["tracer_provider"].add_span_processor(SimpleSpanProcessor(exported))
        kwargs["span_exporter"] = InMemorySpanExporter()
        return real_client(
            **kwargs,
            httpx_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

    monkeypatch.setattr(langfuse, "Langfuse", factory)
    result = await run_probe(export=True)
    assert result["status"] == "receipt_verified"
    assert result["export_requested"] and result["server_receipt_verified"]
    assert len(calls) == 1 and result["expected_observations"] >= 8
    assert result["model_calls"] == 0


async def test_failed_export_preserves_attempt_status_and_hides_exception(monkeypatch):
    langfuse = pytest.importorskip("langfuse")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-synthetic")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-synthetic")
    stopped = []

    class FailedClient:
        def __init__(self, **kwargs):
            pass

        def start_observation(self, **kwargs):
            raise RuntimeError("PRIVATE-SDK-ERROR")

        def flush(self):
            raise RuntimeError("PRIVATE-SDK-ERROR")

        def shutdown(self):
            stopped.append(True)

    monkeypatch.setattr(langfuse, "Langfuse", FailedClient)
    result = await run_probe(export=True)
    assert result["status"] == "probe_failed"
    assert result["export_requested"] and not result["server_receipt_verified"]
    assert "PRIVATE-SDK-ERROR" not in json.dumps(result)
    assert stopped == [True]


def test_real_sdk_otlp_serialization_contains_no_business_content(monkeypatch):
    pytest.importorskip("langfuse")
    import requests
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from app.observability import tracing

    bodies = []

    class Session(requests.Session):
        def send(self, request, **kwargs):
            bodies.append(request.body)
            response = requests.Response()
            response.status_code, response._content = 200, b""
            return response

    exporter = OTLPSpanExporter(endpoint="http://127.0.0.1:1/probe", session=Session())
    provider = tracing.metadata_only_provider()
    original = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(original))
    public_key = "pk-test-" + uuid4().hex
    client = tracing.make_langfuse_client(
        public_key=public_key,
        secret_key="SECRET-KEY-SENTINEL",
        base_url="http://127.0.0.1:1",
        tracer_provider=provider,
        span_exporter=exporter,
    )
    backend = tracing.LangfuseBackend.__new__(tracing.LangfuseBackend)
    backend.client = client
    monkeypatch.setattr(tracing, "_backend", backend)
    monkeypatch.setattr(tracing, "_initialized", True)
    monkeypatch.setenv("AGENT_TRACING_ENABLED", "true")
    monkeypatch.setenv("AGENT_TRACE_MODEL_VERSION", "SECRET-MODEL-SENTINEL")

    @tracing.traced("synthetic.root")
    def business(secret):
        return secret

    try:
        assert business("SECRET-ANSWER-SENTINEL") == "SECRET-ANSWER-SENTINEL"
        # Another project's Langfuse scope must still be filtered before export.
        provider.get_tracer("langfuse-sdk", attributes={"public_key": "pk-other"}).start_span(
            "other-project"
        ).end()
        client.flush()
        assert bodies
        wire = ExportTraceServiceRequest.FromString(bodies[0])
        assert len(wire.resource_spans[0].scope_spans[0].spans) == 1
        payload = str(wire)
        assert "synthetic.root" in payload
        assert "other-project" not in payload
        own = next(span for span in original.get_finished_spans() if span.name == "synthetic.root")
        assert own.instrumentation_scope.attributes["public_key"] == public_key
        assert all(
            secret not in payload
            for secret in (
                public_key,
                "SECRET-KEY-SENTINEL",
                "SECRET-MODEL-SENTINEL",
                "SECRET-ANSWER-SENTINEL",
            )
        )
        assert "service.instance.id" not in payload
        assert "telemetry.sdk" not in payload
        assert not wire.resource_spans[0].scope_spans[0].scope.attributes
        assert "langfuse.observation.input" not in payload
        assert "langfuse.observation.output" not in payload
    finally:
        client.shutdown()
