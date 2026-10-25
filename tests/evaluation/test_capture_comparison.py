"""All generated reports are synthetic structural fixtures, not execution evidence."""

import copy
import json

import pytest

from scripts import prepare_capture_comparison as prepare
from scripts.run_capture_comparison import run as compare
from scripts.run_agent_workflow_capture import DATASETS


@pytest.fixture
def packet(tmp_path):
    captures, evaluations = [], []
    for path in DATASETS:
        for case in prepare.load_workflow_cases(path):
            turns, assertions = [], []
            for index, turn in enumerate(case.turns):
                audit = [
                    {"event": "submitted", "submission_idempotency_key": "fixture"}
                ] * turn.submissions
                result = (
                    {
                        "status": turn.status,
                        "classification": {"intent": "policy_query"},
                        "reply": "fixture answer [S1]",
                    }
                    if not turn.error
                    else None
                )
                calls = []
                if case.case_id in {"WF-013", "WF-017", "WF-018"}:
                    evidence = [
                        {
                            "source_id": "S1",
                            "content": "fixture evidence",
                            "document_version": "1.0",
                        }
                    ]
                    calls = [
                        {
                            "status": "ok",
                            "messages": [
                                {
                                    "role": "user",
                                    "content": "question\n<policy_evidence_json>"
                                    + json.dumps(evidence)
                                    + "</policy_evidence_json>",
                                }
                            ],
                        }
                    ]
                turns.append(
                    {
                        "turn": index,
                        "query": turn.query,
                        "error_type": turn.error,
                        "result": result,
                        "persisted_session": {"phase": turn.phase} if not turn.error else None,
                        "audit_records": audit,
                        "persisted_draft": {"draft": {"user_confirmed": True}},
                        "persisted_submission": {"synthetic": True} if audit else None,
                        "provider_records": calls,
                    }
                )
                checks = {
                    "error": turn.error,
                    "side_effect_count": turn.submissions,
                    "duplicate_side_effect": True,
                }
                checks.update(
                    {"blocked_state_unchanged": True}
                    if turn.error
                    else {"persisted_phase": turn.phase}
                )
                if audit:
                    checks.update(confirmation_compliance=True, submission_persisted=True)
                assertions.extend(
                    {
                        "name": f"turn_{index}.{name}",
                        "expected": value,
                        "actual": value,
                        "passed": True,
                    }
                    for name, value in checks.items()
                )
            captures.append(
                {
                    "case_id": case.case_id,
                    "fixture": case.fixture,
                    "split": case.split,
                    "capture_complete": True,
                    "execution_id": case.case_id + "-fixture",
                    "turns": turns,
                }
            )
            evaluations.append(
                {
                    "case_id": case.case_id,
                    "execution_id": case.case_id + "-fixture",
                    "passed": True,
                    "assertions": assertions,
                }
            )
    report = {
        "source_or_input_drift": False,
        "model_version": "baseline-model-fixture",
        "manifest": {
            "datasets": {p.relative_to(prepare.ROOT).as_posix(): prepare.sha(p) for p in DATASETS},
            "corpus_sha256": "a" * 64,
            "source_sha256": {
                name: "b" * 64 for name in prepare.IDENTITY_FILES | prepare.EXPERIMENT_FILES
            },
        },
        "results": captures,
        "workflow_results": evaluations,
    }
    baseline, candidate = tmp_path / "baseline.json", tmp_path / "candidate.json"
    baseline.write_text(json.dumps(report), "utf-8")
    candidate.write_text(json.dumps(report), "utf-8")
    return baseline, candidate, tmp_path


def change(packet, edit):
    value = json.loads(packet[1].read_text("utf-8"))
    edit(value)
    packet[1].write_text(json.dumps(value), "utf-8")


