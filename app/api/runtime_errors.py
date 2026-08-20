from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from app.observability import REQUEST_ID_HEADER

logger = logging.getLogger(__name__)


async def unhandled_application_error_response(
    request: Request,
    error: Exception,
) -> JSONResponse:
    """Return one correlated, stable 500 response without exception details."""

    request_id = getattr(request.state, "request_id", "request-id-unavailable")
    logger.error(
        "unhandled_application_error",
        extra={
            "request_id": request_id,
            "error_type": type(error).__name__,
        },
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": "internal_server_error",
                "message": "The request could not be completed.",
            },
            "request_id": request_id,
        },
        headers={REQUEST_ID_HEADER: request_id},
    )
