from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path

from app.rag.document_loader import (
    DocumentMetadataError,
    OCRRequiredError,
    PDFDocumentLoader,
    pdf_metadata_sidecar_path,
)
from app.rag.policy_chunker import chunk_policy_directory
from app.rag.policy_context import build_policy_context
from app.rag.policy_parser import parse_policy_directory
from app.rag.policy_retriever import PolicyRetrievalResult, PolicyRetriever
from app.schemas.policy import SecurityLevel
from app.security import PolicyAccessContext, TrustedIdentitySource


class _OfflineEmbeddingProvider:
    @property
    def dimension(self) -> int:
        return 1

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        del text
        return [1.0]


def _metadata_text() -> str:
    return """document_id: PDF_VERIFY_001
document_type: policy
title: PDF 离线验收制度
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


def _write_sidecar(path: Path) -> None:
    pdf_metadata_sidecar_path(path).write_text(_metadata_text(), encoding="utf-8")


def _write_pdf(path: Path, pages: list[list[str]]) -> None:
    import pymupdf

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


def run_verification() -> dict[str, object]:
    """Verify native PDF extraction, provenance, security, and OCR handoff offline."""

    with tempfile.TemporaryDirectory(prefix="enterprise-policy-pdf-") as temporary_directory:
        root = Path(temporary_directory)
        source = root / "policy.pdf"
        _write_pdf(
            source,
            [
                [
                    "PDF 离线验收制度",
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
        _write_sidecar(source)

        documents = parse_policy_directory(root)
        chunks = chunk_policy_directory(root)
        document = documents[0]
        context = build_policy_context([PolicyRetrievalResult(chunk=chunks[1], score=1.0)])

        access_context = PolicyAccessContext(
            employee_id="PDF-VERIFY-001",
            department="技术部",
            roles=("EMPLOYEE",),
            security_clearance=SecurityLevel.INTERNAL,
            region="中国大陆",
            identity_source=TrustedIdentitySource.TEST_FIXTURE,
        )
        retriever = PolicyRetriever(
            embedding_provider=_OfflineEmbeddingProvider(),
            chunks=chunks,
        ).restrict(access_context, as_of_date=date(2026, 8, 20))
        secured_results = retriever.search("保密要求", top_k=2)

        scanned_source = root / "scanned.pdf"
        _write_pdf(scanned_source, [[]])
        _write_sidecar(scanned_source)
        ocr_required = False
        try:
            PDFDocumentLoader().load(scanned_source)
        except OCRRequiredError:
            ocr_required = True

        missing_sidecar_source = root / "missing-sidecar.pdf"
        _write_pdf(
            missing_sidecar_source,
            [["第一章 总则", "第一条 范围", "这是一份有原生文本的制度。"]],
        )
        missing_sidecar_rejected = False
        try:
            PDFDocumentLoader().load(missing_sidecar_source)
        except DocumentMetadataError:
            missing_sidecar_rejected = True

        citation = context.citations[0]
        checks = {
            "pdf_discovered_without_sidecar_as_document": len(documents) == 1,
            "trusted_sidecar_metadata_applied": (
                document.document_id == "PDF_VERIFY_001"
                and document.metadata_source_path == pdf_metadata_sidecar_path(source)
            ),
            "native_text_extracted": (
                document.source_loader_name == "pymupdf" and "## 第一章 总则" in document.content
            ),
            "existing_chunker_reused": (
                len(chunks) == 2
                and [chunk.article_label for chunk in chunks] == ["第一条", "第二条"]
            ),
            "page_provenance_preserved": (
                chunks[0].source_page_start == 1
                and chunks[1].source_page_start == 2
                and citation.source_page_start == 2
            ),
            "authorization_still_precedes_similarity": (
                retriever.allowed_chunk_count == 2 and len(secured_results) == 2
            ),
            "scanned_pdf_requests_future_ocr": ocr_required,
            "missing_sidecar_is_rejected": missing_sidecar_rejected,
        }

        return {
            "schema_version": "1.0",
            "phase": 23,
            "passed": all(checks.values()),
            "parser": "pymupdf-native-text",
            "document_count": len(documents),
            "page_count": document.source_page_count,
            "chunk_count": len(chunks),
            "ocr_executed": False,
            "network_calls": False,
            "model_calls": False,
            "checks": checks,
        }


def main() -> int:
    try:
        report = run_verification()
    except (ImportError, OSError, ValueError) as exc:
        report = {
            "schema_version": "1.0",
            "phase": 23,
            "passed": False,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
