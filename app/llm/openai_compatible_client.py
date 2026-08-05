from __future__ import annotations

from collections.abc import Sequence
from typing import Self

from openai import AsyncOpenAI

from app.core.config import Settings
from app.llm.client import ChatMessage


class OpenAICompatibleLLMClient:
    """通过 OpenAI-compatible API 调用聊天模型。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        normalized_api_key = api_key.strip()
        normalized_base_url = base_url.strip()
        normalized_model = model.strip()

        if not normalized_api_key:
            raise ValueError(
                "api_key must not be blank"
            )

        if not normalized_base_url:
            raise ValueError(
                "base_url must not be blank"
            )

        if not normalized_model:
            raise ValueError(
                "model must not be blank"
            )

        if timeout_seconds <= 0:
            raise ValueError(
                "timeout_seconds must be greater than zero"
            )

        if max_retries < 0:
            raise ValueError(
                "max_retries must not be negative"
            )

        self._model = normalized_model
        self._client = AsyncOpenAI(
            api_key=normalized_api_key,
            base_url=normalized_base_url,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
    ) -> Self:
        """根据应用配置创建客户端。"""

        return cls(
            api_key=(
                settings.llm_api_key.get_secret_value()
            ),
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout_seconds=(
                settings.llm_timeout_seconds
            ),
            max_retries=settings.llm_max_retries,
        )

    async def chat(
        self,
        messages: Sequence[ChatMessage],
    ) -> str:
        """调用聊天补全接口并返回文本。"""

        request_messages = [
            {
                "role": message["role"],
                "content": message["content"],
            }
            for message in messages
        ]

        response = (
            await self._client.chat.completions.create(
                model=self._model,
                messages=request_messages,
            )
        )

        if not response.choices:
            raise RuntimeError(
                "LLM response contains no choices"
            )

        content = response.choices[0].message.content

        if content is None:
            raise RuntimeError(
                "LLM response contains no text content"
            )

        return content

    async def close(self) -> None:
        """关闭底层 HTTP 客户端。"""

        await self._client.close()