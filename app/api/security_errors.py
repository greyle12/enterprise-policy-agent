from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from app.observability import REQUEST_ID_HEADER
from app.security import PromptInjectionBlockedError

logger = logging.getLogger(__name__)


async def prompt_injection_blocked_response(
    request: Request,
    error: PromptInjectionBlockedError,
) -> JSONResponse:
    """Return a stable refusal without echoing content or matched rule details."""

    del error
    request_id = getattr(request.state, "request_id", "request-id-unavailable")
    logger.warning(
        "prompt_injection_blocked",
        extra={"request_id": request_id},
    )
    return JSONResponse(
        status_code=400,
        content={
            "detail": {
                "code": "prompt_injection_blocked",
                "message": "The request was rejected by the input security policy.",
            },
            "request_id": request_id,
        },
        headers={REQUEST_ID_HEADER: request_id},
    )
