"""Local trace correlation, with synthetic malformed-span and failure fixtures."""

import json
from types import SimpleNamespace

import pytest

from app.evaluation.failure_pipeline import failure_cards
from scripts.run_traced_workflow_evaluation import TurnTraceIndex, linked_cards, run, sha


def span(name, number, parent=None, trace=1, status="ok"):
    return SimpleNamespace(
        name=name,
        context=SimpleNamespace(trace_id=trace, span_id=number),
        parent=SimpleNamespace(span_id=parent) if parent else None,
        start_time=1792790400000000000,
        attributes={
            "langfuse.observation.type": "span",
            "langfuse.observation.metadata.final_status": status,
            "langfuse.observation.metadata.latency_ms": "1.0",
        },
    )


def snapshot(turn=0, error=None):
    return [
        {
            "case_id": "WF-SYNTHETIC",
            "execution_id": "PRIVATE-LOCAL-EXECUTION",
            "turns": [{"turn": turn, "error_type": error}],
        }
    ]


def test_index_drains_each_turn_once_and_retains_local_execution_binding():
    spans = [span("child", 2, 1), span("agent.request", 1)]
    index = TurnTraceIndex(SimpleNamespace(get_finished_spans=lambda: tuple(spans)))
    index(snapshot(), [])
    index(snapshot(), [])  # completed-case callback repeats the last snapshot
    spans.extend([span("child", 4, 3, trace=2), span("agent.request", 3, trace=2)])
    index(snapshot(1), [])
    assert len(index.rows) == 2 and len(index.spans) == 4
    assert all(r["status"] == "complete" for r in index.rows)
    assert len({r["trace_id"] for r in index.rows}) == 2
    assert index.rows[0]["execution_id"] == "PRIVATE-LOCAL-EXECUTION"
    assert "PRIVATE-LOCAL-EXECUTION" not in json.dumps(index.spans)


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "parent",
        "trace",
        "duplicate_root",
        "cycle",
        "root_cycle",
        "wrong_status",
        "reused_trace",
    ],
)
def test_invalid_or_reused_trace_never_passes_correlation(fault):
    spans = [span("child", 2, 1), span("agent.request", 1)]
    index = TurnTraceIndex(SimpleNamespace(get_finished_spans=lambda: tuple(spans)))
    if fault == "reused_trace":
        index(snapshot(), [])
        spans.extend([span("child", 4, 3), span("agent.request", 3)])
        index(snapshot(1), [])
    else:
        if fault == "missing":
            spans.clear()
        elif fault == "parent":
            spans[0].parent.span_id = 99
        elif fault == "trace":
            spans[0].context.trace_id = 99
        elif fault == "duplicate_root":
            spans.append(span("agent.request", 3))
        elif fault == "cycle":
            spans[0].parent.span_id = 3
            spans.append(span("child", 3, 2))
        elif fault == "root_cycle":
            spans[1].parent = SimpleNamespace(span_id=2)
        elif fault == "wrong_status":
            spans[1].attributes["langfuse.observation.metadata.final_status"] = "error_or_interrupt"
        index(snapshot(), [])
    assert index.rows[-1]["status"] != "complete"


def test_one_invalid_export_does_not_consume_later_turns():
    spans = [span("agent.request", 1)]
    spans[0].attributes["langfuse.observation.input"] = "PRIVATE-INPUT"
    index = TurnTraceIndex(SimpleNamespace(get_finished_spans=lambda: tuple(spans)))
    index(snapshot(), [])
    spans.append(span("agent.request", 2, trace=2))
    index(snapshot(1), [])
    assert index.rows[0]["status"] == "missing_or_invalid"
    assert index.rows[1]["status"] == "complete"
    assert "PRIVATE-INPUT" not in json.dumps(index.rows + index.spans)


def test_expected_business_error_is_a_complete_error_trace():
    spans = [span("agent.request", 1, status="error_or_interrupt")]
    index = TurnTraceIndex(SimpleNamespace(get_finished_spans=lambda: tuple(spans)))
    index(snapshot(error="PromptInjectionDetected"), [])
    assert index.rows[0]["status"] == "complete"


@pytest.mark.parametrize("matching", [True, False])
def test_failure_cards_keep_source_hash_and_pending_decision(matching):
    report = {
        "results": [{"case_id": "WF-001", "execution_id": "EXEC-001", "turns": [{"turn": 0}]}],
        "workflow_results": [
            {
                "case_id": "WF-001",
                "execution_id": "EXEC-001",
                "split": "safety",
                "passed": False,
                "assertions": [
                    {
                        "name": "turn_0.authorization_violation",
                        "passed": False,
                        "expected": False,
                        "actual": True,
                    }
                ],
            }
        ],
    }
    index = [
        {
            "case_id": "WF-001",
            "execution_id": "EXEC-001" if matching else "OTHER",
            "trace_id": "a" * 32,
            "status": "complete",
            "turn": 0,
        }
    ]
    card = linked_cards(report, index, "capture-sha", "index-sha")[0]
    assert card["source_sha256"] == failure_cards(report["workflow_results"])[0]["source_sha256"]
    assert card["source_capture_sha256"] == "capture-sha"
    assert card["trace_index_sha256"] == "index-sha"
    assert card["trace_ids"] == (["a" * 32] if matching else [])
    assert card["trace_link_status"] == ("complete" if matching else "incomplete")
    assert card["reviewer_decision"] is None and card["review_status"] == "pending_review"
    report["results"][0]["turns"].append({"turn": 1})
    assert (
        linked_cards(report, index, "capture-sha", "index-sha")[0]["trace_link_status"]
        == "incomplete"
    )


