from __future__ import annotations

import builtins
import base64
from io import BytesIO
from pathlib import Path

import pytest

from app.rag.document_loader import (
    DOCXDocumentLoader,
    DocumentLoaderRegistry,
    OCRRequiredError,
    PDFDocumentLoader,
    docx_metadata_sidecar_path,
    pdf_metadata_sidecar_path,
)
from app.rag.ocr import (
    OCRDependencyError,
    OCRImage,
    OCRQualityError,
    OCRQualityGate,
    OCRResult,
    TesseractOCRProvider,
)
from app.rag.policy_chunker import chunk_policy_file
from app.rag.policy_context import build_policy_context
from app.rag.policy_parser import PolicyParseError, parse_policy_file
from app.rag.policy_retriever import PolicyRetrievalResult

docx = pytest.importorskip("docx")
pymupdf = pytest.importorskip("pymupdf")


class _FakeOCRProvider:
    def __init__(self, text: str, *, confidence: float = 0.96) -> None:
        self.text = text
        self.confidence = confidence
        self.calls: list[OCRImage] = []

    def recognize(self, image: OCRImage) -> OCRResult:
        self.calls.append(image)
        return OCRResult(
            text=self.text,
            confidence=self.confidence,
            engine="offline-fixture-ocr",
            language="chi_sim+eng",
        )


def _metadata_text(document_id: str) -> str:
    return f"""document_id: {document_id}
document_type: policy
title: OCR 测试制度
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


def _ocr_policy_text() -> str:
    return """OCR 测试制度
