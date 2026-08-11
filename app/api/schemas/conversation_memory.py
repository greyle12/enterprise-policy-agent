from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.memory.conversation import ConversationRole


class AgentMemoryResponse(BaseModel):
    """How memory was stored and used for the current Agent response."""

    backend: str
    stored_message_count: int
    context_applied: bool
    context_messages_used: int
    context_window_limit: int
    survives_process_restart: bool


class ConversationMessageResponse(BaseModel):
    """One sanitized message returned by the session history endpoint."""

    turn_number: int
    role: ConversationRole
    content: str
    created_at: datetime
    redacted: bool
    truncated: bool


class ConversationHistoryResponse(BaseModel):
    """Recent conversation memory plus retention metadata."""

    session_id: str
    messages: list[ConversationMessageResponse]
    total_message_count: int
    returned_message_count: int
    backend: str
    survives_process_restart: bool


class AgentSessionResetResponse(BaseModel):
    """Confirmation that all mutable state for a session was cleared."""

    session_id: str
    cleared: bool
