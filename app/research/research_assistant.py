from __future__ import annotations

from typing import Protocol

from app.memory.conversation import sanitize_memory_content
from app.rag.policy_answer_service import PolicyAnswer
from app.research.models import (
    ExternalQuery,
    ExternalResearchSource,
    PolicyResearchAnswer,
    ResearchStatus,
    WebSearchInfo,
    WebSearchStatus,
)
from app.research.web_search import WebSearchProvider
from app.resilience import (
    AgentResilienceInfo,
    ResilientToolExecutor,
    ToolCallOutcome,
    ToolCallRecord,
    ToolExecutionError,
    ToolName,
    ToolOperationKind,
)
from app.security import PromptInjectionGuard

_MAX_EXTERNAL_QUERY_CHARACTERS = 500
_EXTERNAL_BOUNDARY_NOTICE = (
    "边界说明：外部公开资料只用于研究参考，不能替代企业内部有效制度，"
    "也不会用于材料、审批、草稿或提交判断。"
)


class PolicyResearcher(Protocol):
    """研究助手依赖的最小内部制度 RAG 接口。"""

    async def answer(self, question: str) -> PolicyAnswer:
        """仅依据内部制度证据回答问题。"""

        ...


def sanitize_external_query(question: str) -> ExternalQuery:
    """只允许把当前问题的脱敏、受限版本发送给外部搜索。"""

    normalized = question.strip()
    if not normalized:
        raise ValueError("question must not be blank")
    text, redacted, truncated = sanitize_memory_content(
        normalized,
        character_limit=_MAX_EXTERNAL_QUERY_CHARACTERS,
    )
    return ExternalQuery(
        text=text,
        redacted=redacted,
        truncated=truncated,
    )


def _resilience_from(records: list[ToolCallRecord]) -> AgentResilienceInfo:
    return AgentResilienceInfo(
        degraded=any(record.outcome is ToolCallOutcome.FAILED for record in records),
        recovered=any(record.outcome is ToolCallOutcome.RECOVERED for record in records),
        tool_calls=tuple(records),
    )


def _status_for(
    *,
    policy_answer: PolicyAnswer | None,
    include_web: bool,
    web_status: WebSearchStatus,
    external_sources: tuple[ExternalResearchSource, ...],
) -> ResearchStatus:
    internal_grounded = policy_answer is not None and bool(policy_answer.citations)
    web_complete = not include_web or web_status is WebSearchStatus.COMPLETED
    if internal_grounded and web_complete:
        return ResearchStatus.COMPLETED
    if policy_answer is not None or external_sources:
        return ResearchStatus.PARTIAL
    return ResearchStatus.UNAVAILABLE


def _compose_answer(
    *,
    policy_answer: PolicyAnswer | None,
    policy_failure_message: str | None,
    include_web: bool,
    web_status: WebSearchStatus,
    web_failure_message: str | None,
    external_sources: tuple[ExternalResearchSource, ...],
) -> str:
    internal_text = (
        policy_answer.answer
        if policy_answer is not None
        else (policy_failure_message or "内部制度研究暂时不可用。")
    )
    sections = [
        "## 内部制度依据",
        internal_text,
    ]
    if not include_web:
        return "\n\n".join(sections)

    sections.append("## 外部公开资料（仅供参考）")
    if external_sources:
        sections.extend(
            (f"- {source.title}：{source.snippet or '该来源未提供摘要。'} [{source.source_id}]")
            for source in external_sources
        )
    elif web_status is WebSearchStatus.DISABLED:
        sections.append("外部搜索未配置，本次没有向外部服务发送查询。")
    elif web_status is WebSearchStatus.NO_RESULTS:
        sections.append("外部搜索已执行，但没有返回可用的公开资料。")
    else:
        sections.append(web_failure_message or "外部搜索暂时不可用。")
    sections.append(_EXTERNAL_BOUNDARY_NOTICE)
    return "\n\n".join(sections)


