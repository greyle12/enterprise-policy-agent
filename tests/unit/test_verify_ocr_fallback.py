from __future__ import annotations

from scripts.verify_ocr_fallback import main, run_verification


def test_ocr_fallback_verification_passes() -> None:
    report = run_verification()

    assert report["phase"] == 25
    assert report["passed"] is True
    assert report["pdf_ocr_units"] == 1
    assert report["docx_ocr_units"] == 1
    assert report["external_ocr_processes"] == 0
    assert report["network_calls"] is False
    assert report["model_calls"] is False
    assert all(report["checks"].values())


def test_ocr_fallback_cli_prints_json(capsys) -> None:
    exit_code = main()

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"phase": 25' in output
    assert '"passed": true' in output
