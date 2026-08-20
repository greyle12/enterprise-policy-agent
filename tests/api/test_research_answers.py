from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_policy_research_assistant
from app.main import create_app
from app.rag.policy_answer_service import PolicyAnswer
from app.rag.policy_context import PolicyCitation
from app.research import (
    ExternalResearchSource,
    PolicyResearchAnswer,
    ResearchStatus,
    WebSearchInfo,
    WebSearchProviderName,
    WebSearchStatus,
)
from app.resilience import (
    AgentResilienceInfo,
    ToolCallOutcome,
    ToolCallRecord,
    ToolName,
    ToolOperationKind,
)

app = create_app(enable_lifespan=False)


class FakeResearchAssistant:
    def __init__(self, result: PolicyResearchAnswer) -> None:
        self.result = result
        self.calls: list[tuple[str, bool]] = []

    async def answer(
        self,
        question: str,
        *,
        include_web: bool = False,
    ) -> PolicyResearchAnswer:
        self.calls.append((question, include_web))
        return self.result


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _result() -> PolicyResearchAnswer:
    citation = PolicyCitation(
        source_id="S1",
        chunk_id="travel-001",
        document_title="差旅报销管理制度",
        chapter_title="住宿费",
        article_label="第八条",
        article_title="报销要求",
        score=0.94,
        source_page_start=4,
        source_page_end=4,
        source_block_start=9,
        source_block_end=11,
        source_ocr_engine="tesseract",
        source_ocr_unit_kind="page",
        source_ocr_unit_numbers=(4,),
        source_ocr_confidence_min=0.91,
    )
    policy_answer = PolicyAnswer(
        question="对比差旅报销要求",
        answer="内部制度要求凭住宿发票报销。[S1]",
        citations=(citation,),
    )
    return PolicyResearchAnswer(
        question="对比差旅报销要求",
        assistant_name="policy_research_assistant",
        assistant_version="1.0",
        status=ResearchStatus.COMPLETED,
        answer=(
            "## 内部制度依据\n\n内部制度要求凭住宿发票报销。[S1]\n\n"
            "## 外部公开资料（仅供参考）\n\n- 公开指南：公开摘要 [W1]"
        ),
        policy_answer=policy_answer,
        external_sources=(
            ExternalResearchSource(
                source_id="W1",
                title="公开指南",
                url="https://example.gov.cn/travel",
                snippet="公开摘要",
                score=0.88,
                published_date="2026-07-01",
            ),
        ),
        web_search=WebSearchInfo(
            requested=True,
            executed=True,
            provider=WebSearchProviderName.TAVILY,
            status=WebSearchStatus.COMPLETED,
            query_redacted=False,
            query_truncated=False,
            result_count=1,
        ),
        resilience=AgentResilienceInfo(
            degraded=False,
            recovered=False,
            tool_calls=(
                ToolCallRecord(
                    tool=ToolName.POLICY_RESEARCH,
                    operation=ToolOperationKind.READ_ONLY,
                    outcome=ToolCallOutcome.SUCCESS,
                    attempts=1,
                    max_attempts=3,
                    timeout_seconds=65.0,
                    retry_safe=True,
                ),
                ToolCallRecord(
                    tool=ToolName.WEB_SEARCH,
                    operation=ToolOperationKind.READ_ONLY,
                    outcome=ToolCallOutcome.SUCCESS,
                    attempts=1,
                    max_attempts=3,
                    timeout_seconds=65.0,
                    retry_safe=True,
                ),
            ),
        ),
    )


def test_returns_research_sources_with_explicit_authority_boundary() -> None:
    assistant = FakeResearchAssistant(_result())
    app.dependency_overrides[get_policy_research_assistant] = lambda: assistant

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/research/answers",
            json={
                "question": "  对比差旅报销要求  ",
                "include_web": True,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["assistant"] == {
        "name": "policy_research_assistant",
        "version": "1.0",
    }
    assert payload["status"] == "completed"
    assert payload["internal_sources"][0]["source_id"] == "S1"
    assert payload["internal_sources"][0]["document_title"] == "差旅报销管理制度"
    assert payload["internal_sources"][0]["source_page_start"] == 4
    assert payload["internal_sources"][0]["source_page_end"] == 4
    assert payload["internal_sources"][0]["source_block_start"] == 9
    assert payload["internal_sources"][0]["source_block_end"] == 11
    assert payload["internal_sources"][0]["source_ocr_engine"] == "tesseract"
    assert payload["internal_sources"][0]["source_ocr_unit_kind"] == "page"
    assert payload["internal_sources"][0]["source_ocr_unit_numbers"] == [4]
    assert payload["internal_sources"][0]["source_ocr_confidence_min"] == 0.91
    assert payload["external_sources"][0] == {
        "source_id": "W1",
        "title": "公开指南",
        "url": "https://example.gov.cn/travel",
        "snippet": "公开摘要",
        "score": 0.88,
        "published_date": "2026-07-01",
    }
    assert payload["source_policy"] == {
        "internal_policy_authoritative": True,
        "external_web_advisory": True,
        "external_web_used_for_workflow": False,
    }
    assert payload["web_search"]["executed"] is True
    assert payload["resilience"]["tool_calls"][1]["tool"] == "web_search"
    assert assistant.calls == [("对比差旅报销要求", True)]


def test_web_search_is_opt_in_by_default() -> None:
    result = _result()
    assistant = FakeResearchAssistant(result)
    app.dependency_overrides[get_policy_research_assistant] = lambda: assistant

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/research/answers",
            json={"question": "查询内部制度"},
        )

    assert response.status_code == 200
    assert assistant.calls == [("查询内部制度", False)]


@pytest.mark.parametrize(
    "question",
    ["", "   ", "甲" * 1001],
)
def test_rejects_invalid_research_question(question: str) -> None:
    app.dependency_overrides[get_policy_research_assistant] = lambda: FakeResearchAssistant(
        _result()
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/research/answers",
            json={"question": question},
        )

    assert response.status_code == 422
