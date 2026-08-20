from __future__ import annotations

import re
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.memory.conversation import sanitize_memory_content
from app.research.models import (
    WebSearchProviderName,
    WebSearchResult,
)

_TAVILY_SEARCH_ENDPOINT = "https://api.tavily.com/search"
_WHITESPACE_PATTERN = re.compile(r"\s+")
_MAX_TITLE_CHARACTERS = 200
_MAX_SNIPPET_CHARACTERS = 600


class WebSearchProvider(Protocol):
    """研究助手依赖的最小异步外部搜索接口。"""

    provider_name: WebSearchProviderName
    available: bool

    async def search(self, query: str) -> tuple[WebSearchResult, ...]:
        """搜索公开网页并返回受限、结构化摘要。"""

        ...

    async def aclose(self) -> None:
        """释放 Provider 持有的网络资源。"""

        ...


class DisabledWebSearchProvider:
    """默认关闭的 Provider，防止未配置时意外外发数据。"""

    provider_name = WebSearchProviderName.DISABLED
    available = False

    async def search(self, query: str) -> tuple[WebSearchResult, ...]:
        del query
        raise RuntimeError("web search provider is disabled")

    async def aclose(self) -> None:
        return None


class _TavilyResultPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1)
    url: HttpUrl
    content: str = ""
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    published_date: str | None = None


class _TavilySearchPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: list[_TavilyResultPayload]


def _normalize_text(value: str, *, limit: int) -> str:
    normalized = _WHITESPACE_PATTERN.sub(" ", value).strip()
    if not normalized:
        return ""
    sanitized, _, _ = sanitize_memory_content(
        normalized,
        character_limit=limit,
    )
    return sanitized


class TavilyWebSearchProvider:
    """通过 Tavily HTTP Search API 获取公开网页摘要。"""

    provider_name = WebSearchProviderName.TAVILY
    available = True

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 10.0,
        max_results: int = 3,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        normalized_key = api_key.strip()
        if not normalized_key:
            raise ValueError("api_key must not be blank")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if not 1 <= max_results <= 5:
            raise ValueError("max_results must be between one and five")

        self._api_key = normalized_key
        self._timeout_seconds = timeout_seconds
        self._max_results = max_results
        self._owns_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=False,
        )

    async def search(self, query: str) -> tuple[WebSearchResult, ...]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be blank")

        response = await self._http_client.post(
            _TAVILY_SEARCH_ENDPOINT,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "query": normalized_query,
                "topic": "general",
                "search_depth": "basic",
                "max_results": self._max_results,
                "include_answer": False,
                "include_raw_content": False,
                "include_images": False,
            },
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        payload = _TavilySearchPayload.model_validate(response.json())

        results: list[WebSearchResult] = []
        seen_urls: set[str] = set()
        for item in payload.results:
            url = str(item.url)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            results.append(
                WebSearchResult(
                    title=_normalize_text(
                        item.title,
                        limit=_MAX_TITLE_CHARACTERS,
                    ),
                    url=url,
                    snippet=_normalize_text(
                        item.content,
                        limit=_MAX_SNIPPET_CHARACTERS,
                    ),
                    score=item.score,
                    published_date=(
                        _normalize_text(item.published_date, limit=64)
                        if item.published_date
                        else None
                    ),
                )
            )
            if len(results) == self._max_results:
                break
        return tuple(results)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http_client.aclose()
