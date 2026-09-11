import copy
import json
from pathlib import Path

import pytest

from scripts.check_semantic_facet_holdout_draft import (
    duplicates,
    load_questions,
    validate_annotation_template,
    validate_questions,
)


ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / "docs/gate_v3/semantic-facet-holdout-v1-candidate"


def package_data():
    questions = load_questions(FOLDER / "questions.jsonl")
    template = json.loads((FOLDER / "annotations.template.json").read_text(encoding="utf-8"))
    return questions, template


def test_current_candidate_is_label_free_and_aligned():
    questions, template = package_data()
    validate_questions(questions)
    validate_annotation_template(questions, template)
    assert all(set(question) == {"case_id", "query"} for question in questions)
    assert template["independent_test_set"] is False
    assert template["model_inference"] is False


@pytest.mark.parametrize("kind", ["field_leak", "duplicate_id", "duplicate_query"])
def test_invalid_question_package_rejected(kind):
    questions, _ = package_data()
    mutated = copy.deepcopy(questions)
    if kind == "field_leak":
        mutated[0]["facet"] = "MATERIAL"
    elif kind == "duplicate_id":
        mutated[1]["case_id"] = mutated[0]["case_id"]
    else:
        mutated[1]["query"] = mutated[0]["query"]
    with pytest.raises(ValueError):
        validate_questions(mutated)


def test_prefilled_annotation_rejected():
    questions, template = package_data()
    mutated = copy.deepcopy(template)
    mutated["cases"][0]["annotator_a"]["accepted_facets"] = []
    with pytest.raises(ValueError):
        validate_annotation_template(questions, mutated)


def test_exact_overlap_is_reported():
    questions = [{"case_id": "SHV-001", "query": "请问由谁审核？"}]
    corpus = [{"case_id": "old", "query": "请问 由谁审核!", "source": "old.jsonl"}]
    exact, near = duplicates(questions, corpus)
    assert len(exact) == 1
    assert near == []


def test_near_overlap_is_a_review_hint():
    questions = [{"case_id": "SHV-001", "query": "请问由谁审核？"}]
    corpus = [{"case_id": "old", "query": "请问由谁审批？", "source": "old.jsonl"}]
    exact, near = duplicates(questions, corpus, threshold=0.65)
    assert exact == []
    assert len(near) == 1
