from __future__ import annotations

from pathlib import Path

import pytest

from app.rag.document_loader import (
    DocumentLoaderRegistry,
    EmptyDocumentError,
    LoadedDocument,
    MarkdownDocumentLoader,
    UnsupportedDocumentFormatError,
)
from app.rag.policy_chunker import chunk_policy_directory
from app.rag.policy_parser import parse_policy_directory, parse_policy_file
from app.rag.policy_retriever import PolicyRetriever


class _PolicyFixtureLoader:
    name = "policy-fixture"
    media_type = "text/x-policy-fixture"
    supported_extensions = frozenset({".policy"})

    def load(self, path: Path) -> LoadedDocument:
        return LoadedDocument(
            source_path=path,
            text=path.read_text(encoding="utf-8"),
            media_type=self.media_type,
            loader_name=self.name,
        )


class _OneDimensionalEmbeddingProvider:
    @property
    def dimension(self) -> int:
        return 1

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        del text
        return [1.0]


def _policy_text(*, document_id: str = "LOADER_POLICY_001") -> str:
    return f"""---
document_id: {document_id}
document_type: policy
title: Loader 测试制度
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
---
# Loader 测试制度

## 第一章 总则

### 第一条 目的

本条用于验证统一文档加载边界。
"""


def test_markdown_loader_returns_format_neutral_text(tmp_path: Path) -> None:
    source = tmp_path / "policy.md"
    source.write_bytes(b"\xef\xbb\xbf# Policy\n\nBody")

    loaded = MarkdownDocumentLoader().load(source)

    assert loaded.source_path == source
    assert loaded.text == "# Policy\n\nBody"
    assert loaded.media_type == "text/markdown"
    assert loaded.loader_name == "markdown"


def test_registry_routes_extensions_case_insensitively(tmp_path: Path) -> None:
    source = tmp_path / "POLICY.MD"
    source.write_text("# Policy", encoding="utf-8")
    registry = DocumentLoaderRegistry([MarkdownDocumentLoader()])

    loaded = registry.load(source)

    assert loaded.loader_name == "markdown"
    assert registry.supported_extensions == (".md",)


def test_registry_discovers_only_supported_files_in_stable_order(tmp_path: Path) -> None:
    (tmp_path / "b.md").write_text("# B", encoding="utf-8")
    (tmp_path / "a.MD").write_text("# A", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("ignored", encoding="utf-8")
    (tmp_path / "nested.md").mkdir()
    registry = DocumentLoaderRegistry([MarkdownDocumentLoader()])

    discovered = registry.discover(tmp_path)

    assert [path.name for path in discovered] == ["a.MD", "b.md"]


def test_registry_rejects_unsupported_extension(tmp_path: Path) -> None:
    source = tmp_path / "policy.pdf"
    source.write_bytes(b"%PDF fixture")
    registry = DocumentLoaderRegistry([MarkdownDocumentLoader()])

    with pytest.raises(UnsupportedDocumentFormatError, match=r"\.pdf"):
        registry.load(source)


def test_registry_rejects_duplicate_extension_ownership() -> None:
    with pytest.raises(ValueError, match=r"\.md is already registered"):
        DocumentLoaderRegistry(
            [
                MarkdownDocumentLoader(),
                MarkdownDocumentLoader(),
            ]
        )


def test_markdown_loader_rejects_empty_extraction(tmp_path: Path) -> None:
    source = tmp_path / "empty.md"
    source.write_text(" \n\t", encoding="utf-8")

    with pytest.raises(EmptyDocumentError, match="no usable text"):
        MarkdownDocumentLoader().load(source)


def test_policy_parser_accepts_a_registered_non_markdown_loader(tmp_path: Path) -> None:
    source = tmp_path / "policy.policy"
    source.write_text(_policy_text(), encoding="utf-8")
    registry = DocumentLoaderRegistry([_PolicyFixtureLoader()])

    document = parse_policy_file(source, loader_registry=registry)

    assert document.document_id == "LOADER_POLICY_001"
    assert document.source_path == source
    assert document.content.startswith("# Loader 测试制度")


def test_registered_loader_flows_through_directory_chunking(tmp_path: Path) -> None:
    (tmp_path / "second.policy").write_text(
        _policy_text(document_id="LOADER_POLICY_002"),
        encoding="utf-8",
    )
    (tmp_path / "first.policy").write_text(
        _policy_text(document_id="LOADER_POLICY_001"),
        encoding="utf-8",
    )
    (tmp_path / "ignored.md").write_text("not selected", encoding="utf-8")
    registry = DocumentLoaderRegistry([_PolicyFixtureLoader()])

    documents = parse_policy_directory(tmp_path, loader_registry=registry)
    chunks = chunk_policy_directory(tmp_path, loader_registry=registry)

    assert [document.document_id for document in documents] == [
        "LOADER_POLICY_001",
        "LOADER_POLICY_002",
    ]
    assert [chunk.document_id for chunk in chunks] == [
        "LOADER_POLICY_001",
        "LOADER_POLICY_002",
    ]


def test_registered_loader_reaches_retriever_index_construction(tmp_path: Path) -> None:
    source = tmp_path / "policy.policy"
    source.write_text(_policy_text(), encoding="utf-8")
    registry = DocumentLoaderRegistry([_PolicyFixtureLoader()])

    retriever = PolicyRetriever.from_directory(
        tmp_path,
        embedding_provider=_OneDimensionalEmbeddingProvider(),
        loader_registry=registry,
    )

    assert retriever.size == 1
    assert retriever.search("目的", top_k=1)[0].chunk.document_id == "LOADER_POLICY_001"
