from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.agent.intent import IntentClassification, IntentType
from app.agent.router import (
    AgentResponseStatus,
    AgentRouteResult,
    AgentSessionInfo,
    AgentSessionPhase,
    AgentWorkflowNode,
    AgentWorkflowStep,
    AgentWorkflowTrace,
)
from app.api.dependencies import get_agent_router
from app.main import create_app
from app.memory import ConversationMemoryInfo
from app.rag.policy_context import PolicyCitation
from app.tools.approval_models import (
    ApprovalAction,
    ApprovalApplicationType,
    ApprovalCheckResult,
    ApprovalLevel,
    ApprovalStep,
    ApproverCode,
)
from app.tools.draft_models import (
    ApplicationDraft,
    DraftAuditMetadata,
    DraftField,
    DraftFieldSource,
    DraftGenerationResult,
    DraftPolicySnapshot,
    DraftStatus,
    DraftUserContext,
)
from app.tools.material_models import (
    ApplicationType,
    MaterialCheckMode,
    MaterialCheckResult,
    MaterialRequirement,
    MissingMaterial,
    ProvidedMaterial,
)
from app.tools.submission_models import (
    ApprovalWorkflowStepStatus,
    MockApprovalSubmissionResult,
    SubmissionAuditEvent,
    SubmissionAuditRecord,
    SubmissionStatus,
    SubmittedApplication,
    SubmittedApprovalStep,
    SubmittedApprovalWorkflow,
)

app = create_app(enable_lifespan=False)


class FakeAgentRouter:
    def __init__(self, result: AgentRouteResult) -> None:
        self.result = result
        self.calls: list[str] = []
        self.session_ids: list[str | None] = []

    async def route(
        self,
        user_input: str,
        *,
        session_id: str | None = None,
    ) -> AgentRouteResult:
        self.calls.append(user_input)
        self.session_ids.append(session_id)
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
            workflow=AgentWorkflowTrace(
                name="enterprise_policy_workflow",
                version="1.1",
                steps=(
                    AgentWorkflowStep(
                        sequence=1,
                        node=AgentWorkflowNode.CLASSIFY_INTENT,
                        outcome="policy_query",
                    ),
                    AgentWorkflowStep(
                        sequence=2,
                        node=AgentWorkflowNode.ANSWER_POLICY,
                        outcome="completed",
                    ),
                ),
                terminal_node=AgentWorkflowNode.ANSWER_POLICY,
            ),
            session=AgentSessionInfo(
                session_id="swagger-demo-001",
                turn_number=1,
                phase=AgentSessionPhase.IDLE,
                active_draft_id=None,
                draft_revision=None,
                pending_confirmation=False,
            ),
            memory=ConversationMemoryInfo(
                backend="sqlite",
                stored_message_count=2,
                context_applied=False,
                context_messages_used=0,
                context_window_limit=4,
                survives_process_restart=True,
            ),
        )
    )
    _use_fake_router(router)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/messages",
            json={
                "message": "  出差住宿标准是多少？  ",
                "session_id": "swagger-demo-001",
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
        "workflow": {
            "name": "enterprise_policy_workflow",
            "version": "1.1",
            "steps": [
                {
                    "sequence": 1,
                    "node": "classify_intent",
                    "outcome": "policy_query",
                },
                {
                    "sequence": 2,
                    "node": "answer_policy",
                    "outcome": "completed",
                },
            ],
            "terminal_node": "answer_policy",
            "interrupted": False,
        },
        "session": {
            "session_id": "swagger-demo-001",
            "turn_number": 1,
            "phase": "idle",
            "pending_confirmation": False,
            "checkpoint_backend": "in_memory",
            "survives_process_restart": False,
        },
        "memory": {
            "backend": "sqlite",
            "stored_message_count": 2,
            "context_applied": False,
            "context_messages_used": 0,
            "context_window_limit": 4,
            "survives_process_restart": True,
        },
    }
    assert router.calls == ["出差住宿标准是多少？"]
    assert router.session_ids == ["swagger-demo-001"]


