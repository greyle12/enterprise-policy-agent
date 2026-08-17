from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_policy_answer_service
from app.llm import ProviderOverloadedError, ProviderQueueTimeoutError
from app.main import create_app

app = create_app(enable_lifespan=False)


class FailingPolicyAnswerService:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def answer(self, question: str) -> None:
        del question
        raise self._error


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ProviderOverloadedError(), "llm_provider_overloaded"),
        (ProviderQueueTimeoutError(), "llm_provider_queue_timeout"),
    ],
)
def test_capacity_errors_return_safe_retryable_503(
    error: Exception,
    code: str,
) -> None:
    secret_question = "机密项目 X-927 的预算是多少？"
    service = FailingPolicyAnswerService(error)
    app.dependency_overrides[get_policy_answer_service] = lambda: service

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/policy-answers",
            json={"question": secret_question},
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == code
    assert secret_question not in response.text
    assert "X-927" not in response.text
