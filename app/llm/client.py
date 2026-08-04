from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol, TypedDict

ChatRole = Literal["system", "user", "assistant"]


class ChatMessage(TypedDict):
    """一条发送给大模型的聊天消息。"""

    role: ChatRole
    content: str


class LLMClient(Protocol):
    """PolicyAnswerService 依赖的最小异步 LLM 接口。"""

    async def chat(
        self,
        messages: Sequence[ChatMessage],
    ) -> str:
        """发送多轮消息并返回模型生成的文本。"""

        ...