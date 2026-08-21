from __future__ import annotations

from pathlib import Path

import pytest

from app.rag.document_loader import (
    DOCXDocumentLoader,
    DocumentMetadataError,
    InvalidDocumentError,
    OCRRequiredError,
    docx_metadata_sidecar_path,
)
from app.rag.policy_chunker import chunk_policy_file
from app.rag.policy_context import build_policy_context
from app.rag.policy_parser import parse_policy_file
from app.rag.policy_retriever import PolicyRetrievalResult

docx = pytest.importorskip("docx")


def _metadata_text() -> str:
    return """document_id: DOCX_POLICY_001
document_type: policy
title: DOCX 测试制度
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
    sidecar = docx_metadata_sidecar_path(path)
    sidecar.write_text(_metadata_text(), encoding="utf-8")
    return sidecar


def _write_policy_docx(path: Path) -> None:
    document = docx.Document()
    document.add_paragraph("DOCX 测试制度", style="Title")
    document.add_paragraph("第一章 总则", style="Heading 1")
    document.add_paragraph("第一条 适用范围", style="Heading 2")
    document.add_paragraph("本制度适用于全体员工。")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "材料"
    table.cell(0, 1).text = "要求"
    table.cell(1, 0).text = "申请表"
    table.cell(1, 1).text = "必须提交"
    document.add_paragraph("第二条 保密要求", style="Heading 2")
    document.add_paragraph("员工不得泄露企业内部信息。")
    document.save(path)


def test_docx_sidecar_uses_stable_sibling_name(tmp_path: Path) -> None:
    assert docx_metadata_sidecar_path(tmp_path / "travel.v2.docx") == (
        tmp_path / "travel.v2.metadata.yaml"
    )


def test_docx_loader_preserves_document_order_headings_tables_and_blocks(
    tmp_path: Path,
) -> None:
    source = tmp_path / "policy.docx"
    _write_policy_docx(source)
    sidecar = _write_sidecar(source)

    loaded = DOCXDocumentLoader().load(source)

    assert loaded.source_path == source
    assert loaded.loader_name == "python-docx"
    assert loaded.media_type.endswith("wordprocessingml.document")
    assert loaded.metadata_source_path == sidecar
    assert loaded.metadata_text == _metadata_text()
    assert loaded.block_count == 7
    assert loaded.text.index("### 第一条 适用范围") < loaded.text.index("| 材料 | 要求 |")
    assert loaded.text.index("| 材料 | 要求 |") < loaded.text.index("### 第二条 保密要求")
    assert "| --- | --- |" in loaded.text
    assert "| 申请表 | 必须提交 |" in loaded.text
    assert len(loaded.line_block_numbers) == len(loaded.text.splitlines())
    assert min(loaded.line_block_numbers) == 1
    assert max(loaded.line_block_numbers) == 7


def test_docx_style_headings_are_normalized_when_text_has_no_policy_number(
    tmp_path: Path,
) -> None:
    source = tmp_path / "styles.docx"
    document = docx.Document()
    document.add_paragraph("员工手册", style="Title")
    document.add_paragraph("适用说明", style="Heading 1")
    document.add_paragraph("办理材料", style="Heading 2")
    document.add_paragraph("申请人提交材料。")
    document.save(source)
    _write_sidecar(source)

    loaded = DOCXDocumentLoader().load(source)

    assert "# 员工手册" in loaded.text
    assert "## 适用说明" in loaded.text
    assert "### 办理材料" in loaded.text


def test_docx_flows_through_existing_parser_chunker_and_context(tmp_path: Path) -> None:
    source = tmp_path / "policy.docx"
    _write_policy_docx(source)
    sidecar = _write_sidecar(source)

    document = parse_policy_file(source)
    chunks = chunk_policy_file(source)
    context = build_policy_context([PolicyRetrievalResult(chunk=chunks[1], score=0.9)])

    assert document.document_id == "DOCX_POLICY_001"
    assert document.source_loader_name == "python-docx"
    assert document.metadata_source_path == sidecar
    assert document.source_block_count == 7
    assert document.source_page_count is None
    assert len(chunks) == 2
    assert chunks[0].source_block_start == 3
    assert chunks[0].source_block_end == 5
    assert "| 申请表 | 必须提交 |" in chunks[0].content
    assert chunks[1].source_block_start == 6
    assert chunks[1].source_block_end == 7
    assert chunks[1].source_page_start is None
    assert context.citations[0].source_block_start == 6
    assert context.citations[0].source_block_end == 7
    assert '"source_block_start":"6"' in context.text


def test_docx_loader_requires_trusted_sidecar(tmp_path: Path) -> None:
    source = tmp_path / "missing-metadata.docx"
    _write_policy_docx(source)

    with pytest.raises(DocumentMetadataError, match="sidecar does not exist"):
        DOCXDocumentLoader().load(source)


def test_docx_loader_marks_image_only_or_empty_body_for_future_ocr(tmp_path: Path) -> None:
    source = tmp_path / "image-only.docx"
    docx.Document().save(source)
    _write_sidecar(source)

    with pytest.raises(OCRRequiredError, match="OCR fallback is required"):
        DOCXDocumentLoader().load(source)


def test_docx_loader_rejects_corrupt_package(tmp_path: Path) -> None:
    source = tmp_path / "corrupt.docx"
    source.write_bytes(b"this is not an Office Open XML package")
    _write_sidecar(source)

    with pytest.raises(InvalidDocumentError, match="cannot open DOCX"):
        DOCXDocumentLoader().load(source)
