from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_policy_research_assistant
from app.api.schemas.research_answers import (
    ExternalWebSourceResponse,
    InternalPolicySourceResponse,
    ResearchAnswerRequest,
    ResearchAnswerResponse,
    ResearchAssistantMetadataResponse,
    ResearchSourcePolicyResponse,
    WebSearchInfoResponse,
)
from app.api.schemas.resilience import build_resilience_response
from app.research import PolicyResearchAssistant

router = APIRouter(
    prefix="/research/answers",
    tags=["research"],
)


@router.post(
    "",
    response_model=ResearchAnswerResponse,
)
async def answer_research_question(
    request: ResearchAnswerRequest,
    assistant: Annotated[
        PolicyResearchAssistant,
        Depends(get_policy_research_assistant),
    ],
) -> ResearchAnswerResponse:
    """先研究内部制度，并按显式授权选择性补充公开网页。"""

    result = await assistant.answer(
        request.question,
        include_web=request.include_web,
    )
    policy_answer = result.policy_answer
    resilience = build_resilience_response(result.resilience)
    if resilience is None:
        raise RuntimeError("research result is missing resilience metadata")
    return ResearchAnswerResponse(
        question=result.question,
        assistant=ResearchAssistantMetadataResponse(
            name=result.assistant_name,
            version=result.assistant_version,
        ),
        status=result.status,
        answer=result.answer,
        internal_answer=(policy_answer.answer if policy_answer is not None else None),
        internal_sources=(
            [
                InternalPolicySourceResponse(
                    source_id=citation.source_id,
                    chunk_id=citation.chunk_id,
                    document_title=citation.document_title,
                    chapter_title=citation.chapter_title,
                    article_label=citation.article_label,
                    article_title=citation.article_title,
                    score=citation.score,
                )
                for citation in policy_answer.citations
            ]
            if policy_answer is not None
            else []
        ),
        external_sources=[
            ExternalWebSourceResponse(
                source_id=source.source_id,
                title=source.title,
                url=source.url,
                snippet=source.snippet,
                score=source.score,
                published_date=source.published_date,
            )
            for source in result.external_sources
        ],
        source_policy=ResearchSourcePolicyResponse(),
        web_search=WebSearchInfoResponse(
            requested=result.web_search.requested,
            executed=result.web_search.executed,
            provider=result.web_search.provider,
            status=result.web_search.status,
            query_redacted=result.web_search.query_redacted,
            query_truncated=result.web_search.query_truncated,
            result_count=result.web_search.result_count,
        ),
        resilience=resilience,
    )
