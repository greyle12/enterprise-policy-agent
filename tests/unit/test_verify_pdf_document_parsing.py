from __future__ import annotations

import json

import pytest

from scripts.verify_pdf_document_parsing import main, run_verification

pytest.importorskip("pymupdf")


def test_pdf_document_parsing_verification_passes() -> None:
    report = run_verification()

    assert report["passed"] is True
    assert report["phase"] == 23
    assert report["document_count"] == 1
    assert report["page_count"] == 2
    assert report["chunk_count"] == 2
    assert report["ocr_executed"] is False
    assert report["network_calls"] is False
    assert report["model_calls"] is False


def test_pdf_document_parsing_cli_prints_json(capsys) -> None:
    exit_code = main()

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["passed"] is True
    assert payload["parser"] == "pymupdf-native-text"
