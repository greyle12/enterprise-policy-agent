from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from app.rag.policy_retriever import PolicyRetrievalResult
from app.security import PromptInjectionGuard


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
    quarantined_chunk_count: int = 0


def _context_record(
    result: PolicyRetrievalResult,
    *,
    source_id: str,
) -> dict[str, str]:
    chunk = result.chunk

    article_reference = " ".join(
        part
        for part in (
            chunk.article_label,
            chunk.article_title,
        )
        if part
    )

    return {
        "source_id": source_id,
        "document_title": chunk.document_title,
        "document_version": chunk.document_version,
        "chapter_title": chunk.chapter_title,
        "article": article_reference,
        "chunk_id": chunk.chunk_id,
        "content": chunk.content,
    }


def build_policy_context(
    results: Sequence[PolicyRetrievalResult],
    *,
    max_chunks: int = 5,
    prompt_guard: PromptInjectionGuard | None = None,
) -> PolicyContext:
    """Quarantine poisoned chunks and serialize evidence as an explicit data payload."""

    if max_chunks < 1:
        raise ValueError("max_chunks must be greater than zero")

    selected_results: list[PolicyRetrievalResult] = []
    seen_chunk_ids: set[str] = set()
    quarantined_chunk_count = 0
    guard = prompt_guard or PromptInjectionGuard()

    for result in results:
        chunk_id = result.chunk.chunk_id

        if chunk_id in seen_chunk_ids:
            continue

        seen_chunk_ids.add(chunk_id)
        chunk = result.chunk
        assessment = guard.assess_evidence(
            "\n".join(
                (
                    chunk.document_title,
                    chunk.chapter_title,
                    chunk.article_label,
                    chunk.article_title,
                    chunk.content,
                )
            )
        )
        if assessment.blocked:
            quarantined_chunk_count += 1
            continue
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

    context_records = [
        _context_record(
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
        text=json.dumps(
            context_records,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        citations=citations,
        quarantined_chunk_count=quarantined_chunk_count,
    )