def test_returns_draft_clarification_without_citations() -> None:
    clarification = "请补充采购事项、数量和预计单价。"
    router = FakeAgentRouter(
        AgentRouteResult(
            request="帮我生成采购申请草稿。",
            classification=IntentClassification(
                intent=IntentType.DRAFT_GENERATION,
                confidence=0.96,
                reason="请求生成采购申请草稿",
            ),
            status=AgentResponseStatus.NEEDS_CLARIFICATION,
            reply=clarification,
            application_draft=DraftGenerationResult(
                application_type=ApplicationType.PURCHASE,
                draft=None,
                clarification_question=clarification,
                citations=(),
            ),
        )
    )
    _use_fake_router(router)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/messages",
            json={"message": "帮我生成采购申请草稿。"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "needs_clarification"
    assert payload["citations"] == []
    assert payload["application_draft"] == {
        "application_type": "purchase",
        "clarification_question": clarification,
    }


def test_serializes_structured_application_draft() -> None:
    citation = PolicyCitation(
        source_id="S1",
        chunk_id="purchase-draft-001",
        document_title="采购管理办法",
        chapter_title="第三章 采购申请",
        article_label="第九条",
        article_title="采购申请必填信息",
        score=1.0,
    )
    material_check = MaterialCheckResult(
        application_type=ApplicationType.PURCHASE,
        mode=MaterialCheckMode.COMPARISON,
        required_materials=(
            MaterialRequirement(
                material_type="product_specification",
                display_name="产品规格说明",
                reason="货物采购需要产品规格说明。",
            ),
        ),
        provided_materials=(
            ProvidedMaterial(
                material_type="product_specification",
                display_name="产品规格说明",
                provided_count=1,
            ),
        ),
        missing_materials=(),
        materials_complete=True,
        clarification_question=None,
        notes=(),
        citations=(citation,),
    )
    approval_check = ApprovalCheckResult(
        application_type=ApprovalApplicationType.PURCHASE,
        approval_level=ApprovalLevel.GENERAL_PURCHASE,
        amount=Decimal(6000),
        leave_days=None,
        steps=(
            ApprovalStep(
                sequence=1,
                approver=ApproverCode.DIRECT_MANAGER,
                display_name="直属经理",
                action=ApprovalAction.APPROVE,
                reason="制度要求。",
            ),
        ),
        special_conditions=(),
        clarification_question=None,
        notes=(),
        citations=(citation,),
    )
    draft = ApplicationDraft(
        draft_id="PURCHASE-DRAFT-ABC123",
        application_type=ApplicationType.PURCHASE,
        title="采购申请草稿",
        status=DraftStatus.WAITING_FOR_CONFIRMATION,
        applicant=DraftUserContext(
            employee_id="DEMO-EMP-001",
            employee_name="演示用户",
            department="演示部门",
            roles=("EMPLOYEE",),
            region="中国大陆",
            identity_source="trusted_demo_context",
        ),
        fields=(
            DraftField(
                field_name="item_name",
                display_name="采购事项",
                value="办公显示器",
                source=DraftFieldSource.USER_INPUT,
            ),
            DraftField(
                field_name="estimated_total_amount",
                display_name="预计总金额（元）",
                value=Decimal(6000),
                source=DraftFieldSource.CALCULATED,
            ),
        ),
        missing_fields=(),
        material_check=material_check,
        approval_check=approval_check,
        policy_snapshots=(
            DraftPolicySnapshot(
                document_id="PROCUREMENT_POLICY_001",
                document_title="采购管理办法",
                version="1.0",
                effective_date=date(2026, 1, 1),
            ),
        ),
        validation_issues=(),
        summary_lines=("采购事项：办公显示器", "预计总金额：6,000元"),
        warnings=("草稿尚未确认，也没有提交审批。",),
        ready_for_confirmation=True,
        confirmation_required=True,
        user_confirmed=False,
        submitted=False,
        audit_metadata=DraftAuditMetadata(
            session_id="STATELESS-DEMO",
            request_id="REQUEST-ABC123",
            idempotency_key="draft:purchase:ABC123",
            created_at=datetime(2026, 8, 7, 10, 30, tzinfo=UTC),
            created_by="DEMO-EMP-001",
            identity_source="trusted_demo_context",
            persisted=False,
        ),
    )
    route_result = AgentRouteResult(
        request="帮我生成采购申请草稿。",
        classification=IntentClassification(
            intent=IntentType.DRAFT_GENERATION,
            confidence=0.99,
            reason="生成采购申请草稿",
        ),
        status=AgentResponseStatus.COMPLETED,
        reply="已生成采购申请草稿。[S1]",
        citations=(citation,),
        application_draft=DraftGenerationResult(
            application_type=ApplicationType.PURCHASE,
            draft=draft,
            clarification_question=None,
            citations=(citation,),
        ),
    )
    _use_fake_router(FakeAgentRouter(route_result))

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/messages",
            json={"message": "帮我生成采购申请草稿。"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["citations"] == ["S1"]
    draft_payload = payload["application_draft"]["draft"]
    assert draft_payload["draft_id"] == "PURCHASE-DRAFT-ABC123"
    assert draft_payload["status"] == "waiting_for_confirmation"
    assert draft_payload["applicant"]["employee_id"] == "DEMO-EMP-001"
    assert draft_payload["fields"][1]["value"] == "6000"
    assert draft_payload["material_check"]["materials_complete"] is True
    assert draft_payload["approval_check"]["amount"] == "6000"
    assert draft_payload["policy_snapshots"][0]["version"] == "1.0"
    assert draft_payload["ready_for_confirmation"] is True
    assert draft_payload["user_confirmed"] is False
    assert draft_payload["submitted"] is False
    assert draft_payload["audit_metadata"]["persisted"] is False
    assert draft_payload["revision"] == 1
    assert "confirmed_at" not in draft_payload
    assert "cancelled_at" not in draft_payload


def test_serializes_structured_approval_check_result() -> None:
    citation = PolicyCitation(
        source_id="S1",
        chunk_id="procurement-approval-001",
        document_title="采购管理办法",
        chapter_title="第四章 金额分级与审批",
        article_label="第十二条",
        article_title="一般采购审批",
        score=1.0,
    )
    approval_check = ApprovalCheckResult(
        application_type=ApprovalApplicationType.PURCHASE,
        approval_level=ApprovalLevel.GENERAL_PURCHASE,
        amount=Decimal(6000),
        leave_days=None,
        steps=(
            ApprovalStep(
                sequence=1,
                approver=ApproverCode.DIRECT_MANAGER,
                display_name="直属经理",
                action=ApprovalAction.APPROVE,
                reason="采购申请首先由直属经理审批。",
            ),
            ApprovalStep(
                sequence=2,
                approver=ApproverCode.DEPARTMENT_HEAD,
                display_name="部门负责人",
                action=ApprovalAction.APPROVE,
                reason="预计采购总金额超过5,000元。",
            ),
        ),
        special_conditions=(),
        clarification_question=None,
        notes=("采购金额按含税总成本计算。",),
        citations=(citation,),
    )
    router = FakeAgentRouter(
        AgentRouteResult(
            request="预计总金额6000元的采购需要谁审批？",
            classification=IntentClassification(
                intent=IntentType.APPROVAL_QUERY,
                confidence=0.99,
                reason="查询采购审批路径",
            ),
            status=AgentResponseStatus.COMPLETED,
            reply="直属经理 → 部门负责人。[S1]",
            citations=(citation,),
            approval_check=approval_check,
        )
    )
    _use_fake_router(router)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/messages",
            json={
                "message": "预计总金额6000元的采购需要谁审批？"
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["citations"] == ["S1"]
    assert payload["approval_check"] == {
        "application_type": "purchase",
        "approval_level": "general_purchase",
        "amount": "6000",
        "steps": [
            {
                "sequence": 1,
                "approver": "DIRECT_MANAGER",
                "display_name": "直属经理",
                "action": "approve",
                "reason": "采购申请首先由直属经理审批。",
            },
            {
                "sequence": 2,
                "approver": "DEPARTMENT_HEAD",
                "display_name": "部门负责人",
                "action": "approve",
                "reason": "预计采购总金额超过5,000元。",
            },
        ],
        "special_conditions": [],
        "notes": ["采购金额按含税总成本计算。"],
    }


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


def test_serializes_mock_approval_submission_result() -> None:
    submitted_at = datetime(2026, 8, 8, 10, 30, tzinfo=UTC)
    submission_id = "MOCK-PUR-20260808-ABC123DEF456"
    idempotency_key = "submission:PURCHASE-DRAFT-001:r1:abc123"
    submission = MockApprovalSubmissionResult(
        success=True,
        duplicate_submission=False,
        submission_result=SubmittedApplication(
            submission_id=submission_id,
            draft_id="PURCHASE-DRAFT-001",
            application_type=ApplicationType.PURCHASE,
            status=SubmissionStatus.APPROVAL_IN_PROGRESS,
            submitted_at=submitted_at,
            submitted_by="DEMO-EMP-001",
            idempotency_key=idempotency_key,
        ),
        approval_workflow=SubmittedApprovalWorkflow(
            workflow_id=f"WF-{submission_id}",
            current_step=1,
            steps=(
                SubmittedApprovalStep(
                    sequence=1,
                    approver=ApproverCode.DIRECT_MANAGER,
                    display_name="直属经理",
                    status=ApprovalWorkflowStepStatus.PENDING,
                ),
                SubmittedApprovalStep(
                    sequence=2,
                    approver=ApproverCode.DEPARTMENT_HEAD,
                    display_name="部门负责人",
                    status=ApprovalWorkflowStepStatus.WAITING,
                ),
            ),
        ),
        audit_record=SubmissionAuditRecord(
            audit_id="AUDIT-001",
            event=SubmissionAuditEvent.SUBMITTED,
            session_id="swagger-submit-001",
            request_id="SUBMIT-REQUEST-001",
            draft_id="PURCHASE-DRAFT-001",
            draft_revision=1,
            submission_id=submission_id,
            submission_idempotency_key=idempotency_key,
            actor_employee_id="DEMO-EMP-001",
            recorded_at=submitted_at,
            confirmation_text_recorded=True,
            confirmation_text_sha256="a" * 64,
            duplicate_submission=False,
        ),
    )
    router = FakeAgentRouter(
        AgentRouteResult(
            request="提交审批",
            classification=IntentClassification(
                intent=IntentType.DRAFT_SUBMISSION,
                confidence=1.0,
                reason="用户明确提交已确认草稿。",
            ),
            status=AgentResponseStatus.SUBMITTED,
            reply=f"草稿已成功模拟提交审批。模拟审批单号：{submission_id}。",
            submission=submission,
            workflow=AgentWorkflowTrace(
                name="enterprise_policy_workflow",
                version="1.2",
                steps=(
                    AgentWorkflowStep(
                        sequence=1,
                        node=AgentWorkflowNode.RESOLVE_TURN,
                        outcome="submit_draft",
                    ),
                    AgentWorkflowStep(
                        sequence=2,
                        node=AgentWorkflowNode.SUBMIT_APPROVAL,
                        outcome="submitted",
                    ),
                ),
                terminal_node=AgentWorkflowNode.SUBMIT_APPROVAL,
            ),
            session=AgentSessionInfo(
                session_id="swagger-submit-001",
                turn_number=3,
                phase=AgentSessionPhase.SUBMITTED,
                active_draft_id="PURCHASE-DRAFT-001",
                draft_revision=1,
                pending_confirmation=False,
            ),
        )
    )
    _use_fake_router(router)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/messages",
            json={
                "message": "提交审批",
                "session_id": "swagger-submit-001",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "submitted"
    assert payload["classification"]["intent"] == "draft_submission"
    assert payload["submission"] == {
        "success": True,
        "duplicate_submission": False,
        "submission_result": {
            "submission_id": submission_id,
            "draft_id": "PURCHASE-DRAFT-001",
            "application_type": "purchase",
            "status": "approval_in_progress",
            "submitted_at": "2026-08-08T10:30:00Z",
            "submitted_by": "DEMO-EMP-001",
            "idempotency_key": idempotency_key,
        },
        "approval_workflow": {
            "workflow_id": f"WF-{submission_id}",
            "current_step": 1,
            "steps": [
                {
                    "sequence": 1,
                    "approver": "DIRECT_MANAGER",
                    "display_name": "直属经理",
                    "status": "pending",
                },
                {
                    "sequence": 2,
                    "approver": "DEPARTMENT_HEAD",
                    "display_name": "部门负责人",
                    "status": "waiting",
                },
            ],
        },
        "audit_record": {
            "audit_id": "AUDIT-001",
            "event": "submitted",
            "session_id": "swagger-submit-001",
            "request_id": "SUBMIT-REQUEST-001",
            "draft_id": "PURCHASE-DRAFT-001",
            "draft_revision": 1,
            "submission_id": submission_id,
            "submission_idempotency_key": idempotency_key,
            "actor_employee_id": "DEMO-EMP-001",
            "recorded_at": "2026-08-08T10:30:00Z",
            "confirmation_text_recorded": True,
            "confirmation_text_sha256": "a" * 64,
            "duplicate_submission": False,
            "sensitive_fields_recorded": False,
        },
        "storage_backend": "in_memory",
        "survives_process_restart": False,
    }
    assert payload["session"]["phase"] == "submitted"
    assert payload["workflow"]["terminal_node"] == "submit_approval"


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


@pytest.mark.parametrize(
    "session_id",
    ["contains space", "slash/not-allowed", "x" * 65],
)
def test_rejects_invalid_session_id(session_id: str) -> None:
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
            json={
                "message": "查询制度",
                "session_id": session_id,
            },
        )

    assert response.status_code == 422
    assert router.calls == []
