from __future__ import annotations

from pathlib import Path

import pytest

from app.evaluation.retrieval_dataset import RetrievalDatasetError, load_retrieval_dataset
from app.evaluation.retrieval_models import RelevanceGrade, RetrievalCase

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DATASET = _PROJECT_ROOT / "tests" / "evaluation" / "retrieval_test_cases.jsonl"


def test_loads_cross_domain_retrieval_judgments() -> None:
    dataset = load_retrieval_dataset(_DATASET)

    assert len(dataset.cases) == 20
    assert sum(len(case.judgments) for case in dataset.cases) == 30
    assert len(dataset.sha256) == 64
    assert {tag for case in dataset.cases for tag in case.tags} >= {
        "travel",
        "procurement",
        "expense",
        "security",
        "leave",
        "multi-relevant",
        "graded-relevance",
    }
    assert {judgment.relevance for case in dataset.cases for judgment in case.judgments} == {
        RelevanceGrade.MARGINAL,
        RelevanceGrade.SUPPORTING,
        RelevanceGrade.HIGHLY_RELEVANT,
    }
    assert all(judgment.rationale for case in dataset.cases for judgment in case.judgments)


def test_upgrades_phase31_binary_case_to_highly_relevant_judgments() -> None:
    case = RetrievalCase.model_validate(
        {
            "case_id": "RET-001",
            "title": "legacy",
            "query": "legacy query",
            "relevant_chunk_ids": ["chunk-1"],
        }
    )

    assert case.relevant_chunk_ids == ("chunk-1",)
    assert case.relevance_by_chunk_id == {"chunk-1": 3}


def test_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    line = (
        '{"case_id":"RET-001","title":"case","query":"query text","relevant_chunk_ids":["chunk-1"]}'
    )
    path = tmp_path / "duplicate.jsonl"
    path.write_text(f"{line}\n{line}\n", encoding="utf-8")

    with pytest.raises(RetrievalDatasetError, match="duplicate retrieval case_id"):
        load_retrieval_dataset(path)


def test_rejects_duplicate_relevant_chunk_ids(tmp_path: Path) -> None:
    path = tmp_path / "duplicate-label.jsonl"
    path.write_text(
        '{"case_id":"RET-001","title":"case","query":"query text",'
        '"relevant_chunk_ids":["chunk-1","chunk-1"]}\n',
        encoding="utf-8",
    )

    with pytest.raises(RetrievalDatasetError, match="judgment chunk_id values must be unique"):
        load_retrieval_dataset(path)


def test_reports_invalid_json_line_number(tmp_path: Path) -> None:
    path = tmp_path / "invalid.jsonl"
    path.write_text("\n{bad-json}\n", encoding="utf-8")

    with pytest.raises(RetrievalDatasetError, match="line 2"):
        load_retrieval_dataset(path)
