from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from app.rag.document_loader import (
    DEFAULT_DOCUMENT_LOADER_REGISTRY,
    DocumentLoaderRegistry,
)
from app.rag.policy_parser import (
    parse_policy_directory,
    parse_policy_file,
)
from app.schemas.chunk import PolicyChunk
from app.schemas.policy import PolicyDocument

CHINESE_NUMBER_PATTERN = r"[一二三四五六七八九十百千零〇两0-9]+"

CHAPTER_HEADING_PATTERN = re.compile(
    rf"^##\s+"
    rf"(?P<chapter_title>"
    rf"第{CHINESE_NUMBER_PATTERN}章"
    rf"(?:\s+.*?)?"
    rf")\s*$"
)

ARTICLE_HEADING_PATTERN = re.compile(
    rf"^###\s+"
    rf"(?P<article_label>"
    rf"第{CHINESE_NUMBER_PATTERN}条"
    rf")"
    rf"(?:\s+(?P<article_title>.*?))?"
    rf"\s*$"
)


class PolicyChunkingError(ValueError):
    """制度文档无法被正确切片。"""


@dataclass
class _ArticleBuffer:
    chapter_index: int
    chapter_title: str
    article_label: str
    article_title: str
    source_line_start: int
    lines: list[str] = field(default_factory=list)


def _normalize_version(version: str) -> str:
    """
    将版本号转换为适合 chunk_id 的字符串。

    示例：
    1.0 -> 1_0
    v2.1-beta -> v2_1_beta
    """

    normalized = re.sub(
        r"[^A-Za-z0-9]+",
        "_",
        version,
    ).strip("_")

    return normalized or "unknown"


def _build_chunk_id(
    document: PolicyDocument,
    chunk_index: int,
) -> str:
    version = _normalize_version(document.metadata.version)

    return f"{document.metadata.document_id}__v{version}__article_{chunk_index:03d}"


def _trim_article_lines(
    lines: list[str],
) -> list[str]:
    trimmed = list(lines)

    while trimmed and not trimmed[-1].strip():
        trimmed.pop()

    return trimmed


def _build_retrieval_text(
    *,
    document: PolicyDocument,
    chapter_title: str,
    article_label: str,
    article_title: str,
    content: str,
) -> str:
    """
    构造后续用于 Embedding 的文本。

    将制度、章节和条款名称补充到正文前面，
    避免单独检索条款时丢失上下文。
    """

    return "\n".join(
        [
            f"制度：{document.metadata.title}",
            f"制度编号：{document.metadata.document_id}",
            f"版本：{document.metadata.version}",
            f"章节：{chapter_title}",
            f"条款：{article_label} {article_title}",
            "",
            content,
        ]
    ).strip()


def _source_page_range(
    document: PolicyDocument,
    *,
    source_line_start: int,
    source_line_end: int,
) -> tuple[int | None, int | None]:
    if not document.content_page_numbers:
        return None, None

    page_numbers = document.content_page_numbers[source_line_start - 1 : source_line_end]
    if not page_numbers:
        raise PolicyChunkingError("PDF Chunk 没有对应的页码映射")
    return min(page_numbers), max(page_numbers)


def _source_block_range(
    document: PolicyDocument,
    *,
    source_line_start: int,
    source_line_end: int,
) -> tuple[int | None, int | None]:
    if not document.content_block_numbers:
        return None, None

    block_numbers = document.content_block_numbers[source_line_start - 1 : source_line_end]
    if not block_numbers:
        raise PolicyChunkingError("DOCX Chunk 没有对应的来源块序号映射")
    return min(block_numbers), max(block_numbers)


