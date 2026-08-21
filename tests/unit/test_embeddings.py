from collections.abc import Sequence

import pytest

from app.rag.embeddings import (
    BGE_QUERY_INSTRUCTION,
    BGEEmbeddingProvider,
    EmbeddingProvider,
    EmbeddingVector,
)


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


class FakeArray:
    def __init__(self, values: list[float] | list[list[float]]) -> None:
        self._values = values

    def tolist(self) -> list[float] | list[list[float]]:
        return self._values


class FakeSentenceTransformer:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def get_embedding_dimension(self) -> int:
        return 3

    def encode(
        self,
        sentences: str | list[str],
        **kwargs: object,
    ) -> FakeArray:
        self.calls.append({"sentences": sentences, **kwargs})

        if isinstance(sentences, str):
            return FakeArray([float(len(sentences)), 0.0, 0.0])

        return FakeArray([[float(len(text)), 0.0, 0.0] for text in sentences])


def test_bge_provider_reports_model_dimension() -> None:
    model = FakeSentenceTransformer()
    provider = BGEEmbeddingProvider(model=model)

    assert provider.dimension == 3


def test_bge_provider_embeds_documents_without_query_instruction() -> None:
    model = FakeSentenceTransformer()
    provider = BGEEmbeddingProvider(model=model, batch_size=2)

    vectors = provider.embed_documents(["采购申请", "差旅报销"])

    call = model.calls[0]
    assert call["sentences"] == ["采购申请", "差旅报销"]
    assert call["batch_size"] == 2
    assert call["normalize_embeddings"] is True
    assert call["convert_to_numpy"] is True
    assert call["show_progress_bar"] is False
    assert len(vectors) == 2


def test_bge_provider_adds_instruction_to_query() -> None:
    model = FakeSentenceTransformer()
    provider = BGEEmbeddingProvider(model=model)

    provider.embed_query("差旅住宿标准是多少？")

    call = model.calls[0]
    assert call["sentences"] == (f"{BGE_QUERY_INSTRUCTION}差旅住宿标准是多少？")
    assert call["normalize_embeddings"] is True


def test_bge_provider_skips_model_for_empty_documents() -> None:
    model = FakeSentenceTransformer()
    provider = BGEEmbeddingProvider(model=model)

    assert provider.embed_documents([]) == []
    assert model.calls == []
