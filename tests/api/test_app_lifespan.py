from __future__ import annotations

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

    application = main_module.create_app()

    with TestClient(application):
        assert (
            application.state.policy_answer_service
            is service
        )
        assert llm_client.closed is False

    assert llm_client.closed is True
    assert not hasattr(
        application.state,
        "policy_answer_service",
    )