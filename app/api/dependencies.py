from __future__ import annotations

from typing import cast

from fastapi import Request

from app.rag.policy_answer_service import (
    PolicyAnswerService,
)


def get_policy_answer_service(
    request: Request,
) -> PolicyAnswerService:
    """从当前 FastAPI 应用取得制度问答服务。"""

    service = getattr(
        request.app.state,
        "policy_answer_service",
        None,
    )

    if service is None:
        raise RuntimeError(
            "PolicyAnswerService is not configured"
        )

    return cast(PolicyAnswerService, service)