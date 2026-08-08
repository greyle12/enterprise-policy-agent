from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.agent.router import (
    AgentRouter,
    AgentSessionInfo,
    AgentWorkflowTrace,
)
from app.api.dependencies import get_agent_router
from app.api.schemas.agent_messages import (
    AgentMessageRequest,
    AgentMessageResponse,
    AgentSessionResponse,
    AgentWorkflowStepResponse,
    AgentWorkflowTraceResponse,
    ApplicationDraftResponse,
    ApprovalCheckResponse,
    ApprovalStepResponse,
    DraftAuditMetadataResponse,
    DraftFieldResponse,
    DraftGenerationResponse,
    DraftPolicySnapshotResponse,
    DraftUserContextResponse,
    DraftValidationIssueResponse,
    IntentClassificationResponse,
    MaterialCheckResponse,
    MaterialRequirementResponse,
    MissingDraftFieldResponse,
    MissingMaterialResponse,
    ProvidedMaterialResponse,
)
from app.tools.approval_models import ApprovalCheckResult
from app.tools.draft_models import DraftGenerationResult
from app.tools.material_models import MaterialCheckResult

router = APIRouter(
    prefix="/agent/messages",
    tags=["agent"],
)


def _material_response(
    result: MaterialCheckResult,
) -> MaterialCheckResponse:
    return MaterialCheckResponse(
        application_type=result.application_type,
        mode=result.mode,
        required_materials=[
            MaterialRequirementResponse(
                material_type=item.material_type,
                display_name=item.display_name,
                reason=item.reason,
                required_count=item.required_count,
                sensitive=item.sensitive,
            )
            for item in result.required_materials
        ],
        provided_materials=[
            ProvidedMaterialResponse(
                material_type=item.material_type,
                display_name=item.display_name,
                provided_count=item.provided_count,
            )
            for item in result.provided_materials
        ],
        missing_materials=[
            MissingMaterialResponse(
                material_type=item.material_type,
                display_name=item.display_name,
                missing_count=item.missing_count,
                reason=item.reason,
                sensitive=item.sensitive,
            )
            for item in result.missing_materials
        ],
        materials_complete=result.materials_complete,
        clarification_question=result.clarification_question,
        notes=list(result.notes),
    )


def _approval_response(
    result: ApprovalCheckResult,
) -> ApprovalCheckResponse:
    return ApprovalCheckResponse(
        application_type=result.application_type,
        approval_level=result.approval_level,
        amount=result.amount,
        leave_days=result.leave_days,
        steps=[
            ApprovalStepResponse(
                sequence=item.sequence,
                approver=item.approver,
                display_name=item.display_name,
                action=item.action,
                reason=item.reason,
            )
            for item in result.steps
        ],
        special_conditions=list(result.special_conditions),
        clarification_question=result.clarification_question,
        notes=list(result.notes),
    )


