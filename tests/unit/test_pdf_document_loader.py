from __future__ import annotations

from pathlib import Path

import pytest

from app.rag.document_loader import (
    DEFAULT_DOCUMENT_LOADER_REGISTRY,
    DocumentMetadataError,
    EncryptedDocumentError,
    InvalidDocumentError,
    OCRRequiredError,
    PDFDocumentLoader,
    pdf_metadata_sidecar_path,
)
from app.rag.policy_chunker import chunk_policy_file
from app.rag.policy_parser import parse_policy_file

pymupdf = pytest.importorskip("pymupdf")


def _metadata_text() -> str:
    return """document_id: PDF_POLICY_001
document_type: policy
title: PDF 测试制度
version: "1.0"
status: effective
issuing_department: 技术部
effective_date: 2026-01-01
allowed_departments:
  - ALL
allowed_roles:
  - EMPLOYEE
security_level: internal
region: 中国大陆
"""


def _write_sidecar(path: Path) -> Path:
    sidecar = pdf_metadata_sidecar_path(path)
    sidecar.write_text(_metadata_text(), encoding="utf-8")
    return sidecar


def _write_pdf(path: Path, pages: list[list[str]]) -> None:
    document = pymupdf.open()
    try:
        for page_lines in pages:
            page = document.new_page()
            vertical_position = 72
            for index, line in enumerate(page_lines):
                page.insert_text(
                    (72, vertical_position),
                    line,
                    fontname="china-s",
                    fontsize=16 if index == 0 else 12,
                )
                vertical_position += 28
        document.save(path)
    finally:
        document.close()


def test_pdf_sidecar_uses_stable_sibling_name(tmp_path: Path) -> None:
    assert pdf_metadata_sidecar_path(tmp_path / "travel.v2.pdf") == (
        tmp_path / "travel.v2.metadata.yaml"
    )


def test_pdf_loader_extracts_native_text_and_page_mapping(tmp_path: Path) -> None:
    source = tmp_path / "policy.pdf"
    _write_pdf(
        source,
        [
            [
                "PDF 测试制度",
                "第一章 总则",
                "第一条 适用范围",
                "本制度适用于全体员工。",
            ],
            [
                "第二条 保密要求",
                "员工不得泄露企业内部信息。",
            ],
        ],
    )
    sidecar = _write_sidecar(source)

    loaded = PDFDocumentLoader().load(source)

    assert loaded.source_path == source
    assert loaded.loader_name == "pymupdf"
    assert loaded.media_type == "application/pdf"
    assert loaded.metadata_source_path == sidecar
    assert loaded.metadata_text == _metadata_text()
    assert loaded.page_count == 2
    assert "## 第一章 总则" in loaded.text
    assert "### 第一条 适用范围" in loaded.text
    assert "### 第二条 保密要求" in loaded.text
    assert len(loaded.line_page_numbers) == len(loaded.text.splitlines())
    assert set(loaded.line_page_numbers) == {1, 2}


def test_pdf_flows_through_parser_and_existing_chunker(tmp_path: Path) -> None:
    source = tmp_path / "policy.pdf"
    _write_pdf(
        source,
        [
            [
                "PDF 测试制度",
                "第一章 总则",
                "第一条 适用范围",
                "本制度适用于全体员工。",
            ],
            [
                "第二条 保密要求",
                "员工不得泄露企业内部信息。",
            ],
        ],
    )
    sidecar = _write_sidecar(source)

    document = parse_policy_file(source)
    chunks = chunk_policy_file(source)

    assert document.document_id == "PDF_POLICY_001"
    assert document.source_media_type == "application/pdf"
    assert document.source_loader_name == "pymupdf"
    assert document.metadata_source_path == sidecar
    assert document.source_page_count == 2
    assert len(chunks) == 2
    assert chunks[0].article_label == "第一条"
    assert chunks[0].source_page_start == 1
    assert chunks[0].source_page_end == 1
    assert chunks[1].article_label == "第二条"
    assert chunks[1].source_page_start == 2
    assert chunks[1].source_page_end == 2
    assert all(chunk.metadata_source_path == sidecar for chunk in chunks)


def test_default_registry_registers_markdown_pdf_and_docx() -> None:
    assert DEFAULT_DOCUMENT_LOADER_REGISTRY.supported_extensions == (".docx", ".md", ".pdf")


def test_pdf_loader_requires_trusted_sidecar(tmp_path: Path) -> None:
    source = tmp_path / "missing-metadata.pdf"
    _write_pdf(source, [["第一章 总则", "第一条 适用范围", "适用于全体员工。"]])

    with pytest.raises(DocumentMetadataError, match="sidecar does not exist"):
        PDFDocumentLoader().load(source)


def test_pdf_loader_marks_no_text_layer_for_future_ocr(tmp_path: Path) -> None:
    source = tmp_path / "scanned.pdf"
    _write_pdf(source, [[]])
    _write_sidecar(source)

    with pytest.raises(OCRRequiredError, match="OCR fallback is required"):
        PDFDocumentLoader().load(source)


def test_pdf_loader_rejects_corrupt_pdf(tmp_path: Path) -> None:
    source = tmp_path / "corrupt.pdf"
    source.write_bytes(b"this is not a PDF")
    _write_sidecar(source)

    with pytest.raises(InvalidDocumentError, match="cannot open PDF"):
        PDFDocumentLoader().load(source)


def test_pdf_loader_rejects_password_protected_pdf(tmp_path: Path) -> None:
    source = tmp_path / "encrypted.pdf"
    document = pymupdf.open()
    try:
        page = document.new_page()
        page.insert_text((72, 72), "protected policy")
        document.save(
            source,
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw="owner-password",
            user_pw="user-password",
        )
    finally:
        document.close()
    _write_sidecar(source)

    with pytest.raises(EncryptedDocumentError, match="requires a password"):
        PDFDocumentLoader().load(source)
