from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.agent.router import AgentRouter
from app.api.dependencies import get_agent_router
from app.api.schemas.agent_messages import (
    AgentMessageRequest,
    AgentMessageResponse,
    IntentClassificationResponse,
)

router = APIRouter(
    prefix="/agent/messages",
    tags=["agent"],
)


@router.post(
    "",
    response_model=AgentMessageResponse,
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
    )
