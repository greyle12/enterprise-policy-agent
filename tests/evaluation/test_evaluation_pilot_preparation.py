"""Guard against stale baselines and overwriting prior preparation evidence."""

import hashlib
import json

import pytest

from scripts import prepare_evaluation_pilot as pilot


def test_manifest_drift_blocks_preparation(tmp_path, monkeypatch):
    evidence = tmp_path / pilot.EVIDENCE
    evidence.mkdir(parents=True)
    source = tmp_path / "source.py"
    source.write_text("original", encoding="utf-8")
    (evidence / "delivery-manifest.json").write_text(
        json.dumps({"source.py": hashlib.sha256(source.read_bytes()).hexdigest()}),
        encoding="utf-8",
    )
    assert pilot.verify_delivery(tmp_path)["source.py"]["matches"]
    source.write_text("changed", encoding="utf-8")
    monkeypatch.setattr(pilot, "ROOT", tmp_path)
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="manifest drift"):
        pilot.prepare(output)
    assert not output.exists()
    source.unlink()
    assert pilot.verify_delivery(tmp_path)["source.py"]["actual"] is None


def test_existing_preparation_cannot_be_overwritten(tmp_path):
    marker = tmp_path / "baseline.json"
    marker.write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError):
        pilot.prepare(tmp_path)
    assert marker.read_text(encoding="utf-8") == "preserve"