def test_self_check_keeps_all_safety_cases_and_binds_three_answers(packet):
    summary = prepare.run(packet[0], packet[1], packet[2] / "output")
    assert summary["baseline_eligible"] == summary["candidate_eligible"] == 19
    assert summary["paired_answers"] == 3 and summary["comparison_kind"] == "same_report_self_check"
    assert summary["quality_winner"] is None and not summary["dataset_frozen"]
    rows = [
        json.loads(s)
        for s in (packet[2] / "output/pairwise-inputs.jsonl").read_text("utf-8").splitlines()
    ]
    assert all(
        r["workflow_case_id"] == r["candidate_a"]["workflow_result"]["case_id"] for r in rows
    )
    assert all("document_version" in r["evidence"]["S1"] for r in rows)


async def test_mock_blind_swap_runs_with_turn_ids_and_no_quality_winner(packet):
    summary = await compare(packet[0], packet[1], packet[2] / "output")
    assert summary["engineering_gate_passed"] and summary["mock_judge_calls"] == 6
    assert summary["pairwise_summaries"][0]["counts"]["Unknown"] == 3
    assert summary["pairwise_summaries"][0]["a_win_rate_ci95"] is None


@pytest.mark.parametrize(
    "fault", ["corpus", "source", "identity_source", "query", "duplicate", "evidence"]
)
def test_uncontrolled_experiment_rejected(packet, fault):
    def edit(r):
        if fault == "corpus":
            r["manifest"]["corpus_sha256"] = "c" * 64
        elif fault in {"source", "identity_source"}:
            name = sorted(
                prepare.EXPERIMENT_FILES if fault == "source" else prepare.IDENTITY_FILES
            )[0]
            r["manifest"]["source_sha256"][name] = "c" * 64
        elif fault == "query":
            r["results"][0]["turns"][0]["query"] = "changed question"
        elif fault == "duplicate":
            r["results"].append(copy.deepcopy(r["results"][0]))
        else:
            row = next(c for c in r["results"] if c["case_id"] == "WF-013")
            row["turns"][0]["provider_records"][0]["messages"][0]["content"] = (
                'x\n<policy_evidence_json>[{"source_id":"S1","content":"different"}]</policy_evidence_json>'
            )

    change(packet, edit)
    with pytest.raises(ValueError):
        prepare.run(packet[0], packet[1], packet[2] / "output")
    assert not (packet[2] / "output").exists()


def test_explicit_component_change_supported_but_identity_change_forbidden(packet):
    name = "app/rag/policy_answer_service.py"
    change(packet, lambda r: r["manifest"]["source_sha256"].update({name: "c" * 64}))
    result = prepare.run(packet[0], packet[1], packet[2] / "output", [name])
    assert result["changed_source_files"] == [name]
    with pytest.raises(ValueError):
        prepare.run(
            packet[0], packet[1], packet[2] / "other", ["app/evaluation/workflow_fixtures.py"]
        )


@pytest.mark.parametrize("case_id", ["WF-006", "WF-013"])
def test_snapshot_regression_detected_despite_declared_pass(packet, case_id):
    def edit(r):
        case = next(c for c in r["results"] if c["case_id"] == case_id)
        case["turns"][0]["persisted_session"] = {"phase": "submitted"}

    change(packet, edit)
    result = prepare.run(packet[0], packet[1], packet[2] / "output")
    assert result["regressions"] == [case_id]


def test_failed_assertion_and_assisted_review_source_are_not_overridden(packet):
    change(packet, lambda r: r["workflow_results"][12]["assertions"][0].update(passed=False))
    result = prepare.run(packet[0], packet[1], packet[2] / "output")
    assert result["regressions"] == ["WF-013"] and result["paired_answers"] == 2
    review = packet[2] / "review.jsonl"
    review.write_text(
        json.dumps(
            {
                "case_id": "WF-013-T0-faithfulness",
                "answer": "fixture answer [S1]",
                "source_capture_sha256": "0" * 64,
                "review_mode": "assistant_assisted_human_review",
                "assistant_recommendation": "PASS",
                "human": None,
                "reviewer_decision": None,
            }
        )
        + "\n",
        "utf-8",
    )
    with pytest.raises(ValueError, match="review binding"):
        prepare.run(packet[0], packet[1], packet[2] / "other", baseline_review=review)


