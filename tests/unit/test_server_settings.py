from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.fixture(autouse=True)
def clear_server_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APP_HOST", raising=False)
    monkeypatch.delenv("APP_PORT", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)


def test_server_settings_use_safe_local_defaults() -> None:
    settings = Settings(
        llm_api_key="test-key",
        _env_file=None,
    )

    assert settings.app_host == "127.0.0.1"
    assert settings.app_port == 8000
    assert settings.log_level == "INFO"


def test_server_settings_load_container_bind_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_HOST", "0.0.0.0")
    monkeypatch.setenv("APP_PORT", "9000")

    settings = Settings(
        llm_api_key="test-key",
        _env_file=None,
    )

    assert settings.app_host == "0.0.0.0"
    assert settings.app_port == 9000


@pytest.mark.parametrize("port", [0, 65536])
def test_server_settings_reject_invalid_ports(port: int) -> None:
    with pytest.raises(ValidationError):
        Settings(
            llm_api_key="test-key",
            app_port=port,
            _env_file=None,
        )


def test_server_settings_normalize_log_level() -> None:
    settings = Settings(
        llm_api_key="test-key",
        log_level="warning",
        _env_file=None,
    )

    assert settings.log_level == "WARNING"


def test_server_settings_reject_unknown_log_level() -> None:
    with pytest.raises(ValidationError):
        Settings(
            llm_api_key="test-key",
            log_level="verbose",
            _env_file=None,
        )
