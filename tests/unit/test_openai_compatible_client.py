import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.llm.client import ChatMessage
from app.llm.openai_compatible_client import (
    OpenAICompatibleLLMClient,
)


def _build_completion(
    content: str | None,
) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_sends_messages_through_async_sdk() -> None:
    messages: list[ChatMessage] = [
        {
            "role": "system",
            "content": "你是制度问答助手。",
        },
        {
            "role": "user",
            "content": "如何申请年假？",
        },
    ]

    settings = Settings(
        llm_api_key="test-key",
        llm_base_url="https://example.com/v1",
        llm_model="test-model",
        llm_timeout_seconds=15,
        llm_max_retries=1,
        _env_file=None,
    )

    with patch("app.llm.openai_compatible_client.AsyncOpenAI") as sdk_class:
        sdk_client = sdk_class.return_value
        sdk_client.chat.completions.create = AsyncMock(
            return_value=_build_completion("应按规定申请。[S1]")
        )

        client = OpenAICompatibleLLMClient.from_settings(settings)
        answer = asyncio.run(client.chat(messages))

    assert answer == "应按规定申请。[S1]"

    sdk_class.assert_called_once_with(
        api_key="test-key",
        base_url="https://example.com/v1",
        timeout=15.0,
        max_retries=1,
    )

    (
        sdk_client.chat.completions.create.assert_awaited_once_with(
            model="test-model",
            messages=messages,
        )
    )


def test_rejects_response_without_text() -> None:
    with patch("app.llm.openai_compatible_client.AsyncOpenAI") as sdk_class:
        sdk_client = sdk_class.return_value
        sdk_client.chat.completions.create = AsyncMock(return_value=_build_completion(None))

        client = OpenAICompatibleLLMClient(
            api_key="test-key",
            base_url="https://example.com/v1",
            model="test-model",
        )

        with pytest.raises(
            RuntimeError,
            match="no text content",
        ):
            asyncio.run(
                client.chat(
                    [
                        {
                            "role": "user",
                            "content": "测试问题",
                        }
                    ]
                )
            )
