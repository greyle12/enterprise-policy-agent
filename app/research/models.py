from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.rag.policy_answer_service import PolicyAnswer
from app.resilience import AgentResilienceInfo


class WebSearchProviderName(StrEnum):
    """应用支持的外部搜索实现。"""

    DISABLED = "disabled"
    TAVILY = "tavily"


class ResearchStatus(StrEnum):
    """一次制度研究请求的完整性状态。"""

    COMPLETED = "completed"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class WebSearchStatus(StrEnum):
    """外部搜索分支是否被请求、执行及成功。"""

    NOT_REQUESTED = "not_requested"
    DISABLED = "disabled"
    COMPLETED = "completed"
    NO_RESULTS = "no_results"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ExternalQuery:
    """实际允许发送给外部搜索的受限查询。"""

    text: str
    redacted: bool
    truncated: bool


@dataclass(frozen=True, slots=True)
class WebSearchResult:
    """搜索 Provider 返回的一条已规范化公开资料。"""

    title: str
    url: str
    snippet: str
    score: float
    published_date: str | None = None


@dataclass(frozen=True, slots=True)
class ExternalResearchSource:
    """研究回答中带独立 W 编号的外部资料。"""

    source_id: str
    title: str
    url: str
    snippet: str
    score: float
    published_date: str | None = None


@dataclass(frozen=True, slots=True)
class WebSearchInfo:
    """公开返回的外部搜索执行边界，不包含实际查询正文。"""

    requested: bool
    executed: bool
    provider: WebSearchProviderName
    status: WebSearchStatus
    query_redacted: bool
    query_truncated: bool
    result_count: int


@dataclass(frozen=True, slots=True)
class PolicyResearchAnswer:
    """内部制度与外部公开资料严格分区的研究结果。"""

    question: str
    assistant_name: str
    assistant_version: str
    status: ResearchStatus
    answer: str
    policy_answer: PolicyAnswer | None
    external_sources: tuple[ExternalResearchSource, ...]
    web_search: WebSearchInfo
    resilience: AgentResilienceInfo