def test_existing_output_preserved(packet):
    output = packet[2] / "output"
    output.mkdir()
    with pytest.raises(FileExistsError):
        prepare.run(packet[0], packet[1], output)


def test_report_only_changes_are_audited_without_invalidating_agent_conditions(packet):
    name = "app/evaluation/pairwise.py"
    for path in packet[:2]:
        report = json.loads(path.read_text("utf-8"))
        report["manifest"]["source_sha256"][name] = "d" * 64
        path.write_text(json.dumps(report), "utf-8")
    change(packet, lambda r: r["manifest"]["source_sha256"].update({name: "e" * 64}))
    result = prepare.run(packet[0], packet[1], packet[2] / "output")
    assert result["report_only_source_changes"] == [name]
    assert result["changed_source_files"] == [] and result["paired_answers"] == 3


def test_declared_prompt_fingerprint_must_match_actual_messages(packet):
    change(packet, lambda r: r["manifest"].update(answer_prompt_sha256="0" * 64))
    with pytest.raises(ValueError, match="actual answer prompt binding"):
        prepare.run(packet[0], packet[1], packet[2] / "output")


def test_offline_or_copied_reports_never_become_real_ab_ready(packet):
    result = prepare.run(packet[0], packet[1], packet[2] / "output")
    assert not result["real_ab_input_ready"]
    assert "same_report_self_check" in result["real_ab_blockers"]
    assert "shared_execution_ids" in result["real_ab_blockers"]
    assert "baseline_not_live_llm" in result["real_ab_blockers"]


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "mode",
        "receipt",
        "execution",
        "response_model",
        "no_variable",
        "shared_receipts",
        "baseline_failure",
    ],
)
def test_real_prompt_ab_input_readiness_keeps_evidence_layers(packet, fault):
    import hashlib

    for index, path in enumerate(packet[:2]):
        report = json.loads(path.read_text("utf-8"))
        prompt = "synthetic-prompt-" + str(index if fault != "no_variable" else 0)
        report["manifest"]["answer_prompt_sha256"] = hashlib.sha256(prompt.encode()).hexdigest()
        report.update(
            provider_mode="live_llm",
            model_version="same-model",
            live_provider_calls=3,
            provider_calls=3,
            model_transport_errors=0,
            provider_receipts=[
                {
                    "response_id": f"synthetic-{index}-{n}",
                    "response_model": "same-response-model",
                    "requested_model": "same-model",
                    "finish_reason": "stop",
                }
                for n in range(3)
            ],
        )
        for c, e in zip(report["results"], report["workflow_results"], strict=True):
            execution = c["execution_id"] + str(index if fault != "execution" else 0)
            c["execution_id"] = e["execution_id"] = execution
            for t in c["turns"]:
                for call in t["provider_records"]:
                    call["messages"].insert(0, {"role": "system", "content": prompt})
        if index == 1:
            if fault == "mode":
                report["provider_mode"] = "offline_scripted"
            if fault == "receipt":
                report["provider_receipts"].pop()
            if fault == "response_model":
                report["provider_receipts"][0]["response_model"] = "changed"
            if fault == "shared_receipts":
                report["provider_receipts"][0]["response_id"] = "synthetic-0-0"
        elif fault == "baseline_failure":
            report["workflow_results"][12]["assertions"][0]["passed"] = False
        path.write_text(json.dumps(report), "utf-8")
    result = prepare.run(packet[0], packet[1], packet[2] / "output")
    assert result["real_ab_input_ready"] is (fault is None)
    assert result["quality_winner"] is None and not result["dataset_frozen"]
