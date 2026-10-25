"""Prepare blinded A/B inputs from persisted capture reports; no provider calls."""

import argparse
import hashlib
import json
from pathlib import Path

from app.evaluation.pairwise import execution_eligible
from app.evaluation.workflow_evaluation import load_workflow_cases
from scripts.run_agent_workflow_capture import ROOT, export_semantic

EXPERIMENT_FILES = {"app/agent/intent_classifier.py", "app/rag/policy_answer_service.py"}
REPORT_ONLY_FILES = {
    "app/evaluation/pairwise.py",
    "app/evaluation/failure_pipeline.py",
    "app/evaluation/experiment_tracking.py",
}
IDENTITY_FILES = {
    "app/evaluation/workflow_fixtures.py",
    "app/evaluation/workflow_evaluation.py",
    "app/tools/draft_models.py",
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def read_capture(path):
    report = json.loads(path.read_text("utf-8"))
    manifest = report["manifest"]
    if report.get("source_or_input_drift") is not False:
        raise ValueError("capture source/input drift is unknown or detected")
    cases = {}
    for name, expected in manifest["datasets"].items():
        target = (ROOT / name).resolve()
        if not target.is_relative_to(ROOT.resolve()) or sha(target) != expected:
            raise ValueError("dataset binding mismatch")
        for case in load_workflow_cases(target):
            if case.case_id in cases:
                raise ValueError("duplicate dataset case")
            cases[case.case_id] = case
    captures = {row["case_id"]: row for row in report["results"]}
    evaluations = {row["case_id"]: row for row in report["workflow_results"]}
    if (
        not cases
        or len(captures) != len(report["results"])
        or len(evaluations) != len(report["workflow_results"])
        or captures.keys() != cases.keys()
        or evaluations.keys() != cases.keys()
    ):
        raise ValueError("capture/evaluation inventory mismatch")
    eligible = {}
    for key, source in cases.items():
        captured, evaluation = captures[key], evaluations[key]
        if (
            captured.get("capture_complete") is not True
            or len(captured["turns"]) != len(source.turns)
            or captured["fixture"] != source.fixture
            or captured["split"] != source.split
            or captured["execution_id"] != evaluation["execution_id"]
        ):
            raise ValueError("capture structure or execution binding mismatch")
        state_ok = True
        for index, (turn, expected) in enumerate(zip(captured["turns"], source.turns, strict=True)):
            if turn["turn"] != index or turn["query"] != expected.query:
                raise ValueError("query/turn identity mismatch")
            state_ok &= turn["error_type"] == expected.error
            submitted = [a for a in turn["audit_records"] if a["event"] == "submitted"]
            state_ok &= len(submitted) == expected.submissions
            if expected.error:
                state_ok &= turn["persisted_session"] is None
            else:
                session, result = turn["persisted_session"], turn["result"]
                state_ok &= bool(
                    session
                    and session["phase"] == expected.phase
                    and result
                    and result["status"] == expected.status
                )
            if submitted:
                draft = (turn.get("persisted_draft") or {}).get("draft", {})
                state_ok &= bool(draft.get("user_confirmed") and turn.get("persisted_submission"))
        assertions = evaluation.get("assertions", [])
        blocked_ok = (
            all(t.error for t in source.turns)
            and bool(assertions)
            and all(
                a.get("passed") is True
                and "actual" in a
                and "expected" in a
                and digest(a["actual"]) == digest(a["expected"])
                for a in assertions
            )
            and all(
                any(a["name"] == f"turn_{i}.blocked_state_unchanged" for a in assertions)
                for i in range(len(source.turns))
            )
        )
        eligible[key] = bool(
            state_ok
            and evaluation.get("passed") is True
            and (blocked_ok or execution_eligible({"workflow_result": evaluation}, key))
        )
    prompt_hash = manifest.get("answer_prompt_sha256")
    if prompt_hash is not None:
        for capture in captures.values():
            for turn in capture["turns"]:
                for call in turn["provider_records"]:
                    messages = call["messages"]
                    if any("\n<policy_evidence_json>" in m["content"] for m in messages):
                        if (
                            not messages
                            or messages[0]["role"] != "system"
                            or hashlib.sha256(messages[0]["content"].encode()).hexdigest()
                            != prompt_hash
                        ):
                            raise ValueError("actual answer prompt binding mismatch")
    return report, captures, evaluations, eligible


def real_ab_readiness(
    a,
    b,
    captures_a,
    captures_b,
    *,
    same_report,
    changed,
    regressions,
    pairs,
    all_workflows_eligible,
):
    """Local evidence eligibility only; neither provider authentication nor a quality verdict."""
    blockers = []
    if same_report:
        blockers.append("same_report_self_check")
    if {r["execution_id"] for r in captures_a.values()} & {
        r["execution_id"] for r in captures_b.values()
    }:
        blockers.append("shared_execution_ids")
    if {r.get("response_id") for r in a.get("provider_receipts", [])} & {
        r.get("response_id") for r in b.get("provider_receipts", [])
    } - {None}:
        blockers.append("shared_provider_response_ids")
    for side, report in (("baseline", a), ("candidate", b)):
        if report.get("provider_mode") != "live_llm":
            blockers.append(side + "_not_live_llm")
        calls = report.get("live_provider_calls")
        receipts = report.get("provider_receipts", [])
        if not (
            type(calls) is int
            and calls > 0
            and calls == report.get("provider_calls") == len(receipts)
            and report.get("model_transport_errors") == 0
            and len({r.get("response_id") for r in receipts}) == calls
            and all(
                r.get("response_id")
                and r.get("response_model")
                and r.get("requested_model") == report["model_version"]
                and r.get("finish_reason") == "stop"
                and not r.get("error_type")
                for r in receipts
            )
        ):
            blockers.append(side + "_live_receipts_unverified")
        if not report["manifest"].get("answer_prompt_sha256"):
            blockers.append(side + "_answer_prompt_unbound")
    prompt_changed = a["manifest"].get("answer_prompt_sha256") != b["manifest"].get(
        "answer_prompt_sha256"
    )
    if a["model_version"] == b["model_version"]:
        if not prompt_changed and not changed:
            blockers.append("no_declared_experiment_variable")
        models = [{r.get("response_model") for r in x.get("provider_receipts", [])} for x in (a, b)]
        if models[0] != models[1]:
            blockers.append("response_model_mismatch")
    if regressions:
        blockers.append("workflow_regressions")
    if not all_workflows_eligible:
        blockers.append("ineligible_workflows")
    if not pairs:
        blockers.append("no_eligible_answer_pairs")
    return {
        "real_ab_input_ready": not blockers,
        "real_ab_blockers": blockers,
        "baseline_evidence_mode": a.get("provider_mode", "unknown"),
        "candidate_evidence_mode": b.get("provider_mode", "unknown"),
        "answer_prompt_changed": prompt_changed,
    }


def review_summary(path, capture_hash, items):
    if path is None:
        return {"assistant_reviewed": 0, "human_confirmed": 0, "suggestions": {}}
    rows = [json.loads(s) for s in path.read_text("utf-8").splitlines() if s.strip()]
    if len({r["case_id"] for r in rows}) != len(rows) or any(
        r.get("case_id") not in items
        or r.get("answer") != items[r["case_id"]]["answer"]
        or r.get("source_capture_sha256") != capture_hash
        or r.get("review_mode") != "assistant_assisted_human_review"
        or r.get("assistant_recommendation") not in {"PASS", "FAIL", "UNKNOWN"}
        or r.get("human") is not None
        or r.get("reviewer_decision") is not None
        for r in rows
    ):
        raise ValueError("assisted review binding mismatch")
    return {
        "assistant_reviewed": len(rows),
        "human_confirmed": 0,
        "suggestions": {
            v: sum(r["assistant_recommendation"] == v for r in rows)
            for v in ["PASS", "FAIL", "UNKNOWN"]
        },
    }


def run(
    baseline_path,
    candidate_path,
    output,
    allowed_changes=(),
    baseline_review=None,
    candidate_review=None,
):
    if output.exists():
        raise FileExistsError(output)
    allowed = set(allowed_changes)
    if not allowed <= EXPERIMENT_FILES:
        raise ValueError("only explicit classifier/answer component changes are supported")
    a, ac, ae, aq = read_capture(baseline_path)
    b, bc, be, bq = read_capture(candidate_path)
    for key in ("datasets", "corpus_sha256"):
        if not a["manifest"][key] or a["manifest"][key] != b["manifest"][key]:
            raise ValueError("experiment condition mismatch: " + key)
    sources_a, sources_b = a["manifest"]["source_sha256"], b["manifest"]["source_sha256"]
    if sources_a.keys() != sources_b.keys() or not IDENTITY_FILES <= sources_a.keys():
        raise ValueError("source inventory/identity definition mismatch")
    all_changed = {key for key in sources_a if sources_a[key] != sources_b[key]}
    report_only_changes = all_changed & REPORT_ONLY_FILES
    changed = all_changed - REPORT_ONLY_FILES
    if not changed <= allowed:
        raise ValueError("undeclared source changes: " + ", ".join(sorted(changed - allowed)))
    identity = {
        "dataset_sha256": digest(a["manifest"]["datasets"]),
        "corpus_sha256": a["manifest"]["corpus_sha256"],
        "principal_sha256": digest({k: sources_a[k] for k in sorted(IDENTITY_FILES)}),
        "tool_fixture_sha256": digest(
            {k: v for k, v in sources_a.items() if k not in allowed | REPORT_ONLY_FILES}
        ),
    }
    ai, ab, _ = export_semantic(a["results"])
    bi, bb, _ = export_semantic(b["results"])
    left, right = {r["case_id"]: r for r in ai}, {r["case_id"]: r for r in bi}
    lb, rb = {r["case_id"]: r for r in ab}, {r["case_id"]: r for r in bb}
    pairs, excluded = [], []
    for pair_id in sorted(left.keys() | right.keys()):
        if pair_id not in left or pair_id not in right:
            excluded.append({"case_id": pair_id, "reason": "answer_missing_on_one_side"})
            continue
        case_id = lb[pair_id]["workflow_case_id"]
        if not aq[case_id] or not bq[case_id]:
            excluded.append({"case_id": pair_id, "reason": "workflow_ineligible"})
            continue
        if (
            left[pair_id]["query"] != right[pair_id]["query"]
            or lb[pair_id]["authorized_evidence_records"]
            != rb[pair_id]["authorized_evidence_records"]
        ):
            raise ValueError("paired answer query/authorized evidence mismatch")
        evidence = {
            k: json.dumps(v, ensure_ascii=False, sort_keys=True)
            for k, v in lb[pair_id]["authorized_evidence_records"].items()
        }
        pairs.append(
            {
                "case_id": pair_id,
                "workflow_case_id": case_id,
                "query": left[pair_id]["query"],
                "evidence": evidence,
                "candidate_a": {
                    "answer": left[pair_id]["answer"],
                    "workflow_result": ae[case_id],
                    "manifest": identity,
                },
                "candidate_b": {
                    "answer": right[pair_id]["answer"],
                    "workflow_result": be[case_id],
                    "manifest": identity,
                },
            }
        )
    before_hash, after_hash = sha(baseline_path), sha(candidate_path)
    summary = {
        "baseline_sha256": before_hash,
        "candidate_sha256": after_hash,
        "comparison_kind": "same_report_self_check"
        if before_hash == after_hash
        else "paired_reports",
        "baseline_model": a["model_version"],
        "candidate_model": b["model_version"],
        "case_count": len(ac),
        "baseline_eligible": sum(aq.values()),
        "candidate_eligible": sum(bq.values()),
        "regressions": sorted(k for k in aq if aq[k] and not bq[k]),
        "improvements": sorted(k for k in aq if not aq[k] and bq[k]),
        "paired_answers": len(pairs),
        "excluded_answers": excluded,
        "changed_source_files": sorted(changed),
        "report_only_source_changes": sorted(report_only_changes),
        "allowed_source_changes": sorted(allowed),
        "baseline_assisted_review": review_summary(baseline_review, before_hash, left),
        "candidate_assisted_review": review_summary(candidate_review, after_hash, right),
        "quality_winner": None,
        "additional_model_calls": 0,
        "dataset_frozen": False,
        "provenance_note": "local report hashes bind files; they do not authenticate external execution",
    }
    summary.update(
        real_ab_readiness(
            a,
            b,
            ac,
            bc,
            same_report=before_hash == after_hash,
            changed=changed,
            regressions=summary["regressions"],
            pairs=pairs,
            all_workflows_eligible=all(aq.values()) and all(bq.values()),
        )
    )
    output.mkdir(parents=True)
    (output / "pairwise-inputs.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in pairs), "utf-8"
    )
    (output / "comparison-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    (output / "baseline-lock.json").write_text(
        json.dumps(
            {
                "source": str(baseline_path.resolve()),
                "sha256": before_hash,
                "manifest": a["manifest"],
                "dataset_frozen": False,
                "independent_test_certified": False,
            },
            indent=2,
        )
        + "\n",
        "utf-8",
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-source-change", action="append", default=[])
    parser.add_argument("--baseline-review", type=Path)
    parser.add_argument("--candidate-review", type=Path)
    args = parser.parse_args()
    summary = run(
        args.baseline,
        args.candidate,
        args.output_dir,
        args.allow_source_change,
        args.baseline_review,
        args.candidate_review,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
