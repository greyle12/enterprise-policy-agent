from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main as main_module


class FakeLLMClient:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def test_lifespan_configures_and_closes_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = object()
    llm_client = FakeLLMClient()

    def build_fake_service() -> tuple[
        object,
        FakeLLMClient,
    ]:
        return service, llm_client

    monkeypatch.setattr(
        main_module,
        "_build_policy_answer_service",
        build_fake_service,
    )
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: SimpleNamespace(
            sqlite_database_path=tmp_path / "agent.db",
            agent_safe_tool_timeout_seconds=65.0,
            agent_mutation_tool_timeout_seconds=10.0,
            agent_tool_max_attempts=3,
            agent_retry_min_wait_seconds=0.1,
            agent_retry_max_wait_seconds=1.0,
        ),
    )

    application = main_module.create_app()

    with TestClient(application):
        assert application.state.policy_answer_service is service
        assert llm_client.closed is False
        assert application.state.agent_state_store.backend_name == "sqlite"

    assert llm_client.closed is True
    assert not hasattr(
        application.state,
        "policy_answer_service",
    )
    assert not hasattr(
        application.state,
        "agent_state_store",
    )
