from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from app.research import (
    DisabledWebSearchProvider,
    TavilyWebSearchProvider,
    WebSearchProviderName,
)


@pytest.mark.asyncio
async def test_disabled_provider_never_performs_search() -> None:
    provider = DisabledWebSearchProvider()

    assert provider.available is False
    assert provider.provider_name is WebSearchProviderName.DISABLED
    with pytest.raises(RuntimeError, match="disabled"):
        await provider.search("测试")
    await provider.aclose()


@pytest.mark.asyncio
async def test_tavily_provider_sends_bounded_request_and_normalizes_results() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "  官方   指南 ",
                        "url": "https://example.com/guide",
                        "content": "第一段\n\n 第二段 api_key=public-leak",
                        "score": 0.91,
                        "published_date": "2026-08-01",
                    },
                    {
                        "title": "重复来源",
                        "url": "https://example.com/guide",
                        "content": "不应重复出现",
                        "score": 0.50,
                    },
                    {
                        "title": "补充来源",
                        "url": "https://example.org/reference",
                        "content": "补充摘要",
                        "score": 0.80,
                    },
                ]
            },
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TavilyWebSearchProvider(
        api_key="tvly-test-secret",
        timeout_seconds=2.0,
        max_results=3,
        http_client=client,
    )

    results = await provider.search("最新差旅税务规则")

    assert provider.available is True
    assert provider.provider_name is WebSearchProviderName.TAVILY
    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["authorization"] == "Bearer tvly-test-secret"
    assert captured["payload"] == {
        "query": "最新差旅税务规则",
        "topic": "general",
        "search_depth": "basic",
        "max_results": 3,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
    }
    assert len(results) == 2
    assert results[0].title == "官方 指南"
    assert results[0].snippet == "第一段 第二段 [REDACTED]"
    assert "public-leak" not in results[0].snippet
    assert results[0].published_date == "2026-08-01"
    assert results[1].url == "https://example.org/reference"

    await provider.aclose()
    assert client.is_closed is False
    await client.aclose()


@pytest.mark.asyncio
async def test_tavily_provider_rejects_untrusted_response_shape() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "bad",
                        "url": "file:///etc/passwd",
                        "content": "invalid URL scheme",
                        "score": 0.5,
                    }
                ]
            },
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TavilyWebSearchProvider(
        api_key="test-key",
        http_client=client,
    )

    with pytest.raises(ValidationError):
        await provider.search("测试")

    await client.aclose()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"api_key": "  "}, "api_key"),
        ({"api_key": "key", "timeout_seconds": 0}, "timeout_seconds"),
        ({"api_key": "key", "max_results": 0}, "max_results"),
        ({"api_key": "key", "max_results": 6}, "max_results"),
    ],
)
def test_tavily_provider_rejects_invalid_configuration(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        TavilyWebSearchProvider(**overrides)
