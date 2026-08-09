from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings

_ENVIRONMENT_NAMES = (
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_TIMEOUT_SECONDS",
    "LLM_MAX_RETRIES",
    "SQLITE_DATABASE_PATH",
)


@pytest.fixture(autouse=True)
def clear_llm_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in _ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_uses_default_llm_settings() -> None:
    settings = Settings(
        llm_api_key="test-key",
        _env_file=None,
    )

    assert settings.llm_base_url == (
        "https://api.deepseek.com"
    )
    assert settings.llm_model == (
        "deepseek-v4-flash"
    )
    assert settings.llm_timeout_seconds == 60.0
    assert settings.llm_max_retries == 2
    assert settings.sqlite_database_path == Path(
        "data/runtime/enterprise_policy_agent.db"
    )
    assert (
        settings.llm_api_key.get_secret_value()
        == "test-key"
    )


def test_loads_llm_settings_from_env_file(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
    (
        "LLM_API_KEY=env-test-key\n"
        "LLM_BASE_URL=https://example.com/v1\n"
        "LLM_MODEL=test-model\n"
        "LLM_TIMEOUT_SECONDS=15\n"
        "LLM_MAX_RETRIES=1\n"
        "SQLITE_DATABASE_PATH=data/test-agent.db"
    ),
    encoding="utf-8",
)

    settings = Settings(_env_file=env_path)

    assert (
        settings.llm_api_key.get_secret_value()
        == "env-test-key"
    )
    assert settings.llm_base_url == (
        "https://example.com/v1"
    )
    assert settings.llm_model == "test-model"
    assert settings.llm_timeout_seconds == 15.0
    assert settings.llm_max_retries == 1
    assert settings.sqlite_database_path == Path(
        "data/test-agent.db"
    )


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("llm_timeout_seconds", 0),
        ("llm_max_retries", -1),
    ],
)
def test_rejects_invalid_numeric_settings(
    field_name: str,
    invalid_value: int,
) -> None:
    values = {
        "llm_api_key": "test-key",
        field_name: invalid_value,
    }

    with pytest.raises(ValidationError):
        Settings(
            **values,
            _env_file=None,
        )
