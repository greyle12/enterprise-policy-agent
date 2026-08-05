from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)


class Settings(BaseSettings):
    """应用运行配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_api_key: SecretStr
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    llm_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
    )
    llm_max_retries: int = Field(
        default=2,
        ge=0,
    )


@lru_cache
def get_settings() -> Settings:
    """读取并缓存应用配置。"""

    return Settings()