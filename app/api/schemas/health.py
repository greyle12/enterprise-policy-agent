from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class LivenessResponse(BaseModel):
    """Process-level health response with no external dependency checks."""

    status: Literal["ok"]
    service: str
    version: str


class ReadinessChecks(BaseModel):
    """Dependency checks required before the API can serve business traffic."""

    application: Literal["ok", "unavailable"]
    database: Literal["ok", "unavailable", "not_checked"]


class ReadinessResponse(BaseModel):
    """Readiness response suitable for Docker and orchestration probes."""

    status: Literal["ready", "not_ready"]
    checks: ReadinessChecks
