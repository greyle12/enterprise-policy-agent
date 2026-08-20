from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from app.api.schemas.health import (
    LivenessResponse,
    ReadinessChecks,
    ReadinessResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/health",
    tags=["health"],
)


class _DatabaseReadinessProbe(Protocol):
    async def ping(self) -> None:
        """Raise when the configured persistence backend is unavailable."""


class _VectorReadinessProbe(Protocol):
    def ping(self) -> None:
        """Raise when the configured vector store is unavailable."""


@router.get(
    "/live",
    response_model=LivenessResponse,
    summary="Check whether the API process is alive",
)
async def liveness(request: Request) -> LivenessResponse:
    """Return process health without touching the model or database."""

    return LivenessResponse(
        status="ok",
        service=request.app.title,
        version=request.app.version,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadinessResponse,
            "description": "Application dependencies are not ready.",
        }
    },
    summary="Check whether the API can serve business traffic",
)
async def readiness(
    request: Request,
) -> ReadinessResponse | JSONResponse:
    """Check initialized application components and the SQLite backend."""

    application = request.app
    required_components = (
        "policy_answer_service",
        "agent_router",
        "agent_state_store",
        "policy_vector_index",
    )
    components_ready = all(hasattr(application.state, name) for name in required_components)

    if not components_ready:
        response = ReadinessResponse(
            status="not_ready",
            checks=ReadinessChecks(
                application="unavailable",
                database="not_checked",
            ),
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=response.model_dump(mode="json"),
        )

    database_probe: _DatabaseReadinessProbe = application.state.agent_state_store
    vector_probe: _VectorReadinessProbe = application.state.policy_vector_index
    try:
        await database_probe.ping()
        await asyncio.to_thread(vector_probe.ping)
    except Exception:
        logger.warning(
            "Runtime persistence readiness probe failed",
            exc_info=True,
        )
        response = ReadinessResponse(
            status="not_ready",
            checks=ReadinessChecks(
                application="ok",
                database="unavailable",
            ),
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=response.model_dump(mode="json"),
        )

    return ReadinessResponse(
        status="ready",
        checks=ReadinessChecks(
            application="ok",
            database="ok",
        ),
    )
