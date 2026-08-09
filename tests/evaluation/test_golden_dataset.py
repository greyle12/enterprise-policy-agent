from __future__ import annotations

from pathlib import Path

import pytest

from app.evaluation.dataset import GoldenDatasetError, load_golden_dataset
from app.evaluation.models import GoldenCaseCategory

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_GOLDEN_DATASET = _PROJECT_ROOT / "tests" / "evaluation" / "golden_test_cases.jsonl"


def test_loads_thirty_balanced_golden_cases() -> None:
    dataset = load_golden_dataset(_GOLDEN_DATASET)

    assert len(dataset.cases) == 30
    assert len(dataset.sha256) == 64
    assert {
        category: sum(case.category is category for case in dataset.cases)
        for category in GoldenCaseCategory
    } == {
        GoldenCaseCategory.ROUTING: 10,
        GoldenCaseCategory.MATERIAL_CHECK: 10,
        GoldenCaseCategory.APPROVAL_ROUTE: 10,
    }


def test_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    line = (
        '{"case_id":"ROUTE-001","category":"routing",'
        '"title":"重复用例","query":"查询制度",'
        '"expected_intent":"policy_query",'
        '"expected_tool":"search_policy"}'
    )
    path = tmp_path / "duplicate.jsonl"
    path.write_text(f"{line}\n{line}\n", encoding="utf-8")

    with pytest.raises(GoldenDatasetError, match="duplicate golden case_id"):
        load_golden_dataset(path)


def test_reports_invalid_json_line_number(tmp_path: Path) -> None:
    path = tmp_path / "invalid.jsonl"
    path.write_text("\n{not-json}\n", encoding="utf-8")

    with pytest.raises(GoldenDatasetError, match="line 2"):
        load_golden_dataset(path)


def test_rejects_category_with_wrong_expectation_fields(tmp_path: Path) -> None:
    path = tmp_path / "wrong-schema.jsonl"
    path.write_text(
        (
            '{"case_id":"MAT-001","category":"material_check",'
            '"title":"错误字段","query":"需要什么材料？",'
            '"expected_intent":"material_check"}\n'
        ),
        encoding="utf-8",
    )

    with pytest.raises(GoldenDatasetError, match="invalid golden case"):
        load_golden_dataset(path)
