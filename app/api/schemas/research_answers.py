from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.api.schemas.resilience import AgentResilienceResponse
from app.research.models import (
    ResearchStatus,
    WebSearchProviderName,
    WebSearchStatus,
)

_ResearchQuestion = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=1000,
    ),
]


class ResearchAnswerRequest(BaseModel):
    """制度研究助手的一次无状态请求。"""

    question: _ResearchQuestion
    include_web: bool = False


class InternalPolicySourceResponse(BaseModel):
    """内部 RAG 实际使用的一条制度依据。"""

    source_id: str
    chunk_id: str
    document_title: str
    chapter_title: str
    article_label: str
    article_title: str
    score: float = Field(ge=-1.0, le=1.0)


class ExternalWebSourceResponse(BaseModel):
    """只供参考、不能驱动业务流程的公开网页来源。"""

    source_id: str
    title: str
    url: str
    snippet: str
    score: float = Field(ge=0.0, le=1.0)
    published_date: str | None = None


class ResearchSourcePolicyResponse(BaseModel):
    """客户端可以直接检查的来源优先级和用途边界。"""

    internal_policy_authoritative: bool = True
    external_web_advisory: bool = True
    external_web_used_for_workflow: bool = False


class WebSearchInfoResponse(BaseModel):
    """外部搜索是否实际执行及查询是否被安全处理。"""

    requested: bool
    executed: bool
    provider: WebSearchProviderName
    status: WebSearchStatus
    query_redacted: bool
    query_truncated: bool
    result_count: int = Field(ge=0, le=5)


class ResearchAssistantMetadataResponse(BaseModel):
    """研究助手的稳定名称和契约版本。"""

    name: str
    version: str


class ResearchAnswerResponse(BaseModel):
    """内部制度与外部资料分栏返回的研究结果。"""

    question: str
    assistant: ResearchAssistantMetadataResponse
    status: ResearchStatus
    answer: str
    internal_answer: str | None
    internal_sources: list[InternalPolicySourceResponse]
    external_sources: list[ExternalWebSourceResponse]
    source_policy: ResearchSourcePolicyResponse
    web_search: WebSearchInfoResponse
    resilience: AgentResilienceResponse
