from __future__ import annotations

import asyncio
import json

from app.rag.policy_answer_service import PolicyAnswer
from app.rag.policy_context import PolicyCitation
from app.research import (
    PolicyResearchAssistant,
    WebSearchProviderName,
    WebSearchResult,
)
from app.resilience import ResilientToolExecutor, ToolName

_SECRET_VALUE = "verify-secret-value"


class _OfflinePolicyResearcher:
    async def answer(self, question: str) -> PolicyAnswer:
        citation = PolicyCitation(
            source_id="S1",
            chunk_id="TRAVEL_POLICY_001__article_008",
            document_title="差旅报销管理制度",
            chapter_title="费用报销",
            article_label="第八条",
            article_title="住宿费报销",
            score=0.96,
        )
        return PolicyAnswer(
            question=question,
            answer="住宿费应在制度标准内凭发票报销。[S1]",
            citations=(citation,),
        )


class _RecoveringWebSearchProvider:
    provider_name = WebSearchProviderName.TAVILY
    available = True

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(self, query: str) -> tuple[WebSearchResult, ...]:
        self.queries.append(query)
        if len(self.queries) < 3:
            raise ConnectionError("offline simulated transient failure")
        return (
            WebSearchResult(
                title="公开差旅凭证指南",
                url="https://example.gov.cn/travel-evidence",
                snippet="公开资料说明了差旅凭证的一般要求。",
                score=0.87,
                published_date="2026-07-01",
            ),
        )

    async def aclose(self) -> None:
        return None


async def run_verification() -> dict[str, object]:
    """完全离线验收 RAG、显式 Web 授权、脱敏和重试恢复。"""

    web_provider = _RecoveringWebSearchProvider()
    assistant = PolicyResearchAssistant(
        policy_researcher=_OfflinePolicyResearcher(),
        web_search_provider=web_provider,
        tool_executor=ResilientToolExecutor(
            safe_tool_timeout_seconds=1.0,
            mutation_tool_timeout_seconds=1.0,
            max_attempts=3,
            retry_min_wait_seconds=0.0,
            retry_max_wait_seconds=0.0,
            error_id_factory=lambda: "ERR-DAY21VERIFY",
        ),
    )

    local_only = await assistant.answer("差旅住宿费如何报销？")
    hybrid = await assistant.answer(
        f"对比公开差旅凭证要求 token={_SECRET_VALUE}",
        include_web=True,
    )
    web_record = next(
        record for record in hybrid.resilience.tool_calls if record.tool is ToolName.WEB_SEARCH
    )
    serialized = json.dumps(
        {
            "answer": hybrid.answer,
            "queries": web_provider.queries,
            "resilience": str(hybrid.resilience),
        },
        ensure_ascii=False,
    )
    checks = {
        "local_only_did_not_call_web": local_only.web_search.executed is False,
        "hybrid_used_internal_source": (
            hybrid.policy_answer is not None
            and [item.source_id for item in hybrid.policy_answer.citations] == ["S1"]
        ),
        "hybrid_used_external_source": (
            [item.source_id for item in hybrid.external_sources] == ["W1"]
        ),
        "query_was_redacted": (
            hybrid.web_search.query_redacted
            and all(_SECRET_VALUE not in query for query in web_provider.queries)
        ),
        "web_retry_recovered": (web_record.attempts == 3 and hybrid.resilience.recovered),
        "external_source_is_advisory": ("不能替代企业内部有效制度" in hybrid.answer),
        "sensitive_value_not_exposed": _SECRET_VALUE not in serialized,
    }
    return {
        "passed": all(checks.values()),
        "assistant_name": hybrid.assistant_name,
        "assistant_version": hybrid.assistant_version,
        "local_only": {
            "status": local_only.status.value,
            "web_status": local_only.web_search.status.value,
            "web_executed": local_only.web_search.executed,
        },
        "hybrid": {
            "status": hybrid.status.value,
            "web_status": hybrid.web_search.status.value,
            "internal_sources": [
                item.source_id
                for item in (
                    hybrid.policy_answer.citations if hybrid.policy_answer is not None else ()
                )
            ],
            "external_sources": [item.source_id for item in hybrid.external_sources],
            "query_redacted": hybrid.web_search.query_redacted,
            "web_attempts": web_record.attempts,
            "recovered": hybrid.resilience.recovered,
        },
        "checks": checks,
    }


def main() -> int:
    report = asyncio.run(run_verification())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
