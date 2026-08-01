from collections.abc import Sequence

import pytest

from app.rag.embeddings import EmbeddingProvider, EmbeddingVector


class FakeEmbeddingProvider(EmbeddingProvider):
    """不调用真实模型的测试实现。"""

    @property
    def dimension(self) -> int:
        return 3

    def embed_documents(self, texts: Sequence[str]) -> list[EmbeddingVector]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> EmbeddingVector:
        return self._embed(text)

    def _embed(self, text: str) -> EmbeddingVector:
        return [
            float(len(text)),
            float(text.count("采购")),
            float(text.count("报销")),
        ]


def test_embedding_provider_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        EmbeddingProvider()


def test_embed_documents_preserves_input_order() -> None:
    provider = FakeEmbeddingProvider()

    vectors = provider.embed_documents(["采购申请", "差旅报销"])

    assert vectors == [
        [4.0, 1.0, 0.0],
        [4.0, 0.0, 1.0],
    ]


def test_embed_documents_returns_one_vector_per_text() -> None:
    provider = FakeEmbeddingProvider()
    texts = ["第一条", "第二条", "第三条"]

    vectors = provider.embed_documents(texts)

    assert len(vectors) == len(texts)
    assert all(len(vector) == provider.dimension for vector in vectors)


def test_embed_query_returns_expected_dimension() -> None:
    provider = FakeEmbeddingProvider()

    vector = provider.embed_query("采购报销")

    assert vector == [4.0, 1.0, 1.0]
    assert len(vector) == provider.dimension


def test_embed_empty_document_collection() -> None:
    provider = FakeEmbeddingProvider()

    assert provider.embed_documents([]) == []