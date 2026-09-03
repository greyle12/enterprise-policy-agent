from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.agent.workflow_models import AgentSessionPhase


@dataclass(frozen=True, slots=True)
class StoredAgentSession:
    """Minimal backend-neutral query model for one persisted Agent session."""

    session_id: str
    turn_number: int
    phase: AgentSessionPhase
    active_draft_id: str | None
    draft_revision: int | None
    pending_confirmation: bool
    checkpoint_backend: str
    updated_at: datetime


__all__ = ["StoredAgentSession"]
