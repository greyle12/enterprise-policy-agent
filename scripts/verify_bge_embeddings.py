from math import sqrt

from app.rag.embeddings import BGEEmbeddingProvider, EmbeddingVector


def calculate_norm(vector: EmbeddingVector) -> float:
    """计算向量的 L2 范数。"""
    return sqrt(sum(value * value for value in vector))


def calculate_dot_product(
    left: EmbeddingVector,
    right: EmbeddingVector,
) -> float:
    """计算两个已归一化向量的点积，即余弦相似度。"""
    return sum(
        left_value * right_value
        for left_value, right_value in zip(left, right, strict=True)
    )


def main() -> None:
    print("正在加载 BGE 模型，首次运行需要下载模型文件……")

    provider = BGEEmbeddingProvider(
        device="cpu",
        batch_size=8,
    )

    query = "出差住宿费怎么报销？"

    documents = [
        "员工因公出差产生的住宿费，应当在规定标准内凭住宿发票报销。",
        "采购金额达到十万元的，应当履行采购申请和审批流程。",
        "员工不得向外部人员泄露账号、密码及其他信息安全凭证。",
    ]

    query_vector = provider.embed_query(query)
    document_vectors = provider.embed_documents(documents)

    all_vectors = [query_vector, *document_vectors]
    norms = [calculate_norm(vector) for vector in all_vectors]

    scores = [
        calculate_dot_product(query_vector, document_vector)
        for document_vector in document_vectors
    ]

    ranking = sorted(
        enumerate(scores),
        key=lambda item: item[1],
        reverse=True,
    )

    print()
    print(f"模型向量维度：{provider.dimension}")
    print(f"查询向量维度：{len(query_vector)}")
    print(f"文档向量数量：{len(document_vectors)}")
    print(f"向量 L2 范数：{[round(norm, 6) for norm in norms]}")

    print()
    print("语义相似度排序：")

    for rank, (document_index, score) in enumerate(ranking, start=1):
        print(
            f"{rank}. score={score:.6f} "
            f"document={documents[document_index]}"
        )

    assert provider.dimension == 512
    assert len(query_vector) == provider.dimension
    assert all(
        len(vector) == provider.dimension
        for vector in document_vectors
    )
    assert all(abs(norm - 1.0) < 1e-4 for norm in norms)
    assert ranking[0][0] == 0

    print()
    print("BGE 真实模型验证通过。")


if __name__ == "__main__":
    main()