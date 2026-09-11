"""Validate review-only data and report duplicate candidates without model calls."""

import hashlib
import json
import platform
import re
import subprocess
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

FOLDER = Path("docs/gate_v3/semantic-facet-blind-v2-draft")
FACETS = {"PROCEDURE", "MATERIAL", "DEADLINE", "RESPONSIBLE_ROLE", "PROCESS_CHOICE"}


def normalized(text):
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text)).casefold()


def validate(questions, review):
    if len(questions) != 18 or review["sample_count"] != 18:
        raise ValueError("sample count")
    ids = [q["case_id"] for q in questions]
    if len(set(ids)) != 18 or any(set(q) != {"case_id", "query"} for q in questions):
        raise ValueError("duplicate ID or model-visible fields")
    if review["status"] != "pending_review" or any(
        review[k] is not False
        for k in ("independent_test_set", "numeric_resume_ready", "model_inference")
    ):
        raise ValueError("review boundary")
    if [r["case_id"] for r in review["cases"]] != ids:
        raise ValueError("label alignment")
    for q, row in zip(questions, review["cases"], strict=True):
        if (
            not q["query"].strip()
            or row["review_status"] != "pending_review"
            or row["accepted_facets"] is not None
        ):
            raise ValueError("pending review required")
        seen = set()
        for facet in row["proposed_facets"]:
            if (
                facet["facet"] not in FACETS
                or facet["facet"] in seen
                or facet["binding"] not in {"BOUND", "UNBOUND"}
            ):
                raise ValueError("facet contract")
            seen.add(facet["facet"])
            span = facet["span"]
            if (
                not (0 <= span["start"] < span["end"] <= len(q["query"]))
                or q["query"][span["start"] : span["end"]] != span["text"]
            ):
                raise ValueError("span mismatch")


def extract(value):
    if isinstance(value, dict):
        if isinstance(value.get("query"), str):
            yield {"case_id": value.get("case_id"), "query": value["query"]}
        for item in value.values():
            yield from extract(item)
    elif isinstance(value, list):
        for item in value:
            yield from extract(item)


def duplicates(questions, corpus, threshold=0.65):
    exact, near = [], []
    for i, q in enumerate(questions):
        for other in corpus + questions[:i]:
            left, right = normalized(q["query"]), normalized(other["query"])
            score = SequenceMatcher(None, left, right).ratio()
            match = {
                "case_id": q["case_id"],
                "other_case_id": other.get("case_id"),
                "other_query": other["query"],
                "source": other.get("source", "current-draft"),
                "similarity": score,
            }
            if left == right:
                exact.append(match)
            elif score >= threshold:
                near.append(match)
    return exact, near


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    questions = [
        json.loads(line)
        for line in (FOLDER / "questions.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    review = json.loads((FOLDER / "review.json").read_text(encoding="utf-8"))
    validate(questions, review)
    corpus, sources = [], []
    for path in sorted(Path("docs/gate_v3").rglob("*")):
        if FOLDER in path.parents or path.suffix not in {".json", ".jsonl"}:
            continue
        raw = path.read_text(encoding="utf-8-sig")
        values = (
            [json.loads(line) for line in raw.splitlines() if line.strip()]
            if path.suffix == ".jsonl"
            else [json.loads(raw)]
        )
        rows = list(extract(values))
        if rows:
            sources.append(
                {"path": path.as_posix(), "sha256": digest(path), "query_count": len(rows)}
            )
            corpus.extend({**row, "source": path.as_posix()} for row in rows)
    exact, near = duplicates(questions, corpus)
    report = {
        "status": "passed" if not exact else "failed",
        "scope": "draft_structure_and_duplicate_screening",
        "dataset_version": review["dataset_version"],
        "sample_count": len(questions),
        "facet_instance_count": sum(len(r["proposed_facets"]) for r in review["cases"]),
        "empty_facet_cases": [r["case_id"] for r in review["cases"] if not r["proposed_facets"]],
        "corpus_scope": "All query fields in docs/gate_v3 JSON/JSONL excluding this draft; no completeness claim outside this directory",
        "sources": sources,
        "corpus_query_count": len(corpus),
        "exact_matches": exact,
        "near_matches": near,
        "near_threshold": 0.65,
        "near_method": "NFKC, remove punctuation/whitespace, SequenceMatcher; review hints only, not semantic deduplication",
        "hashes": {
            p.as_posix(): digest(p)
            for p in [
                FOLDER / "questions.jsonl",
                FOLDER / "review.json",
                Path("docs/gate_v3/semantic-facet-prompt-v1.txt"),
                Path(__file__),
            ]
        },
        "code_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "python": platform.python_version(),
        "dependencies": "standard-library-only",
        "model_id": None,
        "model_revision": None,
        "model_inference": False,
        "semantic_accuracy": None,
        "independent_test_set": False,
        "numeric_resume_ready": False,
    }
    (FOLDER / "validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "status",
                    "sample_count",
                    "facet_instance_count",
                    "corpus_query_count",
                    "empty_facet_cases",
                )
            }
        )
    )
    print(f"Exact matches: {len(exact)}; near-match review hints: {len(near)}")
    return int(bool(exact))


if __name__ == "__main__":
    raise SystemExit(main())
