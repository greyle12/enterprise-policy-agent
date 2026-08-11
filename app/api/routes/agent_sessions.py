from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.agent.router import AgentRouter
from app.api.dependencies import get_agent_router
from app.api.schemas.conversation_memory import (
    AgentSessionResetResponse,
    ConversationHistoryResponse,
    ConversationMessageResponse,
)

router = APIRouter(
    prefix="/agent/sessions",
    tags=["agent-sessions"],
)

SessionIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$",
    ),
]


@router.get(
    "/{session_id}/messages",
    response_model=ConversationHistoryResponse,
)
async def get_agent_session_messages(
    session_id: SessionIdPath,
    agent_router: Annotated[
        AgentRouter,
        Depends(get_agent_router),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ConversationHistoryResponse:
    """Return recent, sanitized conversation messages for one session."""

    snapshot = await agent_router.get_conversation_history(
        session_id,
        limit=limit,
    )
    messages = [
        ConversationMessageResponse(
            turn_number=message.turn_number,
            role=message.role,
            content=message.content,
            created_at=message.created_at,
            redacted=message.redacted,
            truncated=message.truncated,
        )
        for message in snapshot.messages
    ]
    return ConversationHistoryResponse(
        session_id=snapshot.session_id,
        messages=messages,
        total_message_count=snapshot.total_message_count,
        returned_message_count=len(messages),
        backend=snapshot.backend,
        survives_process_restart=snapshot.survives_process_restart,
    )


@router.delete(
    "/{session_id}",
    response_model=AgentSessionResetResponse,
)
async def clear_agent_session(
    session_id: SessionIdPath,
    agent_router: Annotated[
        AgentRouter,
        Depends(get_agent_router),
    ],
) -> AgentSessionResetResponse:
    """Clear workflow checkpoints, draft projections, and conversation memory."""

    await agent_router.clear_session(session_id)
    return AgentSessionResetResponse(
        session_id=session_id,
        cleared=True,
    )