def _draft_response(
    result: DraftGenerationResult,
) -> DraftGenerationResponse:
    draft_response = None
    if result.draft is not None:
        draft = result.draft
        draft_response = ApplicationDraftResponse(
            draft_id=draft.draft_id,
            application_type=draft.application_type,
            title=draft.title,
            status=draft.status,
            applicant=DraftUserContextResponse(
                employee_id=draft.applicant.employee_id,
                employee_name=draft.applicant.employee_name,
                department=draft.applicant.department,
                roles=list(draft.applicant.roles),
                region=draft.applicant.region,
                identity_source=draft.applicant.identity_source,
            ),
            fields=[
                DraftFieldResponse(
                    field_name=item.field_name,
                    display_name=item.display_name,
                    value=item.value,
                    source=item.source,
                    sensitive=item.sensitive,
                )
                for item in draft.fields
            ],
            missing_fields=[
                MissingDraftFieldResponse(
                    field_name=item.field_name,
                    display_name=item.display_name,
                    question=item.question,
                )
                for item in draft.missing_fields
            ],
            material_check=_material_response(
                draft.material_check
            ),
            approval_check=_approval_response(
                draft.approval_check
            ),
            policy_snapshots=[
                DraftPolicySnapshotResponse(
                    document_id=item.document_id,
                    document_title=item.document_title,
                    version=item.version,
                    effective_date=item.effective_date,
                )
                for item in draft.policy_snapshots
            ],
            validation_issues=[
                DraftValidationIssueResponse(
                    code=item.code,
                    severity=item.severity,
                    message=item.message,
                    blocking=item.blocking,
                )
                for item in draft.validation_issues
            ],
            summary_lines=list(draft.summary_lines),
            warnings=list(draft.warnings),
            ready_for_confirmation=(
                draft.ready_for_confirmation
            ),
            confirmation_required=draft.confirmation_required,
            user_confirmed=draft.user_confirmed,
            submitted=draft.submitted,
            audit_metadata=DraftAuditMetadataResponse(
                session_id=draft.audit_metadata.session_id,
                request_id=draft.audit_metadata.request_id,
                idempotency_key=(
                    draft.audit_metadata.idempotency_key
                ),
                created_at=draft.audit_metadata.created_at,
                created_by=draft.audit_metadata.created_by,
                identity_source=(
                    draft.audit_metadata.identity_source
                ),
                persisted=draft.audit_metadata.persisted,
            ),
            revision=draft.revision,
            confirmed_at=draft.confirmed_at,
            cancelled_at=draft.cancelled_at,
        )

    return DraftGenerationResponse(
        application_type=result.application_type,
        draft=draft_response,
        clarification_question=result.clarification_question,
    )


def _workflow_response(
    trace: AgentWorkflowTrace,
) -> AgentWorkflowTraceResponse:
    return AgentWorkflowTraceResponse(
        name=trace.name,
        version=trace.version,
        steps=[
            AgentWorkflowStepResponse(
                sequence=step.sequence,
                node=step.node,
                outcome=step.outcome,
            )
            for step in trace.steps
        ],
        terminal_node=trace.terminal_node,
        interrupted=trace.interrupted,
    )


def _session_response(
    session: AgentSessionInfo,
) -> AgentSessionResponse:
    return AgentSessionResponse(
        session_id=session.session_id,
        turn_number=session.turn_number,
        phase=session.phase,
        active_draft_id=session.active_draft_id,
        draft_revision=session.draft_revision,
        pending_confirmation=session.pending_confirmation,
        checkpoint_backend=session.checkpoint_backend,
        survives_process_restart=(
            session.survives_process_restart
        ),
    )


@router.post(
    "",
    response_model=AgentMessageResponse,
    response_model_exclude_none=True,
)
async def handle_agent_message(
    request: AgentMessageRequest,
    agent_router: Annotated[
        AgentRouter,
        Depends(get_agent_router),
    ],
) -> AgentMessageResponse:
    """识别用户意图并路由到对应 Agent 能力。"""

    result = await agent_router.route(
        request.message,
        session_id=request.session_id,
    )

    material_check = (
        _material_response(result.material_check)
        if result.material_check is not None
        else None
    )
    approval_check = (
        _approval_response(result.approval_check)
        if result.approval_check is not None
        else None
    )
    application_draft = (
        _draft_response(result.application_draft)
        if result.application_draft is not None
        else None
    )

    return AgentMessageResponse(
        request=result.request,
        classification=IntentClassificationResponse(
            intent=result.classification.intent,
            confidence=result.classification.confidence,
            reason=result.classification.reason,
        ),
        status=result.status,
        reply=result.reply,
        citations=[
            citation.source_id
            for citation in result.citations
        ],
        material_check=material_check,
        approval_check=approval_check,
        application_draft=application_draft,
        workflow=(
            _workflow_response(result.workflow)
            if result.workflow is not None
            else None
        ),
        session=(
            _session_response(result.session)
            if result.session is not None
            else None
        ),
    )