class PolicyResearchAssistant:
    """内部制度优先、外部搜索显式授权的研究助手。"""

    name = "policy_research_assistant"
    version = "1.0"

    def __init__(
        self,
        *,
        policy_researcher: PolicyResearcher,
        web_search_provider: WebSearchProvider,
        tool_executor: ResilientToolExecutor | None = None,
        prompt_guard: PromptInjectionGuard | None = None,
    ) -> None:
        self._policy_researcher = policy_researcher
        self._web_search_provider = web_search_provider
        self._tool_executor = tool_executor or ResilientToolExecutor()
        self._prompt_guard = prompt_guard or PromptInjectionGuard()

    async def answer(
        self,
        question: str,
        *,
        include_web: bool = False,
    ) -> PolicyResearchAnswer:
        normalized_question = question.strip()
        if not normalized_question:
            raise ValueError("question must not be blank")
        self._prompt_guard.enforce_user_input(normalized_question)

        records: list[ToolCallRecord] = []
        policy_answer: PolicyAnswer | None = None
        policy_failure_message: str | None = None
        try:
            policy_execution = await self._tool_executor.execute(
                tool=ToolName.POLICY_RESEARCH,
                operation=ToolOperationKind.READ_ONLY,
                call=lambda: self._policy_researcher.answer(normalized_question),
            )
        except ToolExecutionError as exc:
            records.append(exc.record)
            if exc.record.error is not None:
                policy_failure_message = exc.record.error.user_message
        else:
            policy_answer = policy_execution.value
            records.append(policy_execution.record)

        external_sources: tuple[ExternalResearchSource, ...] = ()
        external_query = ExternalQuery(
            text="",
            redacted=False,
            truncated=False,
        )
        web_executed = False
        web_failure_message: str | None = None
        if not include_web:
            web_status = WebSearchStatus.NOT_REQUESTED
        elif not self._web_search_provider.available:
            web_status = WebSearchStatus.DISABLED
        else:
            external_query = sanitize_external_query(normalized_question)
            web_executed = True
            try:
                web_execution = await self._tool_executor.execute(
                    tool=ToolName.WEB_SEARCH,
                    operation=ToolOperationKind.READ_ONLY,
                    call=lambda: self._web_search_provider.search(external_query.text),
                )
            except ToolExecutionError as exc:
                records.append(exc.record)
                web_status = WebSearchStatus.FAILED
                if exc.record.error is not None:
                    web_failure_message = exc.record.error.user_message
            else:
                records.append(web_execution.record)
                external_sources = tuple(
                    ExternalResearchSource(
                        source_id=f"W{index}",
                        title=result.title,
                        url=result.url,
                        snippet=result.snippet,
                        score=result.score,
                        published_date=result.published_date,
                    )
                    for index, result in enumerate(
                        web_execution.value,
                        start=1,
                    )
                )
                web_status = (
                    WebSearchStatus.COMPLETED if external_sources else WebSearchStatus.NO_RESULTS
                )

        web_search = WebSearchInfo(
            requested=include_web,
            executed=web_executed,
            provider=self._web_search_provider.provider_name,
            status=web_status,
            query_redacted=external_query.redacted,
            query_truncated=external_query.truncated,
            result_count=len(external_sources),
        )
        status = _status_for(
            policy_answer=policy_answer,
            include_web=include_web,
            web_status=web_status,
            external_sources=external_sources,
        )
        return PolicyResearchAnswer(
            question=normalized_question,
            assistant_name=self.name,
            assistant_version=self.version,
            status=status,
            answer=_compose_answer(
                policy_answer=policy_answer,
                policy_failure_message=policy_failure_message,
                include_web=include_web,
                web_status=web_status,
                web_failure_message=web_failure_message,
                external_sources=external_sources,
            ),
            policy_answer=policy_answer,
            external_sources=external_sources,
            web_search=web_search,
            resilience=_resilience_from(records),
        )
