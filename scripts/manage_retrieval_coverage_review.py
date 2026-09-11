"""Freeze the existing selector and prepare blind human relevance review.

No retrieval or model calls. Review assertions record human declarations;
software cannot certify reviewer identity or judgment completeness.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from scripts.run_retrieval_coverage_ablation import (
    DEFAULT_RULES,
    ROOT,
    canonical_digest,
    digest,
    read_archive,
    require,
)

QUERIES = ROOT / "tests/evaluation/retrieval_coverage_review_queries.json"
SELECTOR = ROOT / "scripts/run_retrieval_coverage_ablation.py"


def text_digest(data: bytes) -> str:
    return digest(data.replace(b"\r\n", b"\n"))


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def prepare(baseline: Path, output: Path) -> dict:
    files = read_archive(baseline)
    evidence = json.loads(files["evidence.json"])
    corpus = json.loads(files["corpus.json"])
    require(
        evidence["status"] == "completed"
        and evidence["mode"] == "bge"
        and evidence["real_model_inference"] is True,
        "Requires completed real BGE baseline",
    )
    require(
        canonical_digest(corpus["chunks"])
        == evidence["corpus_sha256"]
        == corpus["full_chunk_manifest_sha256"],
        "Corpus fingerprint mismatch",
    )
    proposals = json.loads(QUERIES.read_text(encoding="utf-8-sig"))
    require(
        len({c["case_id"] for c in proposals["cases"]}) == len(proposals["cases"]) > 0,
        "Duplicate/empty cases",
    )
    queries = [c["query"].strip() for c in proposals["cases"]]
    require(all(queries) and len(set(queries)) == len(queries), "Duplicate/blank queries")
    old_queries = {
        c["query"].strip()
        for cohort in evidence["cohorts"]
        for line in files[f"{cohort['name']}/dataset.jsonl"].decode("utf-8-sig").splitlines()
        if line.strip()
        for c in [json.loads(line)]
    }
    require(
        not set(queries) & old_queries,
        "Exact old-query duplicate; semantic independence is not guaranteed",
    )
    rules = json.loads(DEFAULT_RULES.read_text(encoding="utf-8-sig"))
    require(
        rules["selection"]
        == {"anchor_top_k": 2, "preserve_top_k": 4, "final_k": 5, "max_supplements": 1},
        "Unexpected selector contract",
    )
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        head = None
    lock = {
        "schema_version": "1.0",
        "created_at": datetime.now(UTC).isoformat(),
        "git_head": head,
        "baseline_zip_sha256": digest(baseline.read_bytes()),
        "baseline_git": evidence["git"],
        "corpus_snapshot_sha256": canonical_digest(corpus),
        "query_set_sha256": canonical_digest(proposals),
        "rules_sha256": canonical_digest(rules),
        "selector_sha256_lf": text_digest(SELECTOR.read_bytes()),
        "model_lock": evidence["model_lock"],
        "configuration": evidence["configuration"],
        "scope": "prospective_targeted_validation_not_independent_test",
        "rule_changes_after_freeze_allowed": False,
    }
    review = {
        "schema_version": "1.0",
        "freeze_sha256": canonical_digest(lock),
        "cases": [
            {
                **case,
                "answerability": "unreviewed",
                "judgments": [],
                "review": {
                    "status": "pending",
                    "reviewer": "",
                    "reviewed_at": "",
                    "checked_all_authorized_chunks": False,
                    "viewed_retrieval_outputs": False,
                    "notes": "",
                },
            }
            for case in proposals["cases"]
        ],
    }
    allowed = set(corpus["allowed_chunk_ids"])
    require(allowed <= {c["chunk_id"] for c in corpus["chunks"]}, "Invalid authorized snapshot")
    lines = [
        "# 人工相关性复核语料",
        "",
        "此文件仅含原授权语料，无模型排名、分数或建议标签。",
        "按每条查询检查全部条款；Grade 3=直接回答，2=支持解释，1=相关背景。仅主题相近不算相关。",
        "不要因条款属于规则的补充目标就自动标正例；结合具体问题独立判断。",
        "",
    ]
    for chunk in sorted(corpus["chunks"], key=lambda c: c["chunk_id"]):
        if chunk["chunk_id"] in allowed:
            lines.extend(
                [
                    f"## {chunk['chunk_id']}",
                    "",
                    f"{chunk['document_title']} / V{chunk['document_version']}",
                    "",
                    chunk["content"],
                    "",
                ]
            )
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "freeze.json", lock)
    write_json(output / "review.json", review)
    write_json(output / "queries.json", proposals)
    write_json(output / "corpus.json", corpus)
    write_json(output / "rules.json", rules)
    with (output / "corpus-review.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines))
    return {
        "status": "awaiting_human_review",
        "case_count": len(review["cases"]),
        "retrieval_executed": False,
        "numeric_resume_ready": False,
        "freeze_sha256": canonical_digest(lock),
    }


def validate(directory: Path) -> dict:
    def read(name: str) -> dict:
        return json.loads((directory / name).read_text(encoding="utf-8-sig"))

    lock, review, proposals, corpus = (
        read(name) for name in ("freeze.json", "review.json", "queries.json", "corpus.json")
    )
    require(review["freeze_sha256"] == canonical_digest(lock), "Freeze identity mismatch")
    require(
        canonical_digest(proposals)
        == lock["query_set_sha256"]
        == canonical_digest(json.loads(QUERIES.read_text(encoding="utf-8-sig"))),
        "Queries changed after freeze",
    )
    require(
        canonical_digest(corpus) == lock["corpus_snapshot_sha256"],
        "Corpus/ACL changed after freeze",
    )
    require(
        canonical_digest(read("rules.json"))
        == lock["rules_sha256"]
        == canonical_digest(json.loads(DEFAULT_RULES.read_text(encoding="utf-8-sig"))),
        "Rules changed after freeze",
    )
    require(
        text_digest(SELECTOR.read_bytes()) == lock["selector_sha256_lf"],
        "Selector changed after freeze",
    )
    require(len(review["cases"]) == len(proposals["cases"]), "Missing/extra review cases")
    allowed = set(corpus["allowed_chunk_ids"])
    issues = []
    for case, original in zip(review["cases"], proposals["cases"], strict=True):
        require(
            all(case.get(key) == value for key, value in original.items()),
            "Query/case order changed after freeze",
        )
        problems = []
        record = case["review"]
        if record.get("status") != "approved":
            problems.append("human approval required")
        if case.get("answerability") != "answerable":
            problems.append("answerability unresolved; do not silently exclude this case")
        if not isinstance(record.get("reviewer"), str) or not record["reviewer"].strip():
            problems.append("reviewer required")
        try:
            reviewed_at = datetime.fromisoformat(record["reviewed_at"])
            require(reviewed_at.utcoffset() is not None, "Review timestamp needs timezone")
            require(
                datetime.fromisoformat(lock["created_at"]) <= reviewed_at <= datetime.now(UTC),
                "Review timestamp outside freeze-to-now interval",
            )
        except (ValueError, TypeError, KeyError):
            problems.append("valid post-freeze review timestamp required")
        if record.get("checked_all_authorized_chunks") is not True:
            problems.append("full authorized corpus review declaration required")
        if record.get("viewed_retrieval_outputs") is not False:
            problems.append("retrieval outputs seen; cannot release as pre-inference review")
        judgments = case.get("judgments", [])
        require(
            isinstance(judgments, list) and all(isinstance(j, dict) for j in judgments),
            "Malformed judgments",
        )
        ids = [j.get("chunk_id") for j in judgments]
        if not ids or len(set(ids)) != len(ids) or not set(ids) <= allowed:
            problems.append("positive judgments required; IDs must be unique and authorized")
        for judgment in judgments:
            if type(judgment.get("relevance")) is not int or judgment["relevance"] not in (1, 2, 3):
                problems.append("grades must be 1/2/3")
            if not isinstance(judgment.get("rationale"), str) or not judgment["rationale"].strip():
                problems.append("per-judgment rationale required")
        if problems:
            issues.append({"case_id": case["case_id"], "problems": sorted(set(problems))})
    return {
        "schema_version": "1.0",
        "status": "blocked_pending_human_review"
        if issues
        else "review_ready_for_separate_evaluation",
        "passed": not issues,
        "case_count": len(review["cases"]),
        "blocked_case_count": len(issues),
        "issues": issues,
        "freeze_sha256": canonical_digest(lock),
        "review_sha256": canonical_digest(review),
        "retrieval_executed": False,
        "numeric_resume_ready": False,
        "review_identity_and_completeness": "human declarations, not independently certified",
        "scope": lock["scope"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    init = commands.add_parser("prepare")
    init.add_argument("--baseline-zip", type=Path, required=True)
    init.add_argument("--output-dir", type=Path, required=True)
    check = commands.add_parser("validate")
    check.add_argument("--review-dir", type=Path, required=True)
    check.add_argument(
        "--report",
        type=Path,
        required=True,
        help="New file; never overwrites review or prior results",
    )
    args = parser.parse_args(argv)
    try:
        if args.action == "prepare":
            result = prepare(args.baseline_zip, args.output_dir)
        else:
            result = validate(args.review_dir)
            write_json(args.report, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("passed") is False else 0
    except (ValueError, TypeError, KeyError, OSError, zipfile.BadZipFile) as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": str(exc), "numeric_resume_ready": False},
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
