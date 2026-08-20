from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import (
    get_policy_answer_service,
)
from app.api.schemas.policy_answers import (
    PolicyAnswerRequest,
    PolicyAnswerResponse,
)
from app.rag.policy_answer_service import (
    PolicyAnswerService,
)

router = APIRouter(
    prefix="/policy-answers",
    tags=["policy-answers"],
)


@router.post(
    "",
    response_model=PolicyAnswerResponse,
)
async def answer_policy_question(
    request: PolicyAnswerRequest,
    service: Annotated[
        PolicyAnswerService,
        Depends(get_policy_answer_service),
    ],
) -> PolicyAnswerResponse:
    """根据企业制度回答问题。"""

    result = await service.answer(request.question)

    return PolicyAnswerResponse(
        question=result.question,
        answer=result.answer,
        citations=[citation.source_id for citation in result.citations],
    )
