"""Run a frozen, targeted retrieval evaluation on the user-confirmed COV set.

The COV set is deliberately kept separate from the legacy 20-query development
set.  Its labels were proposed with AI assistance and explicitly confirmed by
the user; they are not an independently human-reviewed test set.  This command
therefore never reports ``numeric_resume_ready=true``.  In ``bge`` mode all
embeddings and reranking are real model calls.  ``offline`` is only a wiring
and data-integrity smoke test and is labelled as such in every report.

No production runtime, SQLite data, or LangGraph checkpoint backend is changed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any
from uuid import uuid4

from scripts.run_retrieval_coverage_ablation import (
    DEFAULT_RULES,
    ROOT,
    build_links,
    canonical_digest,
    digest,
    measure,
    select_coverage,
    summarize,
)


DEFAULT_REVIEW_DIR = ROOT / "artifacts/retrieval-coverage-review-v1"
DEFAULT_MODEL_LOCK = ROOT / "artifacts/retrieval-models.lock.json"
DEFAULT_QUERIES = ROOT / "tests/evaluation/retrieval_coverage_review_queries.json"
SELECTOR = ROOT / "scripts/run_retrieval_coverage_ablation.py"
EXPECTED_CASE_IDS = tuple(f"COV-{number:03d}" for number in range(1, 17))
EXPECTED_CONFIRMED_CASE_IDS = tuple(f"COV-{number:03d}" for number in range(2, 17))
TARGET_LABEL_STATUS = "user_confirmed_ai_assisted_development_set"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ValueError(f"cannot read {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc.msg}") from exc
    _require(isinstance(value, dict), f"{path} must contain a JSON object")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _text_digest(path: Path) -> str:
    return digest(path.read_bytes().replace(b"\r\n", b"\n"))


def runtime_corpus_digest(chunks: list[dict[str, Any]]) -> str:
    """Match ``app.evaluation.retrieval_runtime.corpus_sha256`` without imports."""

    manifest = "\n".join(
        f"{chunk['chunk_id']}:{chunk['content_hash']}"
        for chunk in sorted(chunks, key=lambda item: item["chunk_id"])
    )
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def synthetic_case_id(case_id: str) -> str:
    """Map COV IDs to the existing RetrievalCase ID contract without changing labels."""

    _require(case_id in EXPECTED_CASE_IDS, f"unknown targeted case: {case_id}")
    number = int(case_id.rsplit("-", 1)[1])
    return f"RET-{900 + number:03d}"


def _validate_corpus_snapshot(corpus: dict[str, Any]) -> tuple[set[str], str]:
    chunks = corpus.get("chunks")
    allowed_values = corpus.get("allowed_chunk_ids")
    _require(isinstance(chunks, list) and chunks, "frozen corpus must contain chunks")
    _require(isinstance(allowed_values, list), "frozen corpus must contain allowed_chunk_ids")
    ids = [chunk.get("chunk_id") for chunk in chunks if isinstance(chunk, dict)]
    _require(
        len(ids) == len(chunks) and all(isinstance(item, str) and item for item in ids),
        "invalid corpus IDs",
    )
    _require(len(set(ids)) == len(ids), "duplicate corpus chunk_id")
    _require(all(isinstance(item, str) and item for item in allowed_values), "invalid ACL IDs")
    allowed = set(allowed_values)
    _require(len(allowed) == len(allowed_values) and allowed <= set(ids), "invalid ACL snapshot")
    manifest_hash = canonical_digest(chunks)
    _require(
        manifest_hash == corpus.get("full_chunk_manifest_sha256"),
        "frozen corpus manifest fingerprint mismatch",
    )
    _require(
        all(isinstance(chunk.get("content_hash"), str) for chunk in chunks),
        "missing content_hash",
    )
    return allowed, runtime_corpus_digest(chunks)


def _validate_judgments(case: dict[str, Any], allowed: set[str]) -> None:
    judgments = case.get("judgments")
    _require(isinstance(judgments, list) and judgments, f"{case.get('case_id')} needs judgments")
    ids: list[str] = []
    for judgment in judgments:
        _require(isinstance(judgment, dict), f"{case.get('case_id')} has malformed judgment")
        chunk_id = judgment.get("chunk_id")
        _require(
            isinstance(chunk_id, str) and chunk_id in allowed,
            f"unauthorized judgment: {chunk_id}",
        )
        _require(chunk_id not in ids, f"duplicate judgment in {case.get('case_id')}")
        ids.append(chunk_id)
        relevance = judgment.get("relevance")
        _require(
            type(relevance) is int and relevance in (1, 2, 3),
            f"invalid relevance grade in {case.get('case_id')}",
        )
        _require(
            isinstance(judgment.get("rationale"), str) and judgment["rationale"].strip(),
            f"missing judgment rationale in {case.get('case_id')}",
        )


def validate_confirmed_payload(
    *,
    freeze: dict[str, Any],
    review: dict[str, Any],
    confirmation: dict[str, Any],
    queries: dict[str, Any],
    corpus: dict[str, Any],
    rules: dict[str, Any],
) -> tuple[tuple[dict[str, Any], ...], set[str], str]:
    """Validate labels and frozen inputs before importing model/runtime code."""

    _require(freeze.get("schema_version") == "1.0", "unsupported freeze schema")
    _require(review.get("schema_version") == "1.0", "unsupported confirmed review schema")
    _require(confirmation.get("schema_version") == "1.0", "unsupported confirmation schema")
    _require(review.get("freeze_sha256") == canonical_digest(freeze), "freeze identity mismatch")
    _require(
        confirmation.get("status") == "user_confirmed_ai_assisted_labels",
        "confirmation status is not user-confirmed AI-assisted labels",
    )
    _require(
        tuple(confirmation.get("confirmed_case_ids", ())) == EXPECTED_CONFIRMED_CASE_IDS,
        "confirmation must cover exactly COV-002..COV-016",
    )
    for field in (
        "full_authorized_corpus_review",
        "checked_all_authorized_chunks",
        "viewed_retrieval_outputs",
    ):
        _require(confirmation.get(field) is False, f"confirmation field must remain false: {field}")
    _require(
        confirmation.get("retrieval_executed") is False,
        "confirmation already records retrieval",
    )
    _require(
        confirmation.get("numeric_resume_ready") is False,
        "confirmation cannot be resume evidence",
    )

    proposed_cases = queries.get("cases")
    reviewed_cases = review.get("cases")
    _require(
        isinstance(proposed_cases, list) and len(proposed_cases) == 16,
        "expected 16 frozen queries",
    )
    _require(isinstance(reviewed_cases, list), "confirmed review cases must be a list")
    _require(len(reviewed_cases) == len(proposed_cases), "missing or extra confirmed cases")
    _require(
        [case.get("case_id") for case in proposed_cases] == list(EXPECTED_CASE_IDS),
        "frozen query IDs/order changed",
    )
    _require(
        [case.get("case_id") for case in reviewed_cases] == list(EXPECTED_CASE_IDS),
        "confirmed review IDs/order changed",
    )

    allowed, runtime_hash = _validate_corpus_snapshot(corpus)
    _require(
        canonical_digest(rules) == freeze.get("rules_sha256"),
        "frozen rules fingerprint mismatch",
    )
    _require(
        freeze.get("scope") == "prospective_targeted_validation_not_independent_test",
        "unexpected target scope",
    )

    validated: list[dict[str, Any]] = []
    for proposed, actual in zip(proposed_cases, reviewed_cases, strict=True):
        _require(
            all(actual.get(key) == proposed.get(key) for key in ("case_id", "query", "tags")),
            f"query drift in {proposed.get('case_id')}",
        )
        _require(
            actual.get("answerability") == "answerable",
            f"{actual.get('case_id')} is not answerable",
        )
        review_state = actual.get("review")
        _require(isinstance(review_state, dict), f"missing review state in {actual.get('case_id')}")
        expected_state = (
            "pending" if actual["case_id"] == "COV-001" else "user_confirmed_ai_assisted"
        )
        _require(
            review_state.get("status") == expected_state,
            f"unexpected review status for {actual.get('case_id')}: {review_state.get('status')}",
        )
        _require(
            review_state.get("checked_all_authorized_chunks") is False,
            "full corpus review must be false",
        )
        _require(
            review_state.get("viewed_retrieval_outputs") is False,
            "retrieval output review must be false",
        )
        _validate_judgments(actual, allowed)
        validated.append(actual)

    return tuple(validated), allowed, runtime_hash


@dataclass(frozen=True, slots=True)
class ConfirmedInputs:
    review_dir: Path
    freeze: dict[str, Any]
    review: dict[str, Any]
    confirmation: dict[str, Any]
    queries: dict[str, Any]
    corpus: dict[str, Any]
    rules: dict[str, Any]
    cases: tuple[dict[str, Any], ...]
    allowed_chunk_ids: frozenset[str]
    runtime_corpus_sha256: str


def load_confirmed_inputs(review_dir: Path) -> ConfirmedInputs:
    review_dir = review_dir.resolve()
    names = (
        "freeze.json",
        "review.user-confirmed.json",
        "user-confirmation.json",
        "queries.json",
        "corpus.json",
        "rules.json",
    )
    payloads = {name: _read_json(review_dir / name) for name in names}
    cases, allowed, runtime_hash = validate_confirmed_payload(
        freeze=payloads["freeze.json"],
        review=payloads["review.user-confirmed.json"],
        confirmation=payloads["user-confirmation.json"],
        queries=payloads["queries.json"],
        corpus=payloads["corpus.json"],
        rules=payloads["rules.json"],
    )
    freeze = payloads["freeze.json"]
    _require(
        canonical_digest(payloads["queries.json"]) == freeze.get("query_set_sha256"),
        "queries changed after freeze",
    )
    _require(
        canonical_digest(payloads["corpus.json"]) == freeze.get("corpus_snapshot_sha256"),
        "corpus changed after freeze",
    )
    _require(
        _text_digest(SELECTOR) == freeze.get("selector_sha256_lf"),
        "selector changed after freeze",
    )
    current_queries = _read_json(DEFAULT_QUERIES)
    current_rules = _read_json(DEFAULT_RULES)
    _require(
        canonical_digest(current_queries) == freeze.get("query_set_sha256"),
        "repository query file drift",
    )
    _require(
        canonical_digest(current_rules) == freeze.get("rules_sha256"),
        "repository rules file drift",
    )
    return ConfirmedInputs(
        review_dir=review_dir,
        freeze=freeze,
        review=payloads["review.user-confirmed.json"],
        confirmation=payloads["user-confirmation.json"],
        queries=payloads["queries.json"],
        corpus=payloads["corpus.json"],
        rules=payloads["rules.json"],
        cases=cases,
        allowed_chunk_ids=frozenset(allowed),
        runtime_corpus_sha256=runtime_hash,
    )


def build_target_dataset_rows(cases: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    return [
        {
            "case_id": synthetic_case_id(case["case_id"]),
            "title": case["case_id"],
            "query": case["query"],
            "judgments": case["judgments"],
            "tags": case["tags"],
        }
        for case in cases
    ]


def target_dataset_bytes(cases: tuple[dict[str, Any], ...]) -> bytes:
    rows = build_target_dataset_rows(cases)
    return b"".join(
        (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        for row in rows
    )


def remap_report_case_ids(report: Any, cases: tuple[dict[str, Any], ...]) -> Any:
    """Return the normal report with public COV IDs instead of synthetic RET IDs."""

    remapped = tuple(
        result.model_copy(update={"case_id": case["case_id"], "title": case["case_id"]})
        for result, case in zip(report.case_results, cases, strict=True)
    )
    return report.model_copy(update={"case_results": remapped})


def _metric(report_result: dict[str, Any], name: str) -> float:
    if name == "mrr_at_5":
        return float(report_result["reciprocal_rank"])
    return float(report_result["recall_at_k"].get("5", report_result["recall_at_k"].get(5)))


def compute_coverage_rows(
    *,
    report: dict[str, Any],
    cases: tuple[dict[str, Any], ...],
    candidates: dict[str, dict[str, list[str]]],
    links: list[dict[str, Any]],
    allowed: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    rows: list[dict[str, Any]] = []
    for case, captured in zip(cases, report["case_results"], strict=True):
        by_channel = {item["channel"]: item for item in captured["channels"]}
        reranked = by_channel["reranked"]
        baseline_ids = list(reranked["retrieved_chunk_ids"])
        pool = list(candidates.get(case["query"], {}).get("hybrid", ()))
        row: dict[str, Any] = {
            "case_id": case["case_id"],
            "query": case["query"],
            "judgments": case["judgments"],
            "baseline_ids": baseline_ids,
            "candidate_pool_ids": pool,
            "selector_events": [],
            "selected_ids": [],
            "baseline_metrics": None,
            "coverage_metrics": None,
            "candidate_recall_at_20": None,
            "changed": False,
            "error": reranked.get("error"),
        }
        if reranked.get("error"):
            rows.append(row)
            continue
        _require(
            len(pool) <= 20 and len(set(pool)) == len(pool),
            f"invalid hybrid pool for {case['case_id']}",
        )
        _require(set(pool) <= allowed, f"unauthorized hybrid candidate for {case['case_id']}")
        baseline_metrics = measure(baseline_ids, case["judgments"])
        selected_ids, events = select_coverage(baseline_ids, pool, allowed, links)
        coverage_metrics = measure(selected_ids, case["judgments"])
        relevant_ids = {judgment["chunk_id"] for judgment in case["judgments"]}
        candidate_recall = len(relevant_ids & set(pool)) / len(relevant_ids)
        _require(
            math.isclose(
                _metric(reranked, "mrr_at_5"), baseline_metrics["mrr_at_5"], abs_tol=1e-12
            ),
            f"MRR drift for {case['case_id']}",
        )
        _require(
            math.isclose(
                _metric(reranked, "recall_at_5"),
                baseline_metrics["recall_at_5"],
                abs_tol=1e-12,
            ),
            f"Recall drift for {case['case_id']}",
        )
        row.update(
            {
                "selector_events": events,
                "selected_ids": selected_ids,
                "baseline_metrics": baseline_metrics,
                "coverage_metrics": coverage_metrics,
                "candidate_recall_at_20": candidate_recall,
                "changed": selected_ids != baseline_ids,
            }
        )
        rows.append(row)
    valid = [row for row in rows if row["coverage_metrics"] is not None]
    summary = summarize(valid, "coverage_metrics") if len(valid) == len(rows) and rows else None
    return rows, summary


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.2%}"


def render_coverage_markdown(evidence: dict[str, Any]) -> str:
    lines = [
        "# 已确认 COV 定向检索评测",
        "",
        f"- 状态：`{evidence['status']}`",
        f"- 模式：`{evidence['mode']}`；真实模型推理：`{str(evidence['real_model_inference']).lower()}`",
        f"- 查询数：{evidence['query_count']}；标签状态：`{evidence['label_status']}`",
        "- 该集合是冻结规则后的定向开发集，不是独立人工测试集；不得直接写成生产效果或简历独立测试集成绩。",
        f"- `numeric_resume_ready`：`{str(evidence['numeric_resume_ready']).lower()}`（本套件固定为 false）",
        "",
        "Recall@5 是每条查询 Top-5 命中的正相关 Chunk 数除以该查询全部已标注正相关 Chunk 数，再做宏平均；MRR@5 是 Top-5 中首个正相关排名的倒数，未命中为 0。",
        "向量、BM25、RRF 与 Reranker 共用同一授权语料、查询、标签、候选窗口（每路 20）、RRF 常数 60、重排窗口 20 和最终 K=5。",
        "",
        "## 四路基线",
        "",
        "| 方案 | N | Recall@5 | MRR@5 | 相对向量 Recall（百分点） | 相对向量 MRR（百分点） | 错误 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    baseline = next(
        item for item in evidence["baseline"]["summaries"] if item["channel"] == "vector"
    )
    base_recall = float(baseline["recall_at_k"].get("5", baseline["recall_at_k"].get(5)))
    base_mrr = float(baseline["mrr_at_k"])
    for summary in evidence["baseline"]["summaries"]:
        recall = float(summary["recall_at_k"].get("5", summary["recall_at_k"].get(5)))
        mrr = float(summary["mrr_at_k"])
        lines.append(
            f"| `{summary['channel']}` | {summary['case_count']} | {_pct(recall)} | {_pct(mrr)} | "
            f"{(recall - base_recall) * 100:+.2f} | {(mrr - base_mrr) * 100:+.2f} | {summary['error_count']} |"
        )
    lines += [
        "",
        "## 覆盖选择对比",
        "",
        "| 用例 | Reranked Recall@5 | 选择后 Recall@5 | Recall 差值（百分点） | Reranked MRR@5 | 选择后 MRR@5 | MRR 差值（百分点） | 是否替换 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in evidence["coverage"]["cases"]:
        before = row["baseline_metrics"]
        after = row["coverage_metrics"]
        if before is None or after is None:
            lines.append(f"| `{row['case_id']}` | — | — | — | — | — | — | error |")
            continue
        lines.append(
            f"| `{row['case_id']}` | {_pct(before['recall_at_5'])} | {_pct(after['recall_at_5'])} | "
            f"{(after['recall_at_5'] - before['recall_at_5']) * 100:+.2f} | {_pct(before['mrr_at_5'])} | "
            f"{_pct(after['mrr_at_5'])} | {(after['mrr_at_5'] - before['mrr_at_5']) * 100:+.2f} | "
            f"{'是' if row['changed'] else '否'} |"
        )
    summary = evidence["coverage"].get("summary")
    if summary:
        lines += [
            "",
            f"覆盖选择宏平均（N={summary['effective_n']}）：Recall@5={_pct(summary['recall_at_5'])}，MRR@5={_pct(summary['mrr_at_5'])}，nDCG@5={_pct(summary['ndcg_at_5'])}。",
        ]
    lines += [
        "",
        "## 复现边界",
        "",
        "候选覆盖率只表示正例是否进入同一 Top-20 混合候选池；选择器只读取制度内容派生的固定链接规则，不读取查询或标签。标签只在选择完成后用于计算指标。",
        "若模型、语料、冻结查询、规则、权限快照或依赖发生漂移，命令会 fail-closed 并写入 `failure.json`，不会回退到 offline 分数。",
        "",
    ]
    return "\n".join(lines)


def _protocol(
    inputs: ConfirmedInputs,
    *,
    mode: str,
    lock: dict[str, Any] | None,
    dataset_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "suite_name": "enterprise_policy_agent_retrieval_coverage_confirmed",
        "scope": "targeted_validation_not_independent_test",
        "label_status": TARGET_LABEL_STATUS,
        "numeric_resume_ready": False,
        "input": {
            "review_freeze_sha256": canonical_digest(inputs.freeze),
            "confirmed_review_sha256": canonical_digest(inputs.review),
            "user_confirmation_sha256": canonical_digest(inputs.confirmation),
            "query_set_sha256": canonical_digest(inputs.queries),
            "corpus_snapshot_sha256": canonical_digest(inputs.corpus),
            "rules_sha256": canonical_digest(inputs.rules),
            "selector_sha256_lf": _text_digest(SELECTOR),
            "dataset_sha256": dataset_sha256,
        },
        "mode": mode,
        "models": {
            "lock_sha256": canonical_digest(lock) if lock else None,
            "real_model_inference_required": mode == "bge",
        },
        "configuration": {
            "candidate_k_per_channel": 20,
            "rrf_rank_constant": 60,
            "rerank_window": 20,
            "final_k": 5,
            "selector": {"anchor_top_k": 2, "preserve_top_k": 4, "max_supplements": 1},
            "as_of_date": inputs.corpus.get("as_of_date"),
            "access_context": inputs.corpus.get("access_context"),
            "warmups": None,
            "measured_repetitions": 1,
            "latency_for_resume": False,
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-dir", type=Path, default=DEFAULT_REVIEW_DIR)
    parser.add_argument("--model-lock", type=Path, default=DEFAULT_MODEL_LOCK)
    parser.add_argument("--mode", choices=("bge", "offline"), default="bge")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args(argv)


def run(args: argparse.Namespace, output: Path) -> int:
    if args.threads < 1 or args.warmups < 0:
        raise ValueError("threads must be positive and warmups must be nonnegative")
    inputs = load_confirmed_inputs(args.review_dir)

    # Imports are intentionally delayed: validation and unit tests remain usable
    # without torch/sentence-transformers, while bge mode fails closed if absent.
    from app.evaluation.retrieval_evidence import (
        CandidateTracingRetriever,
        corpus_evidence,
        environment_evidence,
        git_identity,
        load_locked_models,
        source_manifest,
        write_json,
    )
    from app.evaluation.retrieval_models import RetrievalCase, RetrievalEvaluationMode
    from app.evaluation.retrieval_reporting import write_retrieval_report
    from app.evaluation.retrieval_runner import RetrievalEvaluationRunner
    from app.evaluation.retrieval_runtime import build_retrieval_evaluation_runtime
    from app.rag.policy_chunker import chunk_policy_directory

    real = args.mode == "bge"
    before_source = source_manifest(ROOT)
    identity = git_identity(ROOT)
    environment = environment_evidence(
        real_models=real,
        device=args.device,
        threads=args.threads,
    )
    lock, snapshots = load_locked_models(args.model_lock) if real else (None, {})
    if real:
        _require(lock == inputs.freeze.get("model_lock"), "model lock differs from frozen review")

    raw_dataset = target_dataset_bytes(inputs.cases)
    dataset_sha256 = digest(raw_dataset)
    dataset_rows = build_target_dataset_rows(inputs.cases)
    runner_cases = tuple(RetrievalCase.model_validate(row) for row in dataset_rows)
    mode = RetrievalEvaluationMode(args.mode)
    runtime_kwargs: dict[str, Any] = {
        "policy_directory": ROOT / "data/policies",
        "cases": runner_cases,
        "mode": mode,
        "device": args.device,
        "candidate_k": 20,
        "embedding_batch_size": 32,
        "reranker_batch_size": 8,
    }
    if real:
        runtime_kwargs.update(
            embedding_model=snapshots["embedding"],
            reranker_model=snapshots["reranker"],
        )
    runtime = build_retrieval_evaluation_runtime(**runtime_kwargs)
    live_corpus = corpus_evidence(runtime.chunks)
    _require(
        live_corpus["full_chunk_manifest_sha256"] == inputs.corpus["full_chunk_manifest_sha256"],
        "live corpus differs from frozen full manifest",
    )
    _require(
        runtime.corpus_sha256 == inputs.runtime_corpus_sha256,
        "live runtime corpus fingerprint drift",
    )

    protocol = _protocol(inputs, mode=args.mode, lock=lock, dataset_sha256=dataset_sha256)
    protocol["configuration"]["warmups"] = args.warmups
    write_json(output / "source-manifest.json", before_source)
    write_json(output / "environment.json", environment)
    write_json(output / "protocol.json", protocol)
    write_json(output / "frozen-corpus.json", inputs.corpus)
    write_json(output / "corpus.json", live_corpus)
    write_json(output / "queries.json", inputs.queries)
    write_json(output / "review.user-confirmed.json", inputs.review)
    write_json(output / "user-confirmation.json", inputs.confirmation)
    write_json(output / "rules.json", inputs.rules)
    chunks_by_id = {chunk.chunk_id: chunk for chunk in runtime.chunks}
    write_json(
        output / "judgment-review.json",
        [
            {
                "case_id": case["case_id"],
                "query": case["query"],
                "labels": [
                    {
                        **judgment,
                        "text": chunks_by_id[judgment["chunk_id"]].retrieval_text,
                    }
                    for judgment in case["judgments"]
                ],
            }
            for case in inputs.cases
        ],
    )
    (output / "target-dataset.jsonl").write_bytes(raw_dataset)
    (output / "requirements-observed.txt").write_text(
        "\n".join(f"{name}=={version}" for name, version in environment["packages"].items()) + "\n",
        encoding="utf-8",
    )
    if lock:
        write_json(output / "models.lock.json", lock)

    providers = (
        {role: f"{model['repo_id']}@{model['revision']}" for role, model in lock["models"].items()}
        if lock
        else {"embedding": runtime.embedding_provider, "reranker": runtime.reranker_provider}
    )
    target = CandidateTracingRetriever(runtime.retriever, candidate_k=20)
    runner = RetrievalEvaluationRunner(
        retriever=target,
        evaluation_mode=mode,
        embedding_provider=providers["embedding"],
        reranker_provider=providers["reranker"],
        external_model_calls=real,
        dataset_sha256=dataset_sha256,
        corpus_sha256=runtime.corpus_sha256,
        candidate_k=20,
    )
    for warmup_number in range(args.warmups):
        warmup = runner.run(runner_cases)
        if any(summary.error_count for summary in warmup.summaries):
            write_json(
                output / "warmup-failure.json",
                {"warmup": warmup_number + 1, "report": warmup.model_dump(mode="json")},
            )
            raise RuntimeError("warmup failed; see warmup-failure.json")
    target.candidates.clear()
    print(
        f"Evaluating confirmed COV set: {len(runner_cases)} queries, mode={args.mode}", flush=True
    )
    measured = runner.run(runner_cases)
    public_report = remap_report_case_ids(measured, inputs.cases)
    baseline_dict = public_report.model_dump(mode="json")
    chunks_for_links = [
        chunk.model_dump(mode="json", exclude={"source_path", "metadata_source_path"})
        for chunk in runtime.chunks
    ]
    links = build_links(chunks_for_links, inputs.rules)
    rows, coverage_summary = compute_coverage_rows(
        report=baseline_dict,
        cases=inputs.cases,
        candidates=target.candidates,
        links=links,
        allowed=set(inputs.allowed_chunk_ids),
    )
    error_count = sum(summary["error_count"] for summary in baseline_dict["summaries"])
    status = "completed" if error_count == 0 else "incomplete_channel_errors"
    coverage_evidence = {
        "schema_version": "1.0",
        "suite_name": "enterprise_policy_agent_retrieval_coverage_confirmed",
        "status": status,
        "mode": args.mode,
        "real_model_inference": real and error_count == 0,
        "numeric_resume_ready": False,
        "label_status": TARGET_LABEL_STATUS,
        "independent_test_set": False,
        "query_count": len(inputs.cases),
        "judgment_count": sum(len(case["judgments"]) for case in inputs.cases),
        "dataset_sha256": dataset_sha256,
        "corpus_sha256": runtime.corpus_sha256,
        "frozen_full_chunk_manifest_sha256": inputs.corpus["full_chunk_manifest_sha256"],
        "source_sha256": canonical_digest(before_source),
        "protocol_sha256": canonical_digest(protocol),
        "environment_sha256": canonical_digest(environment),
        "model_lock_sha256": canonical_digest(lock) if lock else None,
        "git": identity,
        "configuration": protocol["configuration"],
        "baseline": baseline_dict,
        "coverage": {
            "rules_sha256": canonical_digest(inputs.rules),
            "links_sha256": canonical_digest(links),
            "links": links,
            "summary": coverage_summary,
            "cases": rows,
        },
        "latency_claim": None,
        "runtime_backend_changed": False,
        "sqlite_data_migrated": False,
    }
    after_source = source_manifest(ROOT)
    after_corpus = corpus_evidence(chunk_policy_directory(ROOT / "data/policies"))
    _require(before_source == after_source, "source files changed during evaluation")
    _require(
        after_corpus["full_chunk_manifest_sha256"] == live_corpus["full_chunk_manifest_sha256"],
        "corpus changed during evaluation",
    )
    # Only publish measured scores after all source and corpus identity checks
    # pass; a drifted run must leave failure evidence, not an ambiguous report.
    write_retrieval_report(public_report, output)
    write_json(output / "coverage-report.json", coverage_evidence)
    (output / "coverage-report.md").write_text(
        render_coverage_markdown(coverage_evidence), encoding="utf-8"
    )
    summary = {
        "status": status,
        "mode": args.mode,
        "real_model_inference": coverage_evidence["real_model_inference"],
        "numeric_resume_ready": False,
        "query_count": len(inputs.cases),
        "report": str(output / "coverage-report.json"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if status == "completed" else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output = args.output_dir or ROOT / "artifacts" / (
        f"retrieval-coverage-confirmed-{args.mode}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
    )
    created = False
    try:
        output.mkdir(parents=True, exist_ok=False)
        created = True
        return run(args, output)
    except Exception as exc:  # noqa: BLE001 - CLI failure is evidence, never a fallback score
        failure = {
            "schema_version": "1.0",
            "status": "blocked",
            "phase": "confirmed_targeted_retrieval_evaluation",
            "mode": args.mode,
            "real_model_inference_completed": False,
            "numeric_resume_ready": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        if created:
            _write_json(output / "failure.json", failure)
        print(json.dumps(failure, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
