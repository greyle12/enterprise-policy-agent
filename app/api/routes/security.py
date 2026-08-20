from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.api.schemas.security import (
    PromptSecurityMetricsResponse,
    PromptSecurityStatusResponse,
)
from app.security import PromptInjectionGuard

router = APIRouter(
    prefix="/security",
    tags=["security"],
)


def _guard(request: Request) -> PromptInjectionGuard:
    guard = getattr(request.app.state, "prompt_security_guard", None)
    if not isinstance(guard, PromptInjectionGuard):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Prompt security is not initialized",
        )
    return guard


@router.get(
    "/status",
    response_model=PromptSecurityStatusResponse,
    summary="Inspect content-free process-local prompt security counters",
)
async def prompt_security_status(request: Request) -> PromptSecurityStatusResponse:
    snapshot = _guard(request).snapshot()
    return PromptSecurityStatusResponse(
        rule_set_version=snapshot.rule_set_version,
        metrics=PromptSecurityMetricsResponse(
            user_inputs_checked=snapshot.user_inputs_checked,
            user_inputs_blocked=snapshot.user_inputs_blocked,
            evidence_chunks_checked=snapshot.evidence_chunks_checked,
            evidence_chunks_quarantined=snapshot.evidence_chunks_quarantined,
            llm_calls_avoided=snapshot.llm_calls_avoided,
        ),
    )
