from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from app.llm import ProviderCapacityError


async def provider_capacity_error_response(
    request: Request,
    error: ProviderCapacityError,
) -> JSONResponse:
    """Map local overload to a stable response without exposing request data."""

    del request
    return JSONResponse(
        status_code=error.status_code,
        content={
            "detail": {
                "code": error.code,
                "message": error.user_message,
            }
        },
    )
