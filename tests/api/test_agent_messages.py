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
from app.tools.material_models import (
    ApplicationType,
    MaterialCheckMode,
    MaterialCheckResult,
    MaterialRequirement,
    MissingMaterial,
    ProvidedMaterial,
)

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
            request="采购需要走什么审批？",
            classification=IntentClassification(
                intent=IntentType.APPROVAL_QUERY,
                confidence=0.96,
                reason="询问采购审批",
            ),
            status=AgentResponseStatus.UNAVAILABLE,
            reply="审批判断能力暂不可用。",
        )
    )
    _use_fake_router(router)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/messages",
            json={"message": "采购需要走什么审批？"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["citations"] == []


def test_serializes_structured_material_check_result() -> None:
    citation = PolicyCitation(
        source_id="S1",
        chunk_id="travel-materials-001",
        document_title="差旅报销管理制度",
        chapter_title="第六章 报销材料",
        article_label="第十六条",
        article_title="必备报销材料",
        score=1.0,
    )
    material_check = MaterialCheckResult(
        application_type=(
            ApplicationType.TRAVEL_REIMBURSEMENT
        ),
        mode=MaterialCheckMode.COMPARISON,
        required_materials=(
            MaterialRequirement(
                material_type="travel_itinerary",
                display_name="差旅行程单",
                reason="制度要求提供行程单",
            ),
        ),
        provided_materials=(
            ProvidedMaterial(
                material_type="approved_travel_application",
                display_name="已审批的出差申请单",
                provided_count=1,
            ),
        ),
        missing_materials=(
            MissingMaterial(
                material_type="travel_itinerary",
                display_name="差旅行程单",
                missing_count=1,
                reason="制度要求提供行程单",
            ),
        ),
        materials_complete=False,
        clarification_question=None,
        notes=("仅根据当前消息中的材料比对。",),
        citations=(citation,),
    )
    router = FakeAgentRouter(
        AgentRouteResult(
            request="我有出差申请单，还缺什么？",
            classification=IntentClassification(
                intent=IntentType.MATERIAL_CHECK,
                confidence=0.98,
                reason="检查已有差旅材料",
            ),
            status=AgentResponseStatus.COMPLETED,
            reply="还缺差旅行程单。[S1]",
            citations=(citation,),
            material_check=material_check,
        )
    )
    _use_fake_router(router)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/messages",
            json={"message": "我有出差申请单，还缺什么？"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["citations"] == ["S1"]
    assert payload["material_check"] == {
        "application_type": "travel_reimbursement",
        "mode": "comparison",
        "required_materials": [
            {
                "material_type": "travel_itinerary",
                "display_name": "差旅行程单",
                "reason": "制度要求提供行程单",
                "required_count": 1,
                "sensitive": False,
            }
        ],
        "provided_materials": [
            {
                "material_type": (
                    "approved_travel_application"
                ),
                "display_name": "已审批的出差申请单",
                "provided_count": 1,
            }
        ],
        "missing_materials": [
            {
                "material_type": "travel_itinerary",
                "display_name": "差旅行程单",
                "missing_count": 1,
                "reason": "制度要求提供行程单",
                "sensitive": False,
            }
        ],
        "materials_complete": False,
        "notes": ["仅根据当前消息中的材料比对。"],
    }


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