async def test_full_workflow_sdk_correlation_preserves_privacy_and_business_checks(
    tmp_path, monkeypatch
):
    pytest.importorskip("langfuse")
    import httpx
    import requests
    from app.observability import tracing

    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://never-contact.invalid")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "PRIVATE-CONFIG-KEY")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "PRIVATE-CONFIG-SECRET")
    monkeypatch.setenv("AGENT_TRACE_MODEL_VERSION", "PRIVATE-VERSION")
    monkeypatch.setenv("AGENT_TRACING_ENABLED", "false")

    def forbidden(*args, **kwargs):
        pytest.fail("offline trace evaluation attempted network transport")

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(requests.Session, "send", forbidden)
    previous = tracing._backend, tracing._initialized
    output = tmp_path / "traced"
    summary = await run(output)
    assert summary["workflow_passed"] == summary["cases"] == 19
    assert summary["complete_trace_turns"] == summary["turns"] == 39
    assert summary["engineering_gate_passed"] and summary["trace_gate_passed"]
    assert summary["live_provider_calls"] == summary["semantic_judge_calls"] == 0
    assert not summary["external_publication"] and not summary["server_receipt_verified"]
    assert summary["quality_conclusion"] is None
    assert (tracing._backend, tracing._initialized) == previous
    import os

    assert os.environ["AGENT_TRACING_ENABLED"] == "false"
    report = json.loads((output / "workflow/capture-report.json").read_text("utf-8"))
    index = json.loads((output / "trace-index.json").read_text("utf-8"))
    assert index["source_capture_sha256"] == sha(output / "workflow/capture-report.json")
    spans = json.loads((output / "trace-spans.json").read_text("utf-8"))["spans"]
    payload = json.dumps(spans, ensure_ascii=False)
    assert len({r["trace_id"] for r in index["turns"]}) == 39
    for capture in report["results"]:
        assert capture["execution_id"] not in payload
        for turn in capture["turns"]:
            assert turn["query"] not in payload
            if turn["result"]:
                assert turn["result"]["reply"] not in payload
    assert all(
        secret not in payload
        for secret in ("PRIVATE-CONFIG-KEY", "PRIVATE-CONFIG-SECRET", "PRIVATE-VERSION")
    )
    assert "EVAL_PRIVATE_SENTINEL" not in payload and "合成制度要求提交申请" not in payload
    assert sum(len(r["assertions"]) for r in report["workflow_results"]) == 449
    for name, digest in json.loads((output / "delivery-manifest.json").read_text("utf-8")).items():
        assert sha(output / name) == digest
    with pytest.raises(FileExistsError):
        await run(output)


@pytest.mark.parametrize("failure", ["start", "finish"])
async def test_failed_recorder_disables_trace_gate_without_changing_workflow(
    tmp_path, monkeypatch, failure
):
    pytest.importorskip("langfuse")
    from app.observability import tracing

    def fail(*args):
        raise RuntimeError("PRIVATE-RECORDER-ERROR")

    monkeypatch.setattr(tracing.LangfuseBackend, failure, fail)
    output = tmp_path / failure
    summary = await run(output)
    assert summary["workflow_gate_passed"] and summary["workflow_passed"] == 19
    assert summary["complete_trace_turns"] == 0
    assert not summary["trace_gate_passed"] and not summary["engineering_gate_passed"]
    assert "PRIVATE-RECORDER-ERROR" not in (output / "trace-index.json").read_text("utf-8")


@pytest.mark.parametrize("failure", ["flush", "shutdown"])
async def test_sdk_lifecycle_failure_is_reported_without_losing_business_results(
    tmp_path, monkeypatch, failure
):
    langfuse = pytest.importorskip("langfuse")
    from scripts import run_agent_workflow_capture as capture

    real_client = langfuse.Langfuse
    cases = capture.load_workflow_cases(capture.DATASETS[-1])
    selected = next(case for case in cases if case.case_id == "WF-013")
    monkeypatch.setattr(capture, "DATASETS", [capture.DATASETS[-1]])
    monkeypatch.setattr(capture, "load_workflow_cases", lambda _: [selected])

    def factory(**kwargs):
        client = real_client(**kwargs)
        original = getattr(client, failure)

        def fail():
            original()
            raise RuntimeError("PRIVATE-LIFECYCLE-ERROR")

        setattr(client, failure, fail)
        return client

    monkeypatch.setattr(langfuse, "Langfuse", factory)
    output = tmp_path / failure
    summary = await run(output)
    assert summary["workflow_passed"] == 1 and summary["workflow_gate_passed"]
    assert summary["complete_trace_turns"] == 1
    assert summary["recorder_status"] == "failed" and not summary["engineering_gate_passed"]
    assert "PRIVATE-LIFECYCLE-ERROR" not in (output / "summary.json").read_text("utf-8")