第一章 总则
第一条 适用范围
本制度适用于需要进行 OCR 识别的企业制度文档。
"""


def _write_scanned_pdf(path: Path) -> None:
    document = pymupdf.open()
    try:
        document.new_page()
        document.save(path)
    finally:
        document.close()
    pdf_metadata_sidecar_path(path).write_text(_metadata_text("OCR_PDF_001"), encoding="utf-8")


def _write_mixed_pdf(path: Path) -> None:
    document = pymupdf.open()
    try:
        native_page = document.new_page()
        for index, text in enumerate(
            (
                "OCR 测试制度",
                "第一章 总则",
                "第一条 原生条款",
                "这是位于原生文本页的制度内容，不应调用 OCR。",
            )
        ):
            native_page.insert_text(
                (72, 72 + index * 28),
                text,
                fontname="china-s",
                fontsize=12,
            )
        document.new_page()
        document.save(path)
    finally:
        document.close()
    pdf_metadata_sidecar_path(path).write_text(
        _metadata_text("OCR_MIXED_PDF_001"), encoding="utf-8"
    )


def _png_bytes() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAFAAAAAoCAIAAADmAupWAAAAS0lEQVR4nO3PAQ0AIRDAsOf9"
        "ez5cQDJaBduame8l/+2A0wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmu"
        "M1y3AY/gA0371o7fAAAAAElFTkSuQmCC"
    )


def _write_image_docx(path: Path) -> None:
    document = docx.Document()
    document.add_picture(BytesIO(_png_bytes()))
    document.save(path)
    docx_metadata_sidecar_path(path).write_text(_metadata_text("OCR_DOCX_001"), encoding="utf-8")


def test_pdf_ocr_fallback_flows_through_parser_chunker_and_citation(tmp_path: Path) -> None:
    source = tmp_path / "scanned.pdf"
    _write_scanned_pdf(source)
    provider = _FakeOCRProvider(_ocr_policy_text())
    registry = DocumentLoaderRegistry([PDFDocumentLoader(ocr_provider=provider)])

    loaded = registry.load(source)
    document = parse_policy_file(source, loader_registry=registry)
    chunks = chunk_policy_file(source, loader_registry=registry)
    context = build_policy_context([PolicyRetrievalResult(chunk=chunks[0], score=1.0)])

    assert len(provider.calls) == 3
    assert provider.calls[0].unit_kind == "page"
    assert provider.calls[0].unit_number == 1
    assert provider.calls[0].media_type == "image/png"
    assert loaded.ocr_applied is True
    assert loaded.ocr_engine == "offline-fixture-ocr"
    assert loaded.ocr_unit_numbers == (1,)
    assert document.source_ocr_applied is True
    assert chunks[0].source_ocr_applied is True
    assert chunks[0].source_ocr_unit_kind == "page"
    assert chunks[0].source_ocr_unit_numbers == (1,)
    assert chunks[0].source_ocr_confidence_min == pytest.approx(0.96)
    assert context.citations[0].source_ocr_engine == "offline-fixture-ocr"
    assert context.citations[0].source_ocr_unit_numbers == (1,)


def test_docx_embedded_image_ocr_preserves_block_provenance(tmp_path: Path) -> None:
    source = tmp_path / "image-policy.docx"
    _write_image_docx(source)
    provider = _FakeOCRProvider(_ocr_policy_text())
    registry = DocumentLoaderRegistry([DOCXDocumentLoader(ocr_provider=provider)])

    loaded = registry.load(source)
    chunks = chunk_policy_file(source, loader_registry=registry)

    assert len(provider.calls) == 2
    assert provider.calls[0].unit_kind == "block"
    assert provider.calls[0].container_media_type.endswith("wordprocessingml.document")
    assert loaded.ocr_unit_kind == "block"
    assert loaded.ocr_unit_numbers == (1,)
    assert chunks[0].source_block_start == 1
    assert chunks[0].source_block_end == 1
    assert chunks[0].source_ocr_unit_numbers == (1,)
    assert chunks[0].source_ocr_confidence_min == pytest.approx(0.96)


def test_mixed_pdf_only_ocr_processes_scanned_page(tmp_path: Path) -> None:
    source = tmp_path / "mixed.pdf"
    _write_mixed_pdf(source)
    provider = _FakeOCRProvider("第二条 扫描条款\n这是位于扫描页的制度内容，需要 OCR 后进入索引。")
    registry = DocumentLoaderRegistry([PDFDocumentLoader(ocr_provider=provider)])

    loaded = registry.load(source)
    chunks = chunk_policy_file(source, loader_registry=registry)

    assert len(provider.calls) == 2
    assert all(call.unit_number == 2 for call in provider.calls)
    assert loaded.ocr_unit_numbers == (2,)
    assert len(chunks) == 2
    assert chunks[0].article_label == "第一条"
    assert chunks[0].source_ocr_applied is False
    assert chunks[1].article_label == "第二条"
    assert chunks[1].source_page_start == 2
    assert chunks[1].source_ocr_unit_numbers == (2,)


def test_low_confidence_ocr_is_rejected_before_policy_parsing(tmp_path: Path) -> None:
    source = tmp_path / "low-confidence.pdf"
    _write_scanned_pdf(source)
    provider = _FakeOCRProvider(_ocr_policy_text(), confidence=0.42)
    loader = PDFDocumentLoader(
        ocr_provider=provider,
        ocr_quality_gate=OCRQualityGate(minimum_confidence=0.80),
    )

    with pytest.raises(OCRQualityError, match="confidence is below"):
        loader.load(source)

    registry = DocumentLoaderRegistry([loader])
    with pytest.raises(PolicyParseError, match="制度 OCR 失败"):
        parse_policy_file(source, loader_registry=registry)


def test_ocr_remains_explicit_when_no_provider_is_configured(tmp_path: Path) -> None:
    source = tmp_path / "scanned.pdf"
    _write_scanned_pdf(source)

    with pytest.raises(OCRRequiredError, match="OCR fallback is required"):
        PDFDocumentLoader().load(source)


def test_ocr_quality_gate_rejects_too_little_text(tmp_path: Path) -> None:
    image = OCRImage(
        data=b"image",
        media_type="image/png",
        source_path=tmp_path / "source.pdf",
        container_media_type="application/pdf",
        unit_kind="page",
        unit_number=1,
    )
    result = OCRResult(text="少量", confidence=0.99, engine="fixture", language="chi_sim")

    with pytest.raises(OCRQualityError, match="text is below"):
        OCRQualityGate(minimum_text_characters=3).accept(result, image=image)


def test_tesseract_adapter_reports_missing_optional_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = OCRImage(
        data=b"png",
        media_type="image/png",
        source_path=tmp_path / "source.pdf",
        container_media_type="application/pdf",
        unit_kind="page",
        unit_number=1,
    )

    original_import = builtins.__import__

    def import_without_pytesseract(name: str, *args, **kwargs):
        if name == "pytesseract":
            raise ModuleNotFoundError("pytesseract fixture")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_pytesseract)

    with pytest.raises(OCRDependencyError, match="optional 'ocr' dependencies"):
        TesseractOCRProvider().recognize(image)
