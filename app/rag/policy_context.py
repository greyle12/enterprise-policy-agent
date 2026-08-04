from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.rag.policy_retriever import PolicyRetrievalResult


@dataclass(frozen=True, slots=True)
class PolicyCitation:
    """可供回答服务使用的制度引用信息。"""

    source_id: str
    chunk_id: str
    document_title: str
    chapter_title: str
    article_label: str
    article_title: str
    score: float


@dataclass(frozen=True, slots=True)
class PolicyContext:
    """发送给大模型的制度上下文及引用映射。"""

    text: str
    citations: tuple[PolicyCitation, ...]


def _format_context_block(
    result: PolicyRetrievalResult,
    *,
    source_id: str,
) -> str:
    chunk = result.chunk

    article_reference = " ".join(
        part
        for part in (
            chunk.article_label,
            chunk.article_title,
        )
        if part
    )

    return "\n".join(
        (
            f"[{source_id}]",
            (
                f"制度：{chunk.document_title}"
                f"（版本 {chunk.document_version}）"
            ),
            f"章节：{chunk.chapter_title}",
            f"条款：{article_reference}",
            f"Chunk：{chunk.chunk_id}",
            "内容：",
            chunk.content,
        )
    )


def build_policy_context(
    results: Sequence[PolicyRetrievalResult],
    *,
    max_chunks: int = 5,
) -> PolicyContext:
    """将检索结果转换为带编号引用的制度上下文。"""

    if max_chunks < 1:
        raise ValueError(
            "max_chunks must be greater than zero"
        )

    selected_results: list[PolicyRetrievalResult] = []
    seen_chunk_ids: set[str] = set()

    for result in results:
        chunk_id = result.chunk.chunk_id

        if chunk_id in seen_chunk_ids:
            continue

        seen_chunk_ids.add(chunk_id)
        selected_results.append(result)

        if len(selected_results) == max_chunks:
            break

    citations = tuple(
        PolicyCitation(
            source_id=f"S{index}",
            chunk_id=result.chunk.chunk_id,
            document_title=result.chunk.document_title,
            chapter_title=result.chunk.chapter_title,
            article_label=result.chunk.article_label,
            article_title=result.chunk.article_title,
            score=result.score,
        )
        for index, result in enumerate(
            selected_results,
            start=1,
        )
    )

    context_blocks = [
        _format_context_block(
            result,
            source_id=citation.source_id,
        )
        for result, citation in zip(
            selected_results,
            citations,
            strict=True,
        )
    ]

    return PolicyContext(
        text="\n\n".join(context_blocks),
        citations=citations,
    )