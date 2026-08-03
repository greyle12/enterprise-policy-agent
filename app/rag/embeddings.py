from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Protocol, cast

EmbeddingVector = list[float]

DEFAULT_BGE_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
BGE_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


class EmbeddingProvider(ABC):
    """文本向量模型的统一接口。"""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """返回向量维度。"""

    @abstractmethod
    def embed_documents(self, texts: Sequence[str]) -> list[EmbeddingVector]:
        """将多段制度文本转换成向量，输出顺序必须与输入一致。"""

    @abstractmethod
    def embed_query(self, text: str) -> EmbeddingVector:
        """将单条用户查询转换成向量。"""


class _ArrayLike(Protocol):
    def tolist(self) -> object:
        """将模型输出转换成 Python 列表。"""


class _SentenceTransformerLike(Protocol):
    def get_embedding_dimension(self) -> int | None:
        """返回模型向量维度。"""

    def encode(
        self,
        sentences: str | list[str],
        *,
        batch_size: int,
        show_progress_bar: bool,
        convert_to_numpy: bool,
        normalize_embeddings: bool,
    ) -> _ArrayLike:
        """生成文本向量。"""


class BGEEmbeddingProvider(EmbeddingProvider):
    """基于 BAAI BGE 中文模型的 Embedding 实现。"""

    def __init__(
        self,
        model_name: str = DEFAULT_BGE_MODEL_NAME,
        device: str | None = None,
        batch_size: int = 32,
        model: _SentenceTransformerLike | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be greater than zero")

        if model is None:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(model_name, device=device)

        dimension = model.get_embedding_dimension()
        if dimension is None:
            raise ValueError("embedding model did not report its dimension")

        self._model = model
        self._dimension = dimension
        self._batch_size = batch_size

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_documents(self, texts: Sequence[str]) -> list[EmbeddingVector]:
        documents = list(texts)
        if not documents:
            return []

        embeddings = self._model.encode(
            documents,
            batch_size=self._batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return cast(list[EmbeddingVector], embeddings.tolist())

    def embed_query(self, text: str) -> EmbeddingVector:
        query = f"{BGE_QUERY_INSTRUCTION}{text}"

        embedding = self._model.encode(
            query,
            batch_size=self._batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return cast(EmbeddingVector, embedding.tolist())