def _finalize_article(
    *,
    document: PolicyDocument,
    buffer: _ArticleBuffer,
    source_line_end: int,
    chunk_index: int,
) -> PolicyChunk:
    article_lines = _trim_article_lines(buffer.lines)

    if not article_lines:
        raise PolicyChunkingError(f"{buffer.article_label} 没有条款内容")

    content = "\n".join(article_lines).strip()

    if not content:
        raise PolicyChunkingError(f"{buffer.article_label} 的正文为空")

    actual_line_end = buffer.source_line_start + len(article_lines) - 1

    actual_line_end = min(
        actual_line_end,
        source_line_end,
    )

    retrieval_text = _build_retrieval_text(
        document=document,
        chapter_title=buffer.chapter_title,
        article_label=buffer.article_label,
        article_title=buffer.article_title,
        content=content,
    )

    metadata = document.metadata
    source_page_start, source_page_end = _source_page_range(
        document,
        source_line_start=buffer.source_line_start,
        source_line_end=actual_line_end,
    )
    source_block_start, source_block_end = _source_block_range(
        document,
        source_line_start=buffer.source_line_start,
        source_line_end=actual_line_end,
    )

    return PolicyChunk(
        chunk_id=_build_chunk_id(
            document,
            chunk_index,
        ),
        chunk_index=chunk_index,
        document_id=metadata.document_id,
        document_type=metadata.document_type,
        document_title=metadata.title,
        document_version=metadata.version,
        document_status=metadata.status,
        issuing_department=metadata.issuing_department,
        chapter_index=buffer.chapter_index,
        chapter_title=buffer.chapter_title,
        article_label=buffer.article_label,
        article_title=buffer.article_title,
        content=content,
        retrieval_text=retrieval_text,
        source_path=document.source_path,
        source_media_type=document.source_media_type,
        metadata_source_path=document.metadata_source_path,
        source_line_start=buffer.source_line_start,
        source_line_end=actual_line_end,
        source_page_start=source_page_start,
        source_page_end=source_page_end,
        source_block_start=source_block_start,
        source_block_end=source_block_end,
        effective_date=metadata.effective_date,
        expiry_date=metadata.expiry_date,
        allowed_departments=list(metadata.allowed_departments),
        allowed_roles=list(metadata.allowed_roles),
        security_level=metadata.security_level,
        region=metadata.region,
        char_count=len(content),
        content_hash=sha256(content.encode("utf-8")).hexdigest(),
    )


def chunk_policy_document(
    document: PolicyDocument,
) -> list[PolicyChunk]:
    """将一份制度按三级条款标题切片。"""

    lines = document.content.splitlines()

    current_chapter_index = 0
    current_chapter_title: str | None = None
    current_article: _ArticleBuffer | None = None

    chunks: list[PolicyChunk] = []

    for line_number, line in enumerate(
        lines,
        start=1,
    ):
        stripped = line.strip()

        chapter_match = CHAPTER_HEADING_PATTERN.match(stripped)

        if chapter_match:
            if current_article is not None:
                chunks.append(
                    _finalize_article(
                        document=document,
                        buffer=current_article,
                        source_line_end=line_number - 1,
                        chunk_index=len(chunks) + 1,
                    )
                )
                current_article = None

            current_chapter_index += 1
            current_chapter_title = chapter_match.group("chapter_title").strip()
            continue

        article_match = ARTICLE_HEADING_PATTERN.match(stripped)

        if article_match:
            if current_chapter_title is None:
                raise PolicyChunkingError(f"发现条款标题，但条款之前没有章节标题：{stripped}")

            if current_article is not None:
                chunks.append(
                    _finalize_article(
                        document=document,
                        buffer=current_article,
                        source_line_end=line_number - 1,
                        chunk_index=len(chunks) + 1,
                    )
                )

            article_label = article_match.group("article_label").strip()

            article_title = (article_match.group("article_title") or article_label).strip()

            current_article = _ArticleBuffer(
                chapter_index=current_chapter_index,
                chapter_title=current_chapter_title,
                article_label=article_label,
                article_title=article_title,
                source_line_start=line_number,
                lines=[line],
            )
            continue

        if current_article is not None:
            current_article.lines.append(line)

    if current_article is not None:
        chunks.append(
            _finalize_article(
                document=document,
                buffer=current_article,
                source_line_end=len(lines),
                chunk_index=len(chunks) + 1,
            )
        )

    if not chunks:
        raise PolicyChunkingError("制度中没有找到可切分的三级条款标题")

    article_labels = [chunk.article_label for chunk in chunks]

    duplicate_labels = sorted(
        label for label, count in Counter(article_labels).items() if count > 1
    )

    if duplicate_labels:
        raise PolicyChunkingError("同一制度中发现重复条款编号：" + ", ".join(duplicate_labels))

    return chunks


def chunk_policy_file(
    path: str | Path,
    *,
    loader_registry: DocumentLoaderRegistry = DEFAULT_DOCUMENT_LOADER_REGISTRY,
) -> list[PolicyChunk]:
    document = parse_policy_file(
        path,
        loader_registry=loader_registry,
    )
    return chunk_policy_document(document)


def chunk_policy_directory(
    directory: str | Path,
    *,
    loader_registry: DocumentLoaderRegistry = DEFAULT_DOCUMENT_LOADER_REGISTRY,
) -> list[PolicyChunk]:
    documents = parse_policy_directory(
        directory,
        loader_registry=loader_registry,
    )

    chunks = [chunk for document in documents for chunk in chunk_policy_document(document)]

    chunk_ids = [chunk.chunk_id for chunk in chunks]

    duplicate_chunk_ids = sorted(
        chunk_id for chunk_id, count in Counter(chunk_ids).items() if count > 1
    )

    if duplicate_chunk_ids:
        raise PolicyChunkingError("发现重复 chunk_id：" + ", ".join(duplicate_chunk_ids))

    return chunks
