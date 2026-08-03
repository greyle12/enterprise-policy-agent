from __future__ import annotations

from collections import Counter
from pathlib import Path
from time import perf_counter

from app.rag.embeddings import BGEEmbeddingProvider
from app.rag.policy_chunker import chunk_policy_directory
from app.rag.vector_index import (
    InMemoryVectorIndex,
    VectorRecord,
)
from app.schemas.chunk import PolicyChunk

POLICY_DIRECTORY = Path("data/policies")

EXPECTED_DOCUMENT_COUNT = 5
EXPECTED_CHUNK_COUNT = 199

SEARCH_CASES: list[tuple[str, str]] = [
    (
        "出差到上海住酒店，可以报销多少住宿费？",
        "差旅",
    ),
    (
        "购买办公设备需要走什么采购审批流程？",
        "采购",
    ),
    (
        "同事向我要登录密码和短信验证码，可以给吗？",
        "信息安全",
    ),
    (
        "员工休年假需要提前多久提交申请？",
        "请假",
    ),
]


def build_record_metadata(
    chunk: PolicyChunk,
) -> dict[str, str]:
    """将 PolicyChunk 的引用字段转换为索引元数据。"""

    return {
        "document_id": chunk.document_id,
        "document_title": chunk.document_title,
        "document_version": chunk.document_version,
        "document_status": chunk.document_status.value,
        "issuing_department": chunk.issuing_department,
        "chapter_title": chunk.chapter_title,
        "article_label": chunk.article_label,
        "article_title": chunk.article_title,
        "source_path": str(chunk.source_path),
        "source_line_start": str(
            chunk.source_line_start
        ),
        "source_line_end": str(
            chunk.source_line_end
        ),
        "security_level": chunk.security_level.value,
        "content_hash": chunk.content_hash,
    }


def build_policy_index(
    *,
    provider: BGEEmbeddingProvider,
    chunks: list[PolicyChunk],
) -> InMemoryVectorIndex:
    """为真实制度 Chunk 生成向量并建立索引。"""

    retrieval_texts = [
        chunk.retrieval_text
        for chunk in chunks
    ]

    vectors = provider.embed_documents(
        retrieval_texts
    )

    if len(vectors) != len(chunks):
        raise RuntimeError(
            "Embedding 数量与 Chunk 数量不一致："
            f"{len(vectors)} != {len(chunks)}"
        )

    records = [
        VectorRecord(
            record_id=chunk.chunk_id,
            text=chunk.content,
            vector=vector,
            metadata=build_record_metadata(chunk),
        )
        for chunk, vector in zip(
            chunks,
            vectors,
            strict=True,
        )
    ]

    index = InMemoryVectorIndex(
        dimension=provider.dimension
    )
    index.add(records)

    return index


def make_preview(
    text: str,
    *,
    limit: int = 160,
) -> str:
    """将多行条款压缩成便于终端展示的摘要。"""

    normalized = " ".join(text.split())

    if len(normalized) <= limit:
        return normalized

    return normalized[: limit - 1] + "…"


def main() -> None:
    total_started_at = perf_counter()

    print("正在解析并切分真实制度文件……")

    chunks = chunk_policy_directory(
        POLICY_DIRECTORY
    )

    document_ids = {
        chunk.document_id
        for chunk in chunks
    }

    document_chunk_counts = Counter(
        chunk.document_title
        for chunk in chunks
    )

    print()
    print(f"制度文件数量：{len(document_ids)}")
    print(f"制度 Chunk 数量：{len(chunks)}")

    for title, count in sorted(
        document_chunk_counts.items()
    ):
        print(f"- {title}：{count} 个 Chunk")

    assert len(document_ids) == EXPECTED_DOCUMENT_COUNT
    assert len(chunks) == EXPECTED_CHUNK_COUNT

    print()
    print("正在加载 BGE 模型……")

    provider = BGEEmbeddingProvider(
        device="cpu",
        batch_size=16,
    )

    print("正在为 199 个 Chunk 生成向量……")
    embedding_started_at = perf_counter()

    index = build_policy_index(
        provider=provider,
        chunks=chunks,
    )

    embedding_seconds = (
        perf_counter() - embedding_started_at
    )

    assert provider.dimension == 512
    assert index.size == EXPECTED_CHUNK_COUNT

    print()
    print(f"模型向量维度：{provider.dimension}")
    print(f"索引记录数量：{index.size}")
    print(
        "向量生成及建索引耗时："
        f"{embedding_seconds:.2f} 秒"
    )

    for query, expected_title_keyword in SEARCH_CASES:
        query_vector = provider.embed_query(query)
        results = index.search(
            query_vector,
            top_k=3,
        )

        print()
        print(f"用户问题：{query}")

        for rank, result in enumerate(
            results,
            start=1,
        ):
            metadata = result.record.metadata

            print(
                f"{rank}. score={result.score:.6f} "
                f"source="
                f"{metadata['document_title']} "
                f"{metadata['article_label']}"
            )
            print(
                f"   章节："
                f"{metadata['chapter_title']}"
            )
            print(
                f"   内容："
                f"{make_preview(result.record.text)}"
            )
            print(
                f"   Chunk："
                f"{result.record.record_id}"
            )

        matched_expected_policy = any(
            expected_title_keyword
            in result.record.metadata[
                "document_title"
            ]
            for result in results
        )

        assert matched_expected_policy, (
            f"查询“{query}”的 Top-3 中没有找到"
            f"预期制度：{expected_title_keyword}"
        )

    total_seconds = (
        perf_counter() - total_started_at
    )

    print()
    print("真实制度批量向量检索验证通过。")
    print(f"总耗时：{total_seconds:.2f} 秒")


if __name__ == "__main__":
    main()