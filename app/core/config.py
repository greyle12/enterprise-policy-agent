from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, model_validator
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

    app_host: str = Field(
        default="127.0.0.1",
        min_length=1,
    )
    app_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
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
    agent_safe_tool_timeout_seconds: float = Field(
        default=65.0,
        gt=0,
    )
    agent_mutation_tool_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
    )
    agent_tool_max_attempts: int = Field(
        default=3,
        ge=1,
    )
    agent_retry_min_wait_seconds: float = Field(
        default=0.1,
        ge=0,
    )
    agent_retry_max_wait_seconds: float = Field(
        default=1.0,
        ge=0,
    )
    sqlite_database_path: Path = Path("data/runtime/enterprise_policy_agent.db")

    @model_validator(mode="after")
    def validate_agent_retry_wait_range(self) -> Self:
        if self.agent_retry_max_wait_seconds < self.agent_retry_min_wait_seconds:
            raise ValueError(
                "agent_retry_max_wait_seconds must be greater than or equal to "
                "agent_retry_min_wait_seconds"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """读取并缓存应用配置。"""

    return Settings()
