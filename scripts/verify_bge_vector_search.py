from app.rag.embeddings import BGEEmbeddingProvider
from app.rag.vector_index import InMemoryVectorIndex, VectorRecord

DOCUMENTS = [
    (
        "travel-policy-article-8",
        "员工因公出差发生的住宿费，应当在规定标准内凭住宿发票报销。",
        {
            "policy_name": "差旅报销制度",
            "article": "第八条",
        },
    ),
    (
        "purchase-policy-article-5",
        "单笔采购金额达到五万元的，申请部门应提交采购申请并履行审批流程。",
        {
            "policy_name": "采购管理办法",
            "article": "第五条",
        },
    ),
    (
        "security-policy-article-12",
        "员工不得向任何人泄露账号、密码、验证码等信息安全凭证。",
        {
            "policy_name": "信息安全制度",
            "article": "第十二条",
        },
    ),
    (
        "leave-policy-article-6",
        "员工申请年休假，应提前提交请假申请并经直属负责人批准。",
        {
            "policy_name": "请假管理制度",
            "article": "第六条",
        },
    ),
]


def build_index(
    provider: BGEEmbeddingProvider,
) -> InMemoryVectorIndex:
    """将测试制度条款转换为向量并加入索引。"""
    texts = [text for _, text, _ in DOCUMENTS]
    vectors = provider.embed_documents(texts)

    records = [
        VectorRecord(
            record_id=record_id,
            text=text,
            vector=vector,
            metadata=metadata,
        )
        for (record_id, text, metadata), vector in zip(
            DOCUMENTS,
            vectors,
            strict=True,
        )
    ]

    index = InMemoryVectorIndex(dimension=provider.dimension)
    index.add(records)

    return index


def main() -> None:
    print("正在加载 BGE 模型……")

    provider = BGEEmbeddingProvider(
        device="cpu",
        batch_size=8,
    )

    print("正在生成制度条款向量并建立内存索引……")
    index = build_index(provider)

    assert provider.dimension == 512
    assert index.size == len(DOCUMENTS)

    search_cases = [
        (
            "出差住酒店的费用需要什么材料才能报销？",
            "travel-policy-article-8",
        ),
        (
            "采购办公设备之前需要走什么手续？",
            "purchase-policy-article-5",
        ),
        (
            "别人向我要登录验证码，可以告诉他吗？",
            "security-policy-article-12",
        ),
    ]

    print()
    print(f"模型向量维度：{provider.dimension}")
    print(f"索引记录数量：{index.size}")

    for query, expected_record_id in search_cases:
        query_vector = provider.embed_query(query)
        results = index.search(query_vector, top_k=3)

        print()
        print(f"用户问题：{query}")

        for rank, result in enumerate(results, start=1):
            policy_name = result.record.metadata["policy_name"]
            article = result.record.metadata["article"]

            print(
                f"{rank}. score={result.score:.6f} "
                f"source={policy_name} {article}"
            )
            print(f"   {result.record.text}")

        assert results
        assert results[0].record.record_id == expected_record_id, (
            f"查询“{query}”的第一名错误："
            f"期望 {expected_record_id}，"
            f"实际 {results[0].record.record_id}"
        )

    print()
    print("BGE 与内存向量索引集成验证通过。")


if __name__ == "__main__":
    main()