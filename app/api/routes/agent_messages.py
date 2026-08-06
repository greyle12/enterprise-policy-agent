from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.agent.router import AgentRouter
from app.api.dependencies import get_agent_router
from app.api.schemas.agent_messages import (
    AgentMessageRequest,
    AgentMessageResponse,
    IntentClassificationResponse,
    MaterialCheckResponse,
    MaterialRequirementResponse,
    MissingMaterialResponse,
    ProvidedMaterialResponse,
)

router = APIRouter(
    prefix="/agent/messages",
    tags=["agent"],
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

    result = await agent_router.route(request.message)

    material_check = None
    if result.material_check is not None:
        material_check = MaterialCheckResponse(
            application_type=(
                result.material_check.application_type
            ),
            mode=result.material_check.mode,
            required_materials=[
                MaterialRequirementResponse(
                    material_type=item.material_type,
                    display_name=item.display_name,
                    reason=item.reason,
                    required_count=item.required_count,
                    sensitive=item.sensitive,
                )
                for item in (
                    result.material_check.required_materials
                )
            ],
            provided_materials=[
                ProvidedMaterialResponse(
                    material_type=item.material_type,
                    display_name=item.display_name,
                    provided_count=item.provided_count,
                )
                for item in (
                    result.material_check.provided_materials
                )
            ],
            missing_materials=[
                MissingMaterialResponse(
                    material_type=item.material_type,
                    display_name=item.display_name,
                    missing_count=item.missing_count,
                    reason=item.reason,
                    sensitive=item.sensitive,
                )
                for item in (
                    result.material_check.missing_materials
                )
            ],
            materials_complete=(
                result.material_check.materials_complete
            ),
            clarification_question=(
                result.material_check.clarification_question
            ),
            notes=list(result.material_check.notes),
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
    )
