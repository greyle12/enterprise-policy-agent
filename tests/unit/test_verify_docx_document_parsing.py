from __future__ import annotations

from scripts.verify_docx_document_parsing import main, run_verification


def test_docx_document_parsing_verification_passes() -> None:
    report = run_verification()

    assert report["phase"] == 24
    assert report["passed"] is True
    assert report["document_count"] == 1
    assert report["source_block_count"] == 7
    assert report["chunk_count"] == 2
    assert report["ocr_executed"] is False
    assert report["network_calls"] is False
    assert report["model_calls"] is False
    assert all(report["checks"].values())


def test_docx_document_parsing_cli_prints_json(capsys) -> None:
    exit_code = main()

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"phase": 24' in output
    assert '"passed": true' in output
