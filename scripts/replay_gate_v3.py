"""Replay the confirmed v3 gate on captured candidate rankings.

This is an offline selection replay. It reuses the completed real-BGE
candidate-window archive, but performs no new embedding, retrieval, or
reranker inference. The v3 decision is computed before the frozen selection
proposal is accepted, denied, or retained as an unresolved fallback.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping, Sequence

from scripts.evaluate_gate_v3_decisions import _require_confirmed_review
from scripts.gate_v3_intent import (
    CITY_RULE_ID,
    FINANCE_RULE_ID,
    GateDecision,
    evaluate,
)
from scripts.replay_coverage_windows import select_window
from scripts.run_retrieval_coverage_ablation import (
    build_links,
    canonical_digest,
    digest,
    measure,
    read_archive,
)
from scripts.run_retrieval_coverage_confirmed_eval import (
    DEFAULT_REVIEW_DIR,
    load_confirmed_inputs,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_V3_REVIEW_DIR = ROOT / "artifacts/gate-v3-decision-eval-v1"
WINDOWS = (20, 40)
FINAL_K = 5
RULE_IDS = (CITY_RULE_ID, FINANCE_RULE_ID)
METRICS = ("recall_at_5", "mrr_at_5", "ndcg_at_5")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _read_json(files: Mapping[str, bytes], name: str) -> dict[str, Any]:
    try:
        value = json.loads(files[name].decode("utf-8-sig"))
    except KeyError as exc:
        raise ValueError(f"archive is missing {name}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {name}: {exc.msg}") from exc
    _require(isinstance(value, dict), f"{name} must be a JSON object")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _at5(value: Mapping[str, Any]) -> float:
    metrics = value.get("recall_at_k", {})
    result = metrics.get("5", metrics.get(5))
    _require(result is not None, "missing Recall@5")
    return float(result)


def _metric_delta(before: Mapping[str, float], after: Mapping[str, float]) -> dict[str, float]:
    return {metric: float(after[metric]) - float(before[metric]) for metric in METRICS}


def _mean(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    _require(bool(rows), f"empty cohort: {key}")
    return {
        "effective_n": len(rows),
        **{metric: sum(float(row[key][metric]) for row in rows) / len(rows) for metric in METRICS},
    }


def _timing(values: Sequence[float]) -> dict[str, float]:
    _require(bool(values), "empty timing sample")
    ordered = sorted(float(value) for value in values)
    return {
        "n": len(ordered),
        "mean_ms": sum(ordered) / len(ordered),
        "min_ms": ordered[0],
        "max_ms": ordered[-1],
        "p95_ms": ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)],
    }


def _is_control(case: Mapping[str, Any]) -> bool:
    tags = case.get("tags", ())
    return any(tag == "negative_control" or str(tag).startswith("unnecessary_") for tag in tags)


def _decision_payload(query: str, rule_id: str) -> dict[str, Any]:
    intent, result = evaluate(query, rule_id)
    return {
        "decision": result.decision.value,
        "reason_code": result.reason_code,
        "evidence_spans": [span.as_dict() for span in result.evidence_spans],
        "unresolved_reasons": list(result.unresolved_reasons),
        "intent": intent.as_dict(),
    }


def select_v3(
    query: str,
    baseline: Sequence[str],
    candidates: Sequence[str],
    allowed: set[str],
    links: Sequence[Mapping[str, Any]],
    *,
    window: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Apply v3 decisions while preserving Top-4/one-supplement policy."""

    _require(type(window) is int and window in WINDOWS, "window must be 20 or 40")
    _require(len(candidates) <= window, "candidate pool exceeds window")
    _require(len(set(candidates)) == len(candidates), "candidate pool contains duplicates")
    _require(set(candidates) <= allowed, "candidate pool contains unauthorized IDs")
    _require(len(baseline) == FINAL_K, "baseline must contain final K items")
    _require(len(set(baseline)) == len(baseline), "baseline contains duplicates")
    _require(set(baseline) <= set(candidates), "baseline escaped candidate pool")

    decision_cache: dict[str, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []

    def decision_for(rule_id: str) -> dict[str, Any]:
        if rule_id not in decision_cache:
            decision_cache[rule_id] = _decision_payload(query, rule_id)
        return decision_cache[rule_id]

    for anchor in baseline[:2]:
        for link in links:
            if link["source"] != anchor:
                continue
            target = str(link["target"])
            gate = decision_for(str(link["rule_id"]))
            event: dict[str, Any] = {
                "rule_id": link["rule_id"],
                "anchor": anchor,
                "target": target,
                "gate_decision": gate["decision"],
                "reason_code": gate["reason_code"],
                "evidence_spans": gate["evidence_spans"],
                "unresolved_reasons": gate["unresolved_reasons"],
                "fallback_applied": False,
            }
            if target in baseline:
                events.append({**event, "action": "already_present"})
                continue
            if target not in candidates or target not in allowed:
                events.append({**event, "action": "outside_authorized_candidate_window"})
                continue
            decision = gate["decision"]
            if decision == GateDecision.ALLOW.value:
                events.append({**event, "action": "supplemented", "evicted": list(baseline[4:])})
                return list(baseline[:4]) + [target], events
            if decision == GateDecision.UNRESOLVED.value:
                events.append(
                    {
                        **event,
                        "action": "supplemented",
                        "fallback_applied": True,
                        "evicted": list(baseline[4:]),
                    }
                )
                return list(baseline[:4]) + [target], events
            if decision == GateDecision.DENY.value:
                events.append({**event, "action": "denied_by_gate"})
                continue
            if decision == GateDecision.NOT_APPLICABLE.value:
                events.append({**event, "action": "not_applicable"})
                continue
            raise ValueError(f"unsupported v3 decision: {decision}")
    return list(baseline), events


def _validate_v3_freeze(v3_review_dir: Path) -> dict[str, Any]:
    review_path = v3_review_dir / "review.json"
    confirmation_path = v3_review_dir / "user-confirmation.json"
    lock_path = v3_review_dir / "confirmed-lock.json"
    review, confirmation, freeze = _require_confirmed_review(
        review_path, confirmation_path, lock_path
    )
    frozen_files = freeze.get("frozen_files", {})
    _require(isinstance(frozen_files, dict) and frozen_files, "v3 freeze has no frozen files")
    for filename, expected in frozen_files.items():
        path = Path(filename)
        _require(path.is_file(), f"v3 frozen input is missing: {filename}")
        _require(_sha256(path) == expected, f"v3 frozen input changed: {filename}")
    return {
        "review": review,
        "confirmation": confirmation,
        "freeze": freeze,
        "review_sha256": _sha256(review_path),
        "confirmation_sha256": _sha256(confirmation_path),
        "freeze_sha256": _sha256(v3_review_dir / "freeze.json"),
    }


def _validate_archive(
    files: Mapping[str, bytes],
    inputs: Any,
) -> dict[str, Any]:
    evidence = _read_json(files, "candidate-window-report.json")
    _require(
        evidence.get("status") == "completed"
        and evidence.get("mode") == "bge"
        and evidence.get("real_model_inference") is True,
        "completed real-BGE candidate archive required",
    )
    _require(evidence.get("query_count") == len(inputs.cases), "candidate query count mismatch")
    protocol = _read_json(files, "protocol.json")
    source = _read_json(files, "source-manifest.json")
    environment = _read_json(files, "environment.json")
    models = _read_json(files, "models.lock.json")
    archive_review = _read_json(files, "review.user-confirmed.json")
    archive_rules = _read_json(files, "rules.json")
    archive_corpus = _read_json(files, "frozen-corpus.json")
    _require(canonical_digest(source) == evidence["source_sha256"], "source fingerprint mismatch")
    _require(
        canonical_digest(protocol) == evidence["protocol_sha256"], "protocol fingerprint mismatch"
    )
    _require(
        canonical_digest(environment) == evidence["environment_sha256"],
        "environment fingerprint mismatch",
    )
    _require(canonical_digest(models) == evidence["model_lock_sha256"], "model lock mismatch")
    _require(archive_review == inputs.review, "candidate labels changed")
    _require(archive_rules == inputs.rules, "candidate rules changed")
    _require(archive_corpus == inputs.corpus, "candidate ACL/corpus snapshot changed")
    _require(models == inputs.freeze.get("model_lock"), "candidate model differs from frozen model")
    _require(
        protocol.get("input", {}).get("confirmed_review_sha256") == canonical_digest(inputs.review),
        "candidate protocol labels mismatch",
    )
    configuration = evidence.get("configuration", {})
    _require(
        configuration.get("candidate_windows") == list(WINDOWS)
        and configuration.get("final_k") == FINAL_K
        and configuration.get("rrf_rank_constant") == 60,
        "candidate configuration drift",
    )
    for window in WINDOWS:
        report = _read_json(files, f"window-{window}/retrieval-evaluation-report.json")
        _require(report.get("evaluation_mode") == "bge", "window is not BGE evidence")
        _require(report.get("candidate_k") == window, "candidate window mismatch")
        _require(report.get("total_cases") == len(inputs.cases), "window case count mismatch")
        _require(
            digest(files[f"window-{window}/target-dataset.jsonl"]) == evidence["dataset_sha256"],
            "window dataset fingerprint mismatch",
        )
        traces = _read_json(files, f"window-{window}/candidate-traces.json")
        _require(len(traces) == len(inputs.cases), "candidate trace count mismatch")
        for case, captured in zip(inputs.cases, report["case_results"], strict=True):
            _require(
                case["case_id"] == captured["case_id"]
                and case["query"] == captured["query"]
                and case["judgments"] == captured["judgments"],
                f"candidate case drift: {case['case_id']}",
            )
            by_channel = {item["channel"]: item for item in captured["channels"]}
            _require(
                set(by_channel) == {"vector", "bm25", "hybrid", "reranked"},
                "candidate channels changed",
            )
            _require(
                all(item.get("error") is None for item in by_channel.values()), "channel error"
            )
            _require(case["query"] in traces, f"missing candidate trace: {case['case_id']}")
            pool = traces[case["query"]].get("hybrid", [])
            _require(len(pool) == window and len(set(pool)) == len(pool), "invalid hybrid pool")
            for channel, item in by_channel.items():
                ids = item.get("retrieved_chunk_ids", [])
                _require(
                    len(ids) == FINAL_K
                    and len(set(ids)) == len(ids)
                    and set(ids) <= set(inputs.allowed_chunk_ids),
                    f"invalid {channel} ranking: {case['case_id']}",
                )
            _require(
                by_channel["hybrid"]["retrieved_chunk_ids"] == pool[:FINAL_K],
                f"RRF trace mismatch: {case['case_id']}",
            )
    return evidence


def replay(
    evidence_zip: Path,
    *,
    review_dir: Path = DEFAULT_REVIEW_DIR,
    v3_review_dir: Path = DEFAULT_V3_REVIEW_DIR,
) -> dict[str, Any]:
    inputs = load_confirmed_inputs(review_dir)
    v3 = _validate_v3_freeze(v3_review_dir)
    files = read_archive(evidence_zip)
    evidence = _validate_archive(files, inputs)
    source_models = _read_json(files, "models.lock.json")
    source_environment = _read_json(files, "environment.json")
    links = build_links(inputs.corpus["chunks"], inputs.rules)
    rows_by_window: dict[int, list[dict[str, Any]]] = {}
    summaries: dict[int, dict[str, Any]] = {}

    for window in WINDOWS:
        report = _read_json(files, f"window-{window}/retrieval-evaluation-report.json")
        traces = _read_json(files, f"window-{window}/candidate-traces.json")
        rows: list[dict[str, Any]] = []
        for case, captured in zip(inputs.cases, report["case_results"], strict=True):
            by_channel = {item["channel"]: item for item in captured["channels"]}
            baseline = list(by_channel["reranked"]["retrieved_chunk_ids"])
            pool = list(traces[case["query"]]["hybrid"])
            _require(len(baseline) == FINAL_K, f"invalid baseline length: {case['case_id']}")
            _require(set(baseline) <= set(pool), f"reranker escaped RRF pool: {case['case_id']}")
            baseline_metrics = measure(baseline, case["judgments"])
            for channel in ("vector", "bm25", "hybrid", "reranked"):
                ids = list(by_channel[channel]["retrieved_chunk_ids"])
                measured = measure(ids, case["judgments"])
                reported = by_channel[channel]
                _require(
                    math.isclose(measured["recall_at_5"], _at5(reported), abs_tol=1e-12)
                    and math.isclose(
                        measured["mrr_at_5"], float(reported["reciprocal_rank"]), abs_tol=1e-12
                    ),
                    f"metric drift: {case['case_id']}/{channel}",
                )
            frozen_started = perf_counter()
            frozen_selected, frozen_events = select_window(
                baseline,
                pool,
                set(inputs.allowed_chunk_ids),
                links,
                window=window,
            )
            frozen_elapsed = (perf_counter() - frozen_started) * 1000.0
            v3_started = perf_counter()
            v3_selected, v3_events = select_v3(
                case["query"],
                baseline,
                pool,
                set(inputs.allowed_chunk_ids),
                links,
                window=window,
            )
            v3_elapsed = (perf_counter() - v3_started) * 1000.0
            frozen_metrics = measure(frozen_selected, case["judgments"])
            v3_metrics = measure(v3_selected, case["judgments"])
            relation_decisions = {
                rule_id: _decision_payload(case["query"], rule_id) for rule_id in RULE_IDS
            }
            rows.append(
                {
                    "case_id": case["case_id"],
                    "query": case["query"],
                    "control": _is_control(case),
                    "judgments": [dict(judgment) for judgment in case["judgments"]],
                    "baseline_ids": baseline,
                    "candidate_pool_ids": pool,
                    "baseline_metrics": baseline_metrics,
                    "frozen_selected_ids": frozen_selected,
                    "frozen_metrics": frozen_metrics,
                    "frozen_events": frozen_events,
                    "v3_selected_ids": v3_selected,
                    "v3_metrics": v3_metrics,
                    "v3_events": v3_events,
                    "relation_decisions": relation_decisions,
                    "frozen_changed": baseline != frozen_selected,
                    "v3_changed": baseline != v3_selected,
                    "fallback_applied": any(
                        event.get("fallback_applied") is True for event in v3_events
                    ),
                    "evicted_by_frozen": [item for item in baseline if item not in frozen_selected],
                    "evicted_by_v3": [item for item in baseline if item not in v3_selected],
                    "regressions_vs_frozen": [
                        metric
                        for metric in METRICS
                        if v3_metrics[metric] < frozen_metrics[metric] - 1e-12
                    ],
                    "improvements_vs_frozen": [
                        metric
                        for metric in METRICS
                        if v3_metrics[metric] > frozen_metrics[metric] + 1e-12
                    ],
                    "selection_ms": {
                        "frozen": frozen_elapsed,
                        "v3": v3_elapsed,
                    },
                }
            )
        rows_by_window[window] = rows
        controls = [row for row in rows if row["control"]]
        summaries[window] = {
            "window": window,
            "candidate_k_per_channel": window,
            "rrf_rank_constant": 60,
            "rerank_window": window,
            "final_k": FINAL_K,
            "query_count": len(rows),
            "judgment_count": sum(len(row["judgments"]) for row in rows),
            "baseline": _mean(rows, "baseline_metrics"),
            "frozen": _mean(rows, "frozen_metrics"),
            "v3": _mean(rows, "v3_metrics"),
            "delta_v3_minus_frozen": _metric_delta(
                _mean(rows, "frozen_metrics"), _mean(rows, "v3_metrics")
            ),
            "frozen_changed_count": sum(row["frozen_changed"] for row in rows),
            "v3_changed_count": sum(row["v3_changed"] for row in rows),
            "fallback_count": sum(row["fallback_applied"] for row in rows),
            "regression_count": sum(bool(row["regressions_vs_frozen"]) for row in rows),
            "regressed_case_ids": [row["case_id"] for row in rows if row["regressions_vs_frozen"]],
            "timing_ms": {
                "frozen": _timing([row["selection_ms"]["frozen"] for row in rows]),
                "v3": _timing([row["selection_ms"]["v3"] for row in rows]),
            },
            "controls": {
                "case_ids": [row["case_id"] for row in controls],
                "count": len(controls),
                "baseline": _mean(controls, "baseline_metrics") if controls else None,
                "frozen": _mean(controls, "frozen_metrics") if controls else None,
                "v3": _mean(controls, "v3_metrics") if controls else None,
                "frozen_changed_count": sum(row["frozen_changed"] for row in controls),
                "v3_changed_count": sum(row["v3_changed"] for row in controls),
            },
        }

    relation_counts = Counter(
        decision["decision"]
        for rows in rows_by_window.values()
        for row in rows
        for decision in row["relation_decisions"].values()
    )
    event_counts = Counter(
        event["action"]
        for rows in rows_by_window.values()
        for row in rows
        for event in row["v3_events"]
    )
    return {
        "schema_version": "1.0",
        "phase": 38,
        "step": 3,
        "substep": 3,
        "suite_name": "enterprise_policy_agent_gate_v3_candidate_replay",
        "status": "completed",
        "mode": "captured_bge_ranking_replay",
        "new_model_inference": False,
        "source_real_model_inference": True,
        "numeric_resume_ready": False,
        "label_status": "user_confirmed_ai_assisted_development_set",
        "independent_test_set": False,
        "v3_decision_label_status": "user_confirmed_ai_authored_source_checked_not_independently_double_annotated",
        "query_count": len(inputs.cases),
        "judgment_count": sum(len(case["judgments"]) for case in inputs.cases),
        "input_zip_sha256": digest(evidence_zip.read_bytes()),
        "source_experiment": evidence.get("git"),
        "replay_script_sha256": _sha256(Path(__file__)),
        "source_model_lock": source_models,
        "source_environment": source_environment,
        "source_protocol_sha256": evidence.get("protocol_sha256"),
        "source_environment_sha256": evidence.get("environment_sha256"),
        "source_dataset_sha256": evidence.get("dataset_sha256"),
        "candidate_report_sha256": canonical_digest(evidence),
        "candidate_labels_sha256": canonical_digest(inputs.review),
        "candidate_rules_sha256": canonical_digest(inputs.rules),
        "candidate_corpus_sha256": canonical_digest(inputs.corpus),
        "v3_review_sha256": v3["review_sha256"],
        "v3_confirmation_sha256": v3["confirmation_sha256"],
        "v3_freeze_sha256": v3["freeze_sha256"],
        "configuration": {
            "candidate_windows": list(WINDOWS),
            "anchor_top_k": 2,
            "preserve_top_k": 4,
            "max_supplements": 1,
            "final_k": FINAL_K,
            "rrf_rank_constant": 60,
            "same_candidates_per_comparison": True,
            "frozen_selector_unchanged": True,
            "v3_decisions": [decision.value for decision in GateDecision],
        },
        "timing_scope": "selection only; v3 parsing and selection versus frozen selection; excludes archive IO, model loading, embedding, retrieval, and reranking inference",
        "timing_for_resume": False,
        "relation_decision_counts": {
            value: relation_counts.get(value, 0)
            for value in (decision.value for decision in GateDecision)
        },
        "v3_event_action_counts": dict(sorted(event_counts.items())),
        "windows": {str(window): summaries[window] for window in WINDOWS},
        "cases": {str(window): rows_by_window[window] for window in WINDOWS},
        "runtime_backend_changed": False,
        "sqlite_data_migrated": False,
        "langgraph_checkpoint_backend_changed": False,
        "limitations": [
            "COV labels are a user-confirmed AI-assisted development set, not an independent holdout.",
            "This replay measures evidence selection on captured rankings; it does not measure new model quality or production latency.",
            "UNRESOLVED is intentionally replayed as the frozen supplement fallback and is reported separately from ALLOW/DENY success.",
        ],
        "generated_at": datetime.now(UTC).isoformat(),
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    """Render a compact, auditable summary without hiding per-case evidence."""

    lines = [
        "# 门控 v3 候选回放报告",
        "",
        f"状态：`{report['status']}`；模式：`{report['mode']}`。",
        "本报告重放已完成的真实 BGE 候选排名；本步骤没有重新执行 embedding、向量检索或 reranker 推理。",
        "标签来自用户确认的 COV 开发集，未经过独立双人复核，因此 `numeric_resume_ready=false`，不能当作独立测试集成绩。",
        "",
        "## 配置与计时边界",
        "",
        "两个窗口使用同一语料、权限范围、标签、候选记录、RRF 常数 60 和最终 K=5；选择策略固定为前 2 个锚点、保留前 4 条、最多补充 1 条。",
        "选择耗时只覆盖冻结选择器和 v3 选择器本身，排除归档读取、模型加载、向量化、检索、RRF 和重排。",
        "",
        "## 窗口汇总",
        "",
        "| 窗口 | 阶段 | N | Recall@5 | MRR@5 | nDCG@5 | 改变数 | 回归数 | 平均选择耗时 (ms) |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for window in WINDOWS:
        summary = report["windows"][str(window)]
        for stage in ("baseline", "frozen", "v3"):
            metrics = summary[stage]
            changed = 0 if stage == "baseline" else summary[f"{stage}_changed_count"]
            regressions = 0 if stage != "v3" else summary["regression_count"]
            timing = "—" if stage == "baseline" else f"{summary['timing_ms'][stage]['mean_ms']:.3f}"
            lines.append(
                f"| {window} | {stage} | {metrics['effective_n']} | "
                f"{metrics['recall_at_5']:.4f} | {metrics['mrr_at_5']:.4f} | "
                f"{metrics['ndcg_at_5']:.4f} | {changed} | {regressions} | {timing} |"
            )
        delta = summary["delta_v3_minus_frozen"]
        lines.append(
            f"| {window} | v3 - frozen | — | {delta['recall_at_5']:+.4f} | "
            f"{delta['mrr_at_5']:+.4f} | {delta['ndcg_at_5']:+.4f} | — | — | — |"
        )
    lines.extend(
        [
            "",
            "## 决策与回归诊断",
            "",
            f"关系决策计数（两个窗口合并）：`{json.dumps(report['relation_decision_counts'], ensure_ascii=False)}`。",
            f"v3 事件计数（两个窗口合并）：`{json.dumps(report['v3_event_action_counts'], ensure_ascii=False)}`。",
            "每条查询的候选池、基线、两种选择结果、事件、被替换条款和指标均保存在 `replay-report.json` 的 `cases` 中；控制查询单独保存在各窗口的 `controls` 中。",
            "",
            "## 限制",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-zip", type=Path, required=True)
    parser.add_argument("--review-dir", type=Path, default=DEFAULT_REVIEW_DIR)
    parser.add_argument("--v3-review-dir", type=Path, default=DEFAULT_V3_REVIEW_DIR)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    try:
        report = replay(
            args.evidence_zip,
            review_dir=args.review_dir,
            v3_review_dir=args.v3_review_dir,
        )
    except Exception as exc:
        failure = {
            "schema_version": "1.0",
            "phase": 38,
            "step": 3,
            "substep": 3,
            "status": "blocked",
            "numeric_resume_ready": False,
            "error": f"{type(exc).__name__}: {exc}",
            "new_model_inference": False,
        }
        (args.output_dir / "failure.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        raise
    (args.output_dir / "replay-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output_dir / "replay-report.md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "mode": report["mode"],
                "numeric_resume_ready": report["numeric_resume_ready"],
                "windows": {
                    window: {
                        "baseline": report["windows"][window]["baseline"],
                        "frozen": report["windows"][window]["frozen"],
                        "v3": report["windows"][window]["v3"],
                        "delta_v3_minus_frozen": report["windows"][window]["delta_v3_minus_frozen"],
                        "regression_count": report["windows"][window]["regression_count"],
                    }
                    for window in (str(value) for value in WINDOWS)
                },
                "report": str(args.output_dir / "replay-report.json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
