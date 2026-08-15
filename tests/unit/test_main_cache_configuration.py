from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import main as main_module
from app.cache import CacheProviderName, DisabledLLMCache


def test_builds_disabled_cache_without_importing_redis_client() -> None:
    settings = SimpleNamespace(llm_cache_provider=CacheProviderName.DISABLED)

    backend = main_module._build_llm_cache_backend(settings)

    assert isinstance(backend, DisabledLLMCache)


def test_builds_redis_cache_with_bounded_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    observed: dict[str, object] = {}

    def from_url(**kwargs: object) -> object:
        observed.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        main_module,
        "RedisLLMCache",
        SimpleNamespace(from_url=from_url),
    )
    settings = SimpleNamespace(
        llm_cache_provider=CacheProviderName.REDIS,
        redis_url="rediss://cache.example.com:6380/2",
        llm_cache_namespace="company:agent:llm:v2",
        redis_timeout_seconds=0.5,
        llm_cache_max_value_bytes=131_072,
    )

    backend = main_module._build_llm_cache_backend(settings)

    assert backend is sentinel
    assert observed == {
        "url": "rediss://cache.example.com:6380/2",
        "namespace": "company:agent:llm:v2",
        "timeout_seconds": 0.5,
        "max_value_bytes": 131_072,
    }
