from __future__ import annotations

from enum import StrEnum


class AgentStateProviderName(StrEnum):
    """Supported durable Agent runtime state backends."""

    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"


__all__ = ["AgentStateProviderName"]
