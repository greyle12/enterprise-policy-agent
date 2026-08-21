from pathlib import Path

from scripts.verify_document_loader import main, run_verification


def test_document_loader_verification_passes() -> None:
    report = run_verification(policy_directory=Path("data/policies"))

    assert report["passed"] is True
    assert report["phase"] == 22
    assert report["document_count"] == 5
    assert report["chunk_count"] == 199
    assert report["network_calls"] is False
    assert report["model_calls"] is False


def test_document_loader_cli_prints_json(capsys) -> None:
    exit_code = main(["--policy-directory", "data/policies"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"passed": true' in output
    assert '"phase": 22' in output
    assert '"chunk_count": 199' in output
