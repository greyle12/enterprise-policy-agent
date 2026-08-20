from __future__ import annotations

import time
from pathlib import Path

from app.rag.embeddings import BGEEmbeddingProvider
from app.rag.policy_retriever import PolicyRetriever

PROJECT_ROOT = Path(__file__).resolve().parents[1]
POLICY_DIRECTORY = PROJECT_ROOT / "data" / "policies"

TEST_CASES: tuple[tuple[str, str], ...] = (
    (
        "出差到上海住酒店，可以报销多少住宿费？",
        "差旅报销管理制度",
    ),
    (
        "购买办公设备需要走什么采购审批流程？",
        "采购管理办法",
    ),
    (
        "同事向我要登录密码和短信验证码，可以给吗？",
        "信息安全管理制度",
    ),
    (
        "员工休年假需要提前多久提交申请？",
        "员工请假管理制度",
    ),
)


def _preview(text: str, *, limit: int = 180) -> str:
    normalized = " ".join(text.split())

    if len(normalized) <= limit:
        return normalized

    return f"{normalized[:limit]}…"


def main() -> None:
    total_started_at = time.perf_counter()

    print("正在加载 BGE 模型……")
    model_started_at = time.perf_counter()
    embedding_provider = BGEEmbeddingProvider()
    model_elapsed = time.perf_counter() - model_started_at

    print("正在解析制度并建立 PolicyRetriever……")
    index_started_at = time.perf_counter()
    retriever = PolicyRetriever.from_directory(
        POLICY_DIRECTORY,
        embedding_provider=embedding_provider,
    )
    index_elapsed = time.perf_counter() - index_started_at

    print(f"\n模型向量维度：{retriever.dimension}")
    print(f"索引记录数量：{retriever.size}")
    print(f"模型加载耗时：{model_elapsed:.2f} 秒")
    print(f"向量生成及建索引耗时：{index_elapsed:.2f} 秒")

    failures: list[str] = []

    for question, expected_title in TEST_CASES:
        results = retriever.search(
            question,
            top_k=3,
        )

        print(f"\n用户问题：{question}")

        for rank, result in enumerate(results, start=1):
            chunk = result.chunk

            print(
                f"{rank}. score={result.score:.6f} "
                f"source={chunk.document_title} "
                f"{chunk.article_label}"
            )
            print(f"   章节：{chunk.chapter_title}")
            print(f"   内容：{_preview(chunk.content)}")
            print(f"   Chunk：{chunk.chunk_id}")

        matched_titles = {result.chunk.document_title for result in results}

        if expected_title not in matched_titles:
            actual_titles = ", ".join(sorted(matched_titles))
            failures.append(f"{question}：期望 {expected_title}，实际 Top-3 为 {actual_titles}")

    if failures:
        failure_details = "\n".join(failures)
        raise AssertionError(f"以下问题未在 Top-3 命中正确制度：\n{failure_details}")

    total_elapsed = time.perf_counter() - total_started_at

    print("\n真实制度批量向量检索验证通过。")
    print(f"总耗时：{total_elapsed:.2f} 秒")


if __name__ == "__main__":
    main()
