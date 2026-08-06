from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.agent.intent import IntentClassification, IntentType
from app.agent.router import (
    AgentResponseStatus,
    AgentRouteResult,
)
from app.api.dependencies import get_agent_router
from app.main import create_app
from app.rag.policy_context import PolicyCitation

app = create_app(enable_lifespan=False)


class FakeAgentRouter:
    def __init__(self, result: AgentRouteResult) -> None:
        self.result = result
        self.calls: list[str] = []

    async def route(
        self,
        user_input: str,
    ) -> AgentRouteResult:
        self.calls.append(user_input)
        return self.result


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _use_fake_router(router: FakeAgentRouter) -> None:
    def provide_router() -> FakeAgentRouter:
        return router

    app.dependency_overrides[get_agent_router] = provide_router


def test_routes_agent_message_and_returns_citations() -> None:
    citation = PolicyCitation(
        source_id="S1",
        chunk_id="travel-001",
        document_title="差旅报销制度",
        chapter_title="住宿标准",
        article_label="第十条",
        article_title="住宿费",
        score=0.98,
    )
    router = FakeAgentRouter(
        AgentRouteResult(
            request="出差住宿标准是多少？",
            classification=IntentClassification(
                intent=IntentType.POLICY_QUERY,
                confidence=0.98,
                reason="查询制度住宿标准",
            ),
            status=AgentResponseStatus.COMPLETED,
            reply="普通员工住宿标准为500元。[S1]",
            citations=(citation,),
        )
    )
    _use_fake_router(router)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/messages",
            json={
                "message": "  出差住宿标准是多少？  "
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "request": "出差住宿标准是多少？",
        "classification": {
            "intent": "policy_query",
            "confidence": 0.98,
            "reason": "查询制度住宿标准",
        },
        "status": "completed",
        "reply": "普通员工住宿标准为500元。[S1]",
        "citations": ["S1"],
    }
    assert router.calls == ["出差住宿标准是多少？"]


def test_returns_unavailable_without_citations() -> None:
    router = FakeAgentRouter(
        AgentRouteResult(
            request="报销需要哪些材料？",
            classification=IntentClassification(
                intent=IntentType.MATERIAL_CHECK,
                confidence=0.96,
                reason="询问报销材料",
            ),
            status=AgentResponseStatus.UNAVAILABLE,
            reply="材料检查能力暂不可用。",
        )
    )
    _use_fake_router(router)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/messages",
            json={"message": "报销需要哪些材料？"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["citations"] == []


def test_rejects_blank_agent_message() -> None:
    router = FakeAgentRouter(
        AgentRouteResult(
            request="不应调用",
            classification=IntentClassification(
                intent=IntentType.UNKNOWN,
                confidence=1.0,
                reason="不应调用",
            ),
            status=AgentResponseStatus.NEEDS_CLARIFICATION,
            reply="不应调用",
        )
    )
    _use_fake_router(router)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/messages",
            json={"message": "   "},
        )

    assert response.status_code == 422
    assert router.calls == []
