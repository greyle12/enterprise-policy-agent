"""Replay captured real-model rankings; no model inference or runtime wiring.

This intentionally standard-library-only audit consumes the existing evidence
format. Judgments are used ONLY after selection, never to build/select links.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import subprocess
import sys
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES = ROOT / "tests/evaluation/retrieval_coverage_rules.json"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_digest(value: object) -> str:
    return digest(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def unique_ids(ids: list[str], allowed: set[str], limit: int) -> None:
    require(len(ids) <= limit and len(set(ids)) == len(ids), "Invalid ranking length/duplicate ID")
    require(set(ids) <= allowed, "Ranking contains unknown or unauthorized chunk")


def read_archive(path: Path) -> dict[str, bytes]:
    """Read both PowerShell and POSIX ZIP names, without extracting any paths."""
    result = {}
    with zipfile.ZipFile(path) as archive:
        require(
            sum(i.file_size for i in archive.infolist()) <= 64_000_000, "Evidence ZIP too large"
        )
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            if info.is_dir() or name.endswith("/"):
                continue
            parts = PurePosixPath(name).parts
            require(
                bool(parts) and not name.startswith("/") and ".." not in parts and ":" not in name,
                "Unsafe archive member",
            )
            normalized = str(PurePosixPath(name))
            require(normalized not in result, "Duplicate normalized archive member")
            result[normalized] = archive.read(info)
    return result


def measure(ids: list[str], judgments: list[dict]) -> dict[str, float]:
    """Same macro-query definitions as retrieval_runner; checked against its report."""
    grades = {j["chunk_id"]: j["relevance"] for j in judgments}
    require(len(grades) == len(judgments) and bool(grades), "Empty/duplicate judgments")
    require(all(type(g) is int and g in (1, 2, 3) for g in grades.values()), "Invalid grade")
    top = ids[:5]
    require(len(set(top)) == len(top), "Duplicate retrieved ID")
    relevant = set(grades)
    rr = next((1 / rank for rank, key in enumerate(top, 1) if key in relevant), 0.0)
    dcg = sum(
        (2 ** grades.get(key, 0) - 1) / math.log2(rank + 1) for rank, key in enumerate(top, 1)
    )
    ideal = sum(
        (2**g - 1) / math.log2(rank + 1)
        for rank, g in enumerate(sorted(grades.values(), reverse=True)[:5], 1)
    )
    return {
        "recall_at_5": len(set(top) & relevant) / len(relevant),
        "mrr_at_5": rr,
        "ndcg_at_5": dcg / ideal,
    }


def build_links(chunks: list[dict], rules: dict) -> list[dict]:
    """Content-derived, same-document/version links. No queries or labels accepted."""
    require(rules["schema_version"] == "1.0", "Unsupported rule schema")
    require(
        rules["selection"]
        == {"anchor_top_k": 2, "preserve_top_k": 4, "final_k": 5, "max_supplements": 1},
        "Unsupported selection configuration",
    )
    require(len({r["id"] for r in rules["rules"]}) == len(rules["rules"]), "Duplicate rule ID")
    links = []
    for source in sorted(chunks, key=lambda c: c["chunk_id"]):
        for rule in rules["rules"]:
            if rule["source_title_contains"] not in source["article_title"] or not all(
                t in source["content"] for t in rule["source_content_contains"]
            ):
                continue
            targets = [
                c
                for c in chunks
                if c["document_id"] == source["document_id"]
                and c["document_version"] == source["document_version"]
                and c["article_title"] == rule["target_title"]
                and all(t in c["content"] for t in rule["target_content_contains"])
                and c["chunk_id"] != source["chunk_id"]
            ]
            require(len(targets) <= 1, "Ambiguous policy dependency; review corpus")
            for target in targets:
                links.append(
                    {
                        "rule_id": rule["id"],
                        "source": source["chunk_id"],
                        "target": target["chunk_id"],
                        "rationale": rule["rationale"],
                        "source_text_sha256": digest(source["content"].encode()),
                        "target_text_sha256": digest(target["content"].encode()),
                    }
                )
    return links


def select_coverage(
    baseline: list[str], candidates: list[str], allowed: set[str], links: list[dict]
) -> tuple[list[str], list[dict]]:
    """Keep ranks 1-4; at most one linked candidate replaces/appends rank 5."""
    unique_ids(candidates, allowed, 20)
    unique_ids(baseline, set(candidates), 5)
    events = []
    for anchor in baseline[:2]:
        for link in links:
            if link["source"] != anchor:
                continue
            target = link["target"]
            event = {"rule_id": link["rule_id"], "anchor": anchor, "target": target}
            if target in baseline:
                events.append({**event, "action": "already_present"})
            elif target not in candidates or target not in allowed:
                events.append({**event, "action": "outside_authorized_candidate_window"})
            else:
                selected = baseline[:4] + [target]
                events.append({**event, "action": "supplemented", "evicted": baseline[4:]})
                return selected, events
    return list(baseline), events


def summarize(rows: list[dict], key: str) -> dict:
    require(bool(rows), "Empty cohort")
    return {
        "effective_n": len(rows),
        **{
            metric: sum(r[key][metric] for r in rows) / len(rows)
            for metric in ("recall_at_5", "mrr_at_5", "ndcg_at_5")
        },
    }


def evaluate(files: dict[str, bytes], rules: dict) -> dict:
    evidence = json.loads(files["evidence.json"])
    corpus = json.loads(files["corpus.json"])
    require(
        evidence["mode"] == "bge"
        and evidence["status"] == "completed"
        and evidence["real_model_inference"] is True,
        "Requires completed real BGE evidence",
    )
    for key, filename in (
        ("source_sha256", "source-manifest.json"),
        ("protocol_sha256", "protocol.json"),
    ):
        require(
            canonical_digest(json.loads(files[filename])) == evidence[key],
            f"{filename} fingerprint mismatch",
        )
    require(json.loads(files["models.lock.json"]) == evidence["model_lock"], "Model lock mismatch")
    chunks = corpus["chunks"]
    require(
        canonical_digest(chunks)
        == evidence["corpus_sha256"]
        == corpus["full_chunk_manifest_sha256"],
        "Corpus fingerprint mismatch",
    )
    all_ids = {c["chunk_id"] for c in chunks}
    allowed = set(corpus["allowed_chunk_ids"])
    require(len(all_ids) == len(chunks) and allowed <= all_ids, "Invalid corpus IDs/ACL snapshot")
    as_of = date.fromisoformat(corpus["as_of_date"])
    for chunk in chunks:
        if chunk["chunk_id"] in allowed:
            require(
                chunk["document_status"] == "effective"
                and date.fromisoformat(chunk["effective_date"]) <= as_of
                and (not chunk["expiry_date"] or as_of <= date.fromisoformat(chunk["expiry_date"])),
                "Inactive chunk in authorized snapshot",
            )
    config = evidence["configuration"]
    require(
        (
            config["candidate_k_per_channel"],
            config["rerank_window"],
            config["rrf_rank_constant"],
            config["final_k"],
        )
        == (20, 20, 60, 5),
        "Requires fixed 20/20/60/5 baseline",
    )
    links = build_links(chunks, rules)
    cohorts = []
    require(
        bool(evidence["cohorts"])
        and len({c["name"] for c in evidence["cohorts"]}) == len(evidence["cohorts"]),
        "Empty/duplicate cohort",
    )
    for cohort in evidence["cohorts"]:
        name = cohort["name"]
        raw = files[f"{name}/dataset.jsonl"]
        require(digest(raw) == cohort["raw_dataset_sha256"], "Dataset fingerprint mismatch")
        dataset = [
            json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()
        ]
        report = json.loads(files[f"{name}/retrieval-evaluation-report.json"])
        require(
            report["dataset_sha256"] == digest(raw) and report["evaluation_mode"] == "bge",
            "Baseline provenance mismatch",
        )
        for role, field in (("embedding", "embedding_provider"), ("reranker", "reranker_provider")):
            model = evidence["model_lock"]["models"][role]
            require(
                report[field] == f"{model['repo_id']}@{model['revision']}",
                "Baseline model mismatch",
            )
        require(
            len(dataset)
            == report["total_cases"]
            == cohort["case_count"]
            == len(report["case_results"]),
            "Case count mismatch",
        )
        require(
            len({c["case_id"] for c in dataset}) == len(dataset)
            and len({c["query"] for c in dataset}) == len(dataset),
            "Duplicate query/case ID",
        )
        rows = []
        for case, captured in zip(dataset, report["case_results"], strict=True):
            require(
                all(case[k] == captured[k] for k in ("case_id", "query", "judgments")),
                "Case/label drift",
            )
            require(
                {j["chunk_id"] for j in case["judgments"]} <= allowed, "Invalid/invisible judgment"
            )
            by_channel = {c["channel"]: c for c in captured["channels"]}
            require(
                set(by_channel) == {"vector", "bm25", "hybrid", "reranked"}
                and len(captured["channels"]) == 4,
                "Invalid baseline channels",
            )
            pool = cohort["analysis"]["candidates"][case["query"]]["hybrid"]
            unique_ids(pool, allowed, 20)
            metrics = {}
            for channel, channel_result in by_channel.items():
                require(
                    channel_result["error"] is None,
                    "Baseline contains errors; do not report a completed ablation",
                )
                ids = channel_result["retrieved_chunk_ids"]
                unique_ids(ids, allowed, 5)
                metrics[channel] = measure(ids, case["judgments"])
                require(
                    math.isclose(
                        metrics[channel]["recall_at_5"], channel_result["recall_at_k"]["5"]
                    )
                    and math.isclose(
                        metrics[channel]["mrr_at_5"], channel_result["reciprocal_rank"]
                    )
                    and math.isclose(
                        metrics[channel]["ndcg_at_5"], channel_result["ndcg_at_k"]["5"]
                    ),
                    "Baseline metric mismatch",
                )
            require(
                by_channel["hybrid"]["retrieved_chunk_ids"] == pool[:5],
                "Captured RRF ranking mismatch",
            )
            baseline = by_channel["reranked"]["retrieved_chunk_ids"]
            selected, events = select_coverage(baseline, pool, allowed, links)
            relevant = {j["chunk_id"] for j in case["judgments"]}
            rows.append(
                {
                    "case_id": case["case_id"],
                    "query": case["query"],
                    "baseline_ids": baseline,
                    "selected_ids": selected,
                    "candidate_ids": pool,
                    **metrics,
                    "coverage": measure(selected, case["judgments"]),
                    "events": events,
                    "missing_after": sorted(relevant - set(selected)),
                    "gained_judged_ids": sorted(relevant & (set(selected) - set(baseline))),
                    "lost_judged_ids": sorted(relevant & (set(baseline) - set(selected))),
                    "changed": baseline != selected,
                    "candidate_recall": len(relevant & set(pool)) / len(relevant),
                }
            )
        summaries = {
            key: summarize(rows, key)
            for key in ("vector", "bm25", "hybrid", "reranked", "coverage")
        }
        for summary in report["summaries"]:
            actual = summaries[summary["channel"]]
            require(
                math.isclose(actual["recall_at_5"], summary["recall_at_k"]["5"])
                and math.isclose(actual["mrr_at_5"], summary["mrr_at_k"]),
                "Aggregate baseline mismatch",
            )
        cohorts.append(
            {
                "name": name,
                "label_status": cohort["label_status"],
                "dataset_sha256": digest(raw),
                "summaries": summaries,
                "candidate_recall": sum(r["candidate_recall"] for r in rows) / len(rows),
                "changed_count": sum(r["changed"] for r in rows),
                "recall_improved_count": sum(
                    r["coverage"]["recall_at_5"] > r["reranked"]["recall_at_5"] for r in rows
                ),
                "regression_case_ids": [
                    r["case_id"]
                    for r in rows
                    if any(
                        r["coverage"][m] < r["reranked"][m]
                        for m in ("recall_at_5", "mrr_at_5", "ndcg_at_5")
                    )
                ],
                "cases": rows,
            }
        )
    return {
        "schema_version": "1.0",
        "status": "completed",
        "mode": "recorded_real_bge_postprocessing_replay",
        "fresh_model_inference": False,
        "numeric_resume_ready": False,
        "scope": "Post-hoc development ablation; rules informed by observed failures; no held-out claim",
        "baseline_git": evidence["git"],
        "baseline_source_sha256": evidence["source_sha256"],
        "corpus_sha256": evidence["corpus_sha256"],
        "model_lock": evidence["model_lock"],
        "configuration": config,
        "access_context": corpus["access_context"],
        "as_of_date": corpus["as_of_date"],
        "rules": rules,
        "rules_sha256": canonical_digest(rules),
        "derived_links": links,
        "cohorts": cohorts,
        "runtime_changed": False,
        "latency_claim": None,
    }


def markdown(report: dict) -> str:
    lines = [
        "# 补充证据覆盖消融实验",
        "",
        "模式：真实 BGE 已保存排名的确定性后处理重放；本次无模型推理。",
        "规则在观察失败案例后设计，未经独立人工复核；结果仅为开发探索，不新增简历成绩。",
        "前四名保持不变；仅第五位可补入同制度同版本、已授权 RRF Top-20 中的关联条款。",
        "Recall@5 按查询宏平均，分母为全部已标注正相关 Chunk；Grade 1/2/3 均为正相关。",
        "MRR@5 为首个正相关排名倒数的宏平均；另报告分级 nDCG@5 以检测排序退化。",
        "无答案、无权限、无标注样本不在原正例套件内；缺标签或输入错误直接阻止实验。未标注不等于无关。",
        "权限/有效期沿用历史快照，不能作为当前权限校验或在线端到端验收。",
        "",
    ]
    for cohort in report["cohorts"]:
        lines += [
            f"## {cohort['name']}",
            "",
            cohort["label_status"],
            "",
            "|方案|N|Recall@5|MRR@5|nDCG@5|",
            "|---|---:|---:|---:|---:|",
        ]
        for name, value in cohort["summaries"].items():
            lines.append(
                f"|{name}|{value['effective_n']}|{value['recall_at_5']:.2%}|{value['mrr_at_5']:.4f}|{value['ndcg_at_5']:.4f}|"
            )
        lines += [
            "",
            f"改变 {cohort['changed_count']} 条；Recall 提升 {cohort['recall_improved_count']} 条；任一指标退化：{cohort['regression_case_ids']}。",
            "",
            "全部改动或未满召回/首相关非第一案例：",
            "",
        ]
        for row in cohort["cases"]:
            if (
                row["changed"]
                or row["coverage"]["recall_at_5"] < 1
                or row["coverage"]["mrr_at_5"] < 1
            ):
                lines.append(
                    f"- {row['case_id']}：{row['query']}；Recall {row['reranked']['recall_at_5']:.3f} → {row['coverage']['recall_at_5']:.3f}；补入正例 {row['gained_judged_ids']}；移出正例 {row['lost_judged_ids']}；仍缺 {row['missing_after']}；事件 {json.dumps(row['events'], ensure_ascii=False)}"
                )
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-zip", type=Path, required=True)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    try:
        files = read_archive(args.baseline_zip)
        rules = json.loads(args.rules.read_text(encoding="utf-8-sig"))
        report = evaluate(files, rules)
        report.update(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "baseline_zip_sha256": digest(args.baseline_zip.read_bytes()),
                "experiment_script_sha256": digest(Path(__file__).read_bytes()),
                "python": sys.version,
                "platform": platform.platform(),
            }
        )
        try:
            report["experiment_git_head"] = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            report["experiment_git_head"] = None
        (args.output_dir / "coverage-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (args.output_dir / "coverage-report.md").write_text(
            markdown(report) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "status": "completed",
                    "fresh_model_inference": False,
                    "numeric_resume_ready": False,
                    "report": str(args.output_dir / "coverage-report.json"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile) as exc:
        failure = {
            "status": "blocked",
            "numeric_resume_ready": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        (args.output_dir / "failure.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(failure, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
