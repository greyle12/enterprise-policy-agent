import copy
import json
from pathlib import Path

import pytest

from scripts.check_semantic_facet_blind_draft import duplicates, validate


def data():
    folder = Path(__file__).resolve().parents[2] / "docs/gate_v3/semantic-facet-blind-v2-draft"
    return (
        [
            json.loads(x)
            for x in (folder / "questions.jsonl").read_text(encoding="utf-8").splitlines()
        ],
        json.loads((folder / "review.json").read_text(encoding="utf-8")),
    )


def test_current_draft():
    validate(*data())


@pytest.mark.parametrize("kind", ["span", "leak", "confirmed", "id", "alignment", "binding"])
def test_invalid_draft_rejected(kind):
    questions, review = copy.deepcopy(data())
    if kind == "span":
        review["cases"][0]["proposed_facets"][0]["span"]["end"] -= 1
    elif kind == "leak":
        questions[0]["label"] = "MATERIAL"
    elif kind == "confirmed":
        review["cases"][0]["accepted_facets"] = []
    elif kind == "id":
        questions[1]["case_id"] = questions[0]["case_id"]
    elif kind == "alignment":
        review["cases"].reverse()
    else:
        review["cases"][0]["proposed_facets"][0]["binding"] = "MISSING"
    with pytest.raises(ValueError):
        validate(questions, review)


def test_duplicate_normalization_and_near_hint():
    questions = [{"case_id": "a", "query": "请问由谁审核？"}]
    exact, near = duplicates(
        questions,
        [{"case_id": "b", "query": "请问 由谁审核!"}, {"case_id": "c", "query": "请问由谁审批？"}],
    )
    assert len(exact) == 1
    assert len(near) == 1
