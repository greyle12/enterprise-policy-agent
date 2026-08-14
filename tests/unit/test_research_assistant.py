from __future__ import annotations

import pytest

from app.rag.policy_answer_service import PolicyAnswer
from app.rag.policy_context import PolicyCitation
from app.research import (
    DisabledWebSearchProvider,
    PolicyResearchAssistant,
    ResearchStatus,
    WebSearchProviderName,
    WebSearchResult,
    WebSearchStatus,
    sanitize_external_query,
)
from app.resilience import (
    ResilientToolExecutor,
    ToolCallOutcome,
    ToolName,
)


def _policy_answer(*, with_citation: bool = True) -> PolicyAnswer:
    citation = PolicyCitation(
        source_id="S1",
        chunk_id="travel-001",
        document_title="差旅报销管理制度",
        chapter_title="住宿费",
        article_label="第八条",
        article_title="报销要求",
        score=0.94,
    )
    return PolicyAnswer(
        question="差旅住宿费如何报销？",
        answer=(
            "住宿费应在制度标准内凭发票报销。[S1]"
            if with_citation
            else "未检索到可用于回答该问题的制度依据。"
        ),
        citations=((citation,) if with_citation else ()),
    )


class FakePolicyResearcher:
    def __init__(self, outcomes: list[PolicyAnswer | Exception]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    async def answer(self, question: str) -> PolicyAnswer:
        self.calls.append(question)
        outcome = self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeWebSearchProvider:
    provider_name = WebSearchProviderName.TAVILY
    available = True

    def __init__(self, outcomes: list[tuple[WebSearchResult, ...] | Exception]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []
        self.closed = False

    async def search(self, query: str) -> tuple[WebSearchResult, ...]:
        self.calls.append(query)
        outcome = self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def aclose(self) -> None:
        self.closed = True


def _web_result() -> WebSearchResult:
    return WebSearchResult(
        title="国家税务公开指南",
        url="https://example.gov.cn/travel",
        snippet="公开资料说明了差旅凭证的一般要求。",
        score=0.88,
        published_date="2026-07-01",
    )


def _executor(*, max_attempts: int = 3) -> ResilientToolExecutor:
    return ResilientToolExecutor(
        safe_tool_timeout_seconds=0.2,
        mutation_tool_timeout_seconds=0.2,
        max_attempts=max_attempts,
        retry_min_wait_seconds=0.0,
        retry_max_wait_seconds=0.0,
        error_id_factory=lambda: "ERR-RESEARCH0001",
    )


@pytest.mark.asyncio
async def test_local_only_research_never_calls_web() -> None:
    policy = FakePolicyResearcher([_policy_answer()])
    web = FakeWebSearchProvider([(_web_result(),)])
    assistant = PolicyResearchAssistant(
        policy_researcher=policy,
        web_search_provider=web,
        tool_executor=_executor(),
    )

    result = await assistant.answer("差旅住宿费如何报销？")

    assert result.assistant_name == "policy_research_assistant"
    assert result.assistant_version == "1.0"
    assert result.status is ResearchStatus.COMPLETED
    assert result.policy_answer == _policy_answer()
    assert result.external_sources == ()
    assert result.web_search.status is WebSearchStatus.NOT_REQUESTED
    assert result.web_search.executed is False
    assert web.calls == []
    assert [record.tool for record in result.resilience.tool_calls] == [ToolName.POLICY_RESEARCH]


@pytest.mark.asyncio
async def test_hybrid_research_separates_internal_and_external_sources() -> None:
    web = FakeWebSearchProvider([(_web_result(),)])
    assistant = PolicyResearchAssistant(
        policy_researcher=FakePolicyResearcher([_policy_answer()]),
        web_search_provider=web,
        tool_executor=_executor(),
    )

    result = await assistant.answer(
        "差旅住宿费如何报销？",
        include_web=True,
    )

    assert result.status is ResearchStatus.COMPLETED
    assert result.policy_answer is not None
    assert result.policy_answer.citations[0].source_id == "S1"
    assert result.external_sources[0].source_id == "W1"
    assert result.web_search.status is WebSearchStatus.COMPLETED
    assert result.web_search.result_count == 1
    assert "[S1]" in result.answer
    assert "[W1]" in result.answer
    assert "不能替代企业内部有效制度" in result.answer
    assert [record.tool for record in result.resilience.tool_calls] == [
        ToolName.POLICY_RESEARCH,
        ToolName.WEB_SEARCH,
    ]


@pytest.mark.asyncio
async def test_web_search_requires_both_request_opt_in_and_server_provider() -> None:
    assistant = PolicyResearchAssistant(
        policy_researcher=FakePolicyResearcher([_policy_answer()]),
        web_search_provider=DisabledWebSearchProvider(),
        tool_executor=_executor(),
    )

    result = await assistant.answer("对比公开要求", include_web=True)

    assert result.status is ResearchStatus.PARTIAL
    assert result.web_search.requested is True
    assert result.web_search.executed is False
    assert result.web_search.status is WebSearchStatus.DISABLED
    assert "没有向外部服务发送查询" in result.answer
    assert result.resilience.degraded is False


def test_external_query_is_redacted_and_truncated() -> None:
    query = sanitize_external_query("比较规则 password=hunter2 " + ("甲" * 600))

    assert "hunter2" not in query.text
    assert "[REDACTED]" in query.text
    assert len(query.text) == 500
    assert query.redacted is True
    assert query.truncated is True


@pytest.mark.asyncio
async def test_only_sanitized_current_question_is_sent_to_web() -> None:
    web = FakeWebSearchProvider([(_web_result(),)])
    assistant = PolicyResearchAssistant(
        policy_researcher=FakePolicyResearcher([_policy_answer()]),
        web_search_provider=web,
        tool_executor=_executor(),
    )

    result = await assistant.answer(
        "查询最新规则 token=top-secret-value",
        include_web=True,
    )

    assert web.calls == ["查询最新规则 [REDACTED]"]
    assert result.web_search.query_redacted is True
    assert "top-secret-value" not in result.answer


@pytest.mark.asyncio
async def test_transient_web_failure_recovers_with_bounded_retry() -> None:
    web = FakeWebSearchProvider(
        [
            ConnectionError("temporary one"),
            ConnectionError("temporary two"),
            (_web_result(),),
        ]
    )
    assistant = PolicyResearchAssistant(
        policy_researcher=FakePolicyResearcher([_policy_answer()]),
        web_search_provider=web,
        tool_executor=_executor(),
    )

    result = await assistant.answer("查询公开要求", include_web=True)

    assert len(web.calls) == 3
    assert result.status is ResearchStatus.COMPLETED
    assert result.resilience.recovered is True
    assert result.resilience.tool_calls[-1].tool is ToolName.WEB_SEARCH
    assert result.resilience.tool_calls[-1].outcome is ToolCallOutcome.RECOVERED
    assert result.resilience.tool_calls[-1].attempts == 3


@pytest.mark.asyncio
async def test_web_failure_returns_partial_internal_answer_without_leaking_exception() -> None:
    secret = "api_key=web-provider-secret"
    assistant = PolicyResearchAssistant(
        policy_researcher=FakePolicyResearcher([_policy_answer()]),
        web_search_provider=FakeWebSearchProvider([ConnectionError(secret)]),
        tool_executor=_executor(max_attempts=2),
    )

    result = await assistant.answer("查询公开要求", include_web=True)

    assert result.status is ResearchStatus.PARTIAL
    assert result.web_search.status is WebSearchStatus.FAILED
    assert result.policy_answer is not None
    assert result.external_sources == ()
    assert result.resilience.degraded is True
    assert secret not in result.answer
    assert "web-provider-secret" not in str(result.resilience)


@pytest.mark.asyncio
async def test_external_results_remain_advisory_when_internal_rag_fails() -> None:
    assistant = PolicyResearchAssistant(
        policy_researcher=FakePolicyResearcher([ConnectionError("rag down")]),
        web_search_provider=FakeWebSearchProvider([(_web_result(),)]),
        tool_executor=_executor(max_attempts=1),
    )

    result = await assistant.answer("研究差旅要求", include_web=True)

    assert result.status is ResearchStatus.PARTIAL
    assert result.policy_answer is None
    assert result.external_sources[0].source_id == "W1"
    assert "不能替代企业内部有效制度" in result.answer
    assert result.resilience.degraded is True


@pytest.mark.asyncio
async def test_returns_unavailable_when_no_source_branch_succeeds() -> None:
    assistant = PolicyResearchAssistant(
        policy_researcher=FakePolicyResearcher([ConnectionError("rag down")]),
        web_search_provider=FakeWebSearchProvider([ConnectionError("web down")]),
        tool_executor=_executor(max_attempts=1),
    )

    result = await assistant.answer("研究差旅要求", include_web=True)

    assert result.status is ResearchStatus.UNAVAILABLE
    assert result.policy_answer is None
    assert result.external_sources == ()
    assert result.web_search.status is WebSearchStatus.FAILED
    assert len(result.resilience.tool_calls) == 2


@pytest.mark.asyncio
async def test_no_internal_evidence_or_web_results_is_partial() -> None:
    assistant = PolicyResearchAssistant(
        policy_researcher=FakePolicyResearcher([_policy_answer(with_citation=False)]),
        web_search_provider=FakeWebSearchProvider([()]),
        tool_executor=_executor(),
    )

    result = await assistant.answer("未知事项", include_web=True)

    assert result.status is ResearchStatus.PARTIAL
    assert result.web_search.status is WebSearchStatus.NO_RESULTS
    assert result.resilience.degraded is False
