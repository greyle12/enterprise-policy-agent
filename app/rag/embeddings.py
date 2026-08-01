from abc import ABC, abstractmethod
from collections.abc import Sequence

EmbeddingVector = list[float]


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