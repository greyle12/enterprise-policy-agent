"""执行一次真实的 LLM 连接测试。"""

from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings
from app.llm.client import ChatMessage
from app.llm.openai_compatible_client import (
    OpenAICompatibleLLMClient,
)

_LOGGER = logging.getLogger(__name__)


async def main() -> None:
    """发送最小测试请求并输出结果。"""

    settings = get_settings()
    client = OpenAICompatibleLLMClient.from_settings(
        settings
    )

    messages: list[ChatMessage] = [
        {
            "role": "system",
            "content": "你正在接受 API 连接测试。",
        },
        {
            "role": "user",
            "content": "只回复：LLM connection OK",
        },
    ]

    try:
        answer = await client.chat(messages)
    finally:
        await client.close()

    _LOGGER.info("LLM response: %s", answer)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )
    asyncio.run(main())