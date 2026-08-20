from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_policy_answer_service,
)
from app.main import create_app

app = create_app(enable_lifespan=False)


class FakePolicyAnswerService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def answer(
        self,
        question: str,
    ) -> SimpleNamespace:
        self.calls.append(question)

        return SimpleNamespace(
            question=question,
            answer="普通员工住宿标准为500元。[S1]",
            citations=(SimpleNamespace(source_id="S1"),),
        )


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _use_fake_service(
    service: FakePolicyAnswerService,
) -> None:
    def provide_service() -> FakePolicyAnswerService:
        return service

    app.dependency_overrides[get_policy_answer_service] = provide_service


def test_answers_policy_question() -> None:
    service = FakePolicyAnswerService()
    _use_fake_service(service)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/policy-answers",
            json={"question": ("  出差住宿标准是多少？  ")},
        )

    assert response.status_code == 200
    assert response.json() == {
        "question": "出差住宿标准是多少？",
        "answer": "普通员工住宿标准为500元。[S1]",
        "citations": ["S1"],
    }
    assert service.calls == ["出差住宿标准是多少？"]


def test_rejects_blank_question() -> None:
    service = FakePolicyAnswerService()
    _use_fake_service(service)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/policy-answers",
            json={"question": "   "},
        )

    assert response.status_code == 422
    assert service.calls == []
