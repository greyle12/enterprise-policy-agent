"""Validate a label-free semantic Facet holdout candidate package.

This checker deliberately stops before human labels, model inference, or scoring.
It validates the question-only payload, the empty double-annotation template, and
an exact/near-duplicate screen against JSON and JSONL query fields already present
in the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

DATASET_VERSION = "semantic-facet-holdout-v1-candidate-1"
FOLDER = Path("docs/gate_v3/semantic-facet-holdout-v1-candidate")
EXPECTED_SAMPLE_COUNT = 18
QUESTION_KEYS = frozenset({"case_id", "query"})
ANNOTATION_KEYS = frozenset({"status", "accepted_facets", "rationale"})
CASE_KEYS = frozenset({"case_id", "annotator_a", "annotator_b", "adjudication"})
NEAR_THRESHOLD = 0.72


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized(text: str) -> str:
    """Normalize punctuation and spacing for conservative duplicate screening."""

    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text)).casefold()


def load_questions(path: Path) -> list[dict[str, str]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def validate_questions(questions: list[dict[str, str]]) -> None:
    if len(questions) != EXPECTED_SAMPLE_COUNT:
        raise ValueError(f"expected {EXPECTED_SAMPLE_COUNT} questions")

    ids = [question.get("case_id") for question in questions]
    if len(set(ids)) != len(ids):
        raise ValueError("question IDs must be unique")
    if any(set(question) != QUESTION_KEYS for question in questions):
        raise ValueError("questions must contain only case_id and query")
    if any(
        not isinstance(question["case_id"], str)
        or not re.fullmatch(r"SHV-\d{3}", question["case_id"])
        for question in questions
    ):
        raise ValueError("question IDs must use SHV-NNN format")
    queries = [question["query"] for question in questions]
    if any(
        not isinstance(query, str) or not query.strip() or query != query.strip()
        for query in queries
    ):
        raise ValueError("queries must be non-empty strings without outer whitespace")
    normalized_queries = [normalized(query) for query in queries]
    if len(set(normalized_queries)) != len(normalized_queries):
        raise ValueError("question queries must be unique after normalization")


def _validate_empty_annotation(value: Any, label: str) -> None:
    if not isinstance(value, dict) or set(value) != ANNOTATION_KEYS:
        raise ValueError(f"{label} has an invalid annotation shape")
    if value["status"] != "pending":
        raise ValueError(f"{label} must remain pending")
    if value["accepted_facets"] is not None or value["rationale"] is not None:
        raise ValueError(f"{label} must not contain labels or rationale")


def validate_annotation_template(questions: list[dict[str, str]], template: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "dataset_version",
        "status",
        "sample_count",
        "independent_test_set",
        "numeric_resume_ready",
        "model_inference",
        "independent_double_annotation",
        "cases",
    }
    if set(template) != required:
        raise ValueError("annotation template has unexpected or missing top-level fields")
    if template["schema_version"] != "1.0":
        raise ValueError("unsupported annotation template schema")
    if template["dataset_version"] != DATASET_VERSION:
        raise ValueError("annotation template dataset version mismatch")
    if template["status"] != "pending_double_annotation":
        raise ValueError("annotation template must be pending_double_annotation")
    if template["sample_count"] != len(questions) or len(template["cases"]) != len(questions):
        raise ValueError("annotation template sample count mismatch")
    if any(
        template[field] is not False
        for field in (
            "independent_test_set",
            "numeric_resume_ready",
            "model_inference",
            "independent_double_annotation",
        )
    ):
        raise ValueError("annotation boundary flags must remain false")

    question_ids = [question["case_id"] for question in questions]
    template_ids = [case.get("case_id") for case in template["cases"]]
    if template_ids != question_ids:
        raise ValueError("annotation template IDs are not aligned with questions")
    for case in template["cases"]:
        if set(case) != CASE_KEYS:
            raise ValueError(f"invalid annotation case shape: {case.get('case_id')}")
        _validate_empty_annotation(case["annotator_a"], f"{case['case_id']}/annotator_a")
        _validate_empty_annotation(case["annotator_b"], f"{case['case_id']}/annotator_b")
        _validate_empty_annotation(case["adjudication"], f"{case['case_id']}/adjudication")


def _read_json_values(path: Path) -> Iterable[Any]:
    raw = path.read_text(encoding="utf-8-sig")
    if path.suffix == ".jsonl":
        for line in raw.splitlines():
            if line.strip():
                yield json.loads(line)
    else:
        yield json.loads(raw)


def _extract_queries(value: Any, source: str) -> Iterable[dict[str, str]]:
    if isinstance(value, dict):
        query = value.get("query")
        if isinstance(query, str) and query.strip():
            case_id = value.get("case_id")
            yield {
                "case_id": case_id if isinstance(case_id, str) else source,
                "query": query,
                "source": source,
            }
        for child in value.values():
            yield from _extract_queries(child, source)
    elif isinstance(value, list):
        for child in value:
            yield from _extract_queries(child, source)


def collect_query_corpus(
    root: Path, excluded_folder: Path
) -> tuple[list[dict[str, str]], list[dict]]:
    """Collect query-bearing JSON/JSONL records and their file hashes."""

    corpus: list[dict[str, str]] = []
    sources: list[dict] = []
    invalid_files: list[dict[str, str]] = []
    source_roots = [root / "docs/gate_v3", root / "artifacts"]
    for source_root in source_roots:
        if not source_root.exists():
            continue
        for path in sorted(source_root.rglob("*")):
            if (
                not path.is_file()
                or path.suffix not in {".json", ".jsonl"}
                or path.is_relative_to(excluded_folder)
            ):
                continue
            relative = path.relative_to(root).as_posix()
            try:
                values = list(_read_json_values(path))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                invalid_files.append({"path": relative, "error": type(exc).__name__})
                continue
            rows = list(_extract_queries(values, relative))
            if rows:
                sources.append({"path": relative, "sha256": digest(path), "query_count": len(rows)})
                corpus.extend(rows)
    return corpus, [{"files": sources, "invalid_files": invalid_files}]


def duplicates(
    questions: list[dict[str, str]], corpus: list[dict[str, str]], threshold: float = NEAR_THRESHOLD
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    exact: list[dict[str, Any]] = []
    near: list[dict[str, Any]] = []
    for question in questions:
        left = normalized(question["query"])
        for other in corpus:
            right = normalized(other["query"])
            score = SequenceMatcher(None, left, right).ratio()
            match = {
                "case_id": question["case_id"],
                "other_case_id": other["case_id"],
                "other_query": other["query"],
                "source": other["source"],
                "similarity": round(score, 6),
            }
            if left == right:
                exact.append(match)
            elif score >= threshold:
                near.append(match)
    return exact, near


def check_package(root: Path) -> dict[str, Any]:
    root = root.resolve()
    folder = root / FOLDER
    question_path = folder / "questions.jsonl"
    annotation_path = folder / "annotations.template.json"
    questions = load_questions(question_path)
    template = json.loads(annotation_path.read_text(encoding="utf-8"))
    validate_questions(questions)
    validate_annotation_template(questions, template)
    corpus, source_groups = collect_query_corpus(root, folder)
    sources = source_groups[0]["files"]
    invalid_files = source_groups[0]["invalid_files"]
    exact, near = duplicates(questions, corpus)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "dataset_version": DATASET_VERSION,
        "status": "passed" if not exact else "failed",
        "scope": "label_free_holdout_candidate_structure_and_exact_overlap_screen",
        "sample_count": len(questions),
        "question_ids": [question["case_id"] for question in questions],
        "question_sha256": digest(question_path),
        "annotation_template_sha256": digest(annotation_path),
        "source_scope": [
            "docs/gate_v3/**/*.json",
            "docs/gate_v3/**/*.jsonl",
            "artifacts/**/*.json",
            "artifacts/**/*.jsonl",
        ],
        "source_file_count": len(sources),
        "corpus_query_count": len(corpus),
        "invalid_source_file_count": len(invalid_files),
        "invalid_source_files": invalid_files,
        "exact_overlap_count": len(exact),
        "exact_overlaps": exact,
        "near_match_count": len(near),
        "near_matches": near,
        "near_threshold": NEAR_THRESHOLD,
        "near_method": "NFKC, remove punctuation/whitespace, SequenceMatcher; near matches require human review",
        "source_files": sources,
        "code_head": head,
        "validator_sha256": digest(Path(__file__)),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "dependencies": "standard-library-only",
        "model_inference": False,
        "model_id": None,
        "model_revision": None,
        "retrieval_executed": False,
        "parser_runtime_integration": False,
        "independent_double_annotation": False,
        "independent_test_set": False,
        "numeric_resume_ready": False,
        "authoring_boundary": "AI-assisted candidate; it requires two fresh human annotations and adjudication before freeze or model evaluation.",
        "label_policy": "No accepted labels, model predictions, or semantic score are present in this package.",
    }
    (folder / "validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest = {
        "schema_version": "1.0",
        "dataset_version": DATASET_VERSION,
        "status": "candidate_pending_double_annotation",
        "sample_count": len(questions),
        "independent_test_set": False,
        "numeric_resume_ready": False,
        "model_inference": False,
        "source_files": [
            {"path": item["path"], "sha256": item["sha256"], "query_count": item["query_count"]}
            for item in sources
        ],
        "deliverables": [
            {"path": question_path.relative_to(root).as_posix(), "sha256": digest(question_path)},
            {
                "path": annotation_path.relative_to(root).as_posix(),
                "sha256": digest(annotation_path),
            },
            {
                "path": (folder / "annotation_guide.md").relative_to(root).as_posix(),
                "sha256": digest(folder / "annotation_guide.md"),
            },
            {
                "path": (folder / "validation.json").relative_to(root).as_posix(),
                "sha256": digest(folder / "validation.json"),
            },
        ],
        "question_sha256": digest(question_path),
        "annotation_template_sha256": digest(annotation_path),
        "validation_sha256": digest(folder / "validation.json"),
        "validator_sha256": digest(Path(__file__)),
        "code_head": head,
        "model_visible_fields": ["case_id", "query"],
        "model_visible_files": [question_path.relative_to(root).as_posix()],
        "human_only_files": [
            annotation_path.relative_to(root).as_posix(),
            (folder / "annotation_guide.md").relative_to(root).as_posix(),
        ],
    }
    (folder / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    args = parser.parse_args()
    report = check_package(args.project_root)
    print(
        json.dumps(
            {
                "status": report["status"],
                "dataset_version": report["dataset_version"],
                "sample_count": report["sample_count"],
                "corpus_query_count": report["corpus_query_count"],
                "exact_overlap_count": report["exact_overlap_count"],
                "near_match_count": report["near_match_count"],
                "independent_test_set": report["independent_test_set"],
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(1 if report["status"] == "failed" else 0)


if __name__ == "__main__":
    main()
