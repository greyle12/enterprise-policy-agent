from __future__ import annotations

from collections import Counter
from hashlib import sha256
from pathlib import Path

import pytest

from app.rag.policy_chunker import (
    PolicyChunkingError,
    chunk_policy_directory,
    chunk_policy_document,
    chunk_policy_file,
)
from app.rag.policy_parser import (
    parse_policy_file,
    parse_policy_text,
)

POLICY_DIR = Path("data/policies")


def _build_test_policy(
    body: str,
) -> str:
    return f"""---
document_id: TEST_POLICY_001
document_type: policy
title: 测试制度
version: "1.0"
status: effective
issuing_department: 测试部门
effective_date: 2026-01-01
allowed_departments:
  - ALL
allowed_roles:
  - EMPLOYEE
security_level: internal
region: CN
---
{body}
"""


def test_chunk_single_policy() -> None:
    chunks = chunk_policy_file(POLICY_DIR / "travel_reimbursement_policy_v1.md")

    assert len(chunks) == 26


def test_chunk_all_policy_files() -> None:
    chunks = chunk_policy_directory(POLICY_DIR)

    assert len(chunks) == 199


def test_chunk_count_per_document() -> None:
    chunks = chunk_policy_directory(POLICY_DIR)

    counts = Counter(chunk.document_id for chunk in chunks)

    assert counts == {
        "EXPENSE_REIMBURSEMENT_GUIDE_001": 47,
        "INFORMATION_SECURITY_POLICY_001": 46,
        "LEAVE_POLICY_001": 43,
        "PROCUREMENT_POLICY_001": 37,
        "TRAVEL_POLICY_001": 26,
    }


def test_first_travel_chunk_metadata() -> None:
    chunks = chunk_policy_file(POLICY_DIR / "travel_reimbursement_policy_v1.md")

    first = chunks[0]

    assert first.chunk_index == 1
    assert first.chapter_index == 1
    assert first.chapter_title == "第一章 总则"
    assert first.article_label == "第一条"
    assert first.article_title == "制定目的"
    assert first.document_id == "TRAVEL_POLICY_001"
    assert first.document_title == "差旅报销管理制度"
    assert first.document_version == "1.0"


def test_chunk_ids_are_unique() -> None:
    chunks = chunk_policy_directory(POLICY_DIR)

    chunk_ids = [chunk.chunk_id for chunk in chunks]

    assert len(chunk_ids) == len(set(chunk_ids))


def test_chunk_ids_are_stable() -> None:
    first_run = chunk_policy_directory(POLICY_DIR)
    second_run = chunk_policy_directory(POLICY_DIR)

    assert [chunk.chunk_id for chunk in first_run] == [chunk.chunk_id for chunk in second_run]


def test_article_boundaries_do_not_overlap() -> None:
    chunks = chunk_policy_file(POLICY_DIR / "travel_reimbursement_policy_v1.md")

    first = chunks[0]

    assert "### 第一条 制定目的" in first.content
    assert "### 第二条 适用范围" not in first.content


def test_retrieval_text_contains_context() -> None:
    chunks = chunk_policy_file(POLICY_DIR / "travel_reimbursement_policy_v1.md")

    first = chunks[0]

    assert "制度：差旅报销管理制度" in first.retrieval_text
    assert "章节：第一章 总则" in first.retrieval_text
    assert "条款：第一条 制定目的" in first.retrieval_text
    assert first.content in first.retrieval_text


def test_source_line_range_is_valid() -> None:
    chunks = chunk_policy_directory(POLICY_DIR)

    for chunk in chunks:
        assert chunk.source_line_start >= 1
        assert chunk.source_line_end >= chunk.source_line_start


def test_security_metadata_is_inherited() -> None:
    document = parse_policy_file(POLICY_DIR / "information_security_policy_v1.md")

    chunks = chunk_policy_document(document)

    for chunk in chunks:
        assert chunk.security_level == document.metadata.security_level
        assert chunk.allowed_roles == document.metadata.allowed_roles
        assert chunk.allowed_departments == document.metadata.allowed_departments


def test_content_hash_and_char_count() -> None:
    chunks = chunk_policy_directory(POLICY_DIR)

    for chunk in chunks:
        assert chunk.char_count == len(chunk.content)
        assert chunk.content_hash == sha256(chunk.content.encode("utf-8")).hexdigest()


def test_reject_article_before_chapter() -> None:
    raw_text = _build_test_policy(
        """# 测试制度

### 第一条 错误条款

条款前面没有章节。
"""
    )

    document = parse_policy_text(
        raw_text,
        source_path=Path("invalid_order.md"),
    )

    with pytest.raises(
        PolicyChunkingError,
        match="条款之前没有章节标题",
    ):
        chunk_policy_document(document)


def test_reject_document_without_articles() -> None:
    raw_text = _build_test_policy(
        """# 测试制度

## 第一章 总则

这里只有章节，没有三级条款。
"""
    )

    document = parse_policy_text(
        raw_text,
        source_path=Path("no_articles.md"),
    )

    with pytest.raises(
        PolicyChunkingError,
        match="没有找到可切分",
    ):
        chunk_policy_document(document)
