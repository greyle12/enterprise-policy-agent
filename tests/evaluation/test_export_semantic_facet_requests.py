import json
from pathlib import Path

import pytest

from scripts.export_semantic_facet_requests import digest, export_requests

ROOT = Path(__file__).resolve().parents[2]


def test_export_is_complete_label_free_and_repeatable(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    manifest = export_requests(ROOT, first)
    export_requests(ROOT, second)
    records = [
        json.loads(line)
        for line in (ROOT / "docs/gate_v3/semantic-dev-v1-confirmed/records.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    rows = [
        json.loads(line)
        for line in (first / "requests.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == len(records) == manifest["sample_count"] == 7
    assert len({row["case_id"] for row in rows}) == 7
    assert all(set(row) == {"case_id", "query"} for row in rows)
    assert [row["query"] for row in rows] == [record["query"] for record in records]
    assert manifest["model_visible_files"] == ["prompt.txt", "requests.jsonl"]
    assert manifest["requests_sha256"] == digest(first / "requests.jsonl")
    assert manifest["prompt_sha256"] == digest(first / "prompt.txt")
    assert manifest["model_inference"] is False
    for filename in ("requests.jsonl", "prompt.txt", "manifest.json"):
        assert (first / filename).read_bytes() == (second / filename).read_bytes()
    before = (first / "requests.jsonl").read_bytes()
    with pytest.raises(FileExistsError):
        export_requests(ROOT, first)
    assert (first / "requests.jsonl").read_bytes() == before
