from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path

from app.agent.router import AgentRouter
from app.evaluation.runtime import OfflineIntentClassifier
from app.llm.client import ChatMessage
from app.rag.policy_answer_service import PolicyAnswerService
from app.rag.policy_chunker import chunk_policy_directory
from app.rag.policy_retriever import AccessControlledPolicyRetriever, PolicyRetriever
from app.research import (
    PolicyResearchAssistant,
    WebSearchProviderName,
    WebSearchResult,
)
from app.resilience import ResilientToolExecutor
from app.schemas.policy import SecurityLevel
from app.security import (
    PolicyAccessContext,
    PromptInjectionGuard,
    TrustedIdentitySource,
)
from app.tools.approval_check import ApprovalRuleChecker
from app.tools.draft_generation import ApplicationDraftGenerator
from app.tools.draft_models import DraftUserContext
from app.tools.material_check import RequiredMaterialsChecker
from app.tools.mock_approval_submission import MockApprovalSubmitter

_TEXT_RUN_PATTERN = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]+")
_DEMO_CLOCK = datetime(2026, 8, 20, 0, 0, tzinfo=UTC)
_DEMO_AS_OF_DATE = date(2026, 8, 20)


class DeterministicLexicalEmbeddingProvider:
    """Offline hashed n-gram vectors; useful for demos, never a BGE substitute."""

    def __init__(self, dimension: int = 1024) -> None:
        if dimension < 128:
            raise ValueError("dimension must be at least 128")
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    @staticmethod
    def _tokens(text: str) -> tuple[tuple[str, float], ...]:
        normalized = unicodedata.normalize("NFKC", text).casefold()
        tokens: list[tuple[str, float]] = []
        for run in _TEXT_RUN_PATTERN.findall(normalized):
            if run.isascii():
                tokens.append((f"word:{run}", 2.0))
                continue
            for width, weight in ((1, 0.25), (2, 1.5), (3, 2.5)):
                tokens.extend(
                    (f"cjk{width}:{run[index : index + width]}", weight)
                    for index in range(len(run) - width + 1)
                )
        return tuple(tokens)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimension
        for token, weight in self._tokens(text):
            digest = sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:8], "big") % self._dimension
            vector[index] += weight
        magnitude = math.sqrt(sum(value * value for value in vector))
        if magnitude == 0.0:
            raise ValueError("text must contain searchable characters")
        return [value / magnitude for value in vector]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class OfflinePortfolioLLM:
    """Fixed citation response used to exercise orchestration without a live model."""

    def __init__(self) -> None:
        self.call_count = 0

    async def chat(self, messages: list[ChatMessage]) -> str:
        if not messages:
            raise ValueError("messages must not be empty")
        self.call_count += 1
        return "住宿费应按有效制度标准并凭合规材料报销。[S1]"


class OfflinePortfolioWebSearchProvider:
    """Fixed public-source fixture that performs no HTTP or DNS operations."""

    provider_name = WebSearchProviderName.TAVILY
    available = True

    def __init__(self) -> None:
        self.call_count = 0

    async def search(self, query: str) -> tuple[WebSearchResult, ...]:
        if not query.strip():
            raise ValueError("query must not be blank")
        self.call_count += 1
        return (
            WebSearchResult(
                title="公开差旅凭证指南（离线夹具）",
                url="https://example.gov.cn/travel-evidence",
                snippet="公开资料说明差旅凭证的一般要求。",
                score=0.88,
                published_date="2026-08-01",
            ),
        )

    async def aclose(self) -> None:
        return None


@dataclass(slots=True)
class OfflinePortfolioRuntime:
    """Real project components wired to deterministic offline providers."""

    policy_answer_service: PolicyAnswerService
    router: AgentRouter
    research_assistant: PolicyResearchAssistant
    retriever: AccessControlledPolicyRetriever
    prompt_guard: PromptInjectionGuard
    llm: OfflinePortfolioLLM
    web_search: OfflinePortfolioWebSearchProvider
    policy_document_count: int

    @classmethod
    def build(cls, policy_directory: str | Path) -> OfflinePortfolioRuntime:
        directory = Path(policy_directory)
        chunks = tuple(chunk_policy_directory(directory))
        raw_retriever = PolicyRetriever(
            embedding_provider=DeterministicLexicalEmbeddingProvider(),
            chunks=chunks,
        )
        access_context = PolicyAccessContext(
            employee_id="PORTFOLIO-EMP-001",
            department="演示部门",
            roles=("EMPLOYEE",),
            security_clearance=SecurityLevel.INTERNAL,
            region="中国大陆",
            identity_source=TrustedIdentitySource.TEST_FIXTURE,
        )
        retriever = raw_retriever.restrict(
            access_context,
            as_of_date=_DEMO_AS_OF_DATE,
        )
        prompt_guard = PromptInjectionGuard()
        llm = OfflinePortfolioLLM()
        policy_answer_service = PolicyAnswerService(
            retriever=retriever,
            llm_client=llm,
            prompt_guard=prompt_guard,
        )

        material_checker = RequiredMaterialsChecker.from_policy_directory(directory)
        approval_checker = ApprovalRuleChecker.from_policy_directory(directory)
        draft_generator = ApplicationDraftGenerator.from_policy_directory(
            directory,
            material_checker=material_checker,
            approval_checker=approval_checker,
            user_context=DraftUserContext(
                employee_id="PORTFOLIO-EMP-001",
                employee_name="作品集演示用户",
                department="演示部门",
                roles=("EMPLOYEE",),
                region="中国大陆",
                identity_source="trusted_portfolio_fixture",
            ),
            clock=lambda: _DEMO_CLOCK,
            session_id="PORTFOLIO-DEMO",
        )
        router = AgentRouter(
            intent_classifier=OfflineIntentClassifier(),
            policy_answer_service=policy_answer_service,
            material_checker=material_checker,
            approval_checker=approval_checker,
            draft_generator=draft_generator,
            submission_service=MockApprovalSubmitter(),
            prompt_guard=prompt_guard,
        )
        web_search = OfflinePortfolioWebSearchProvider()
        research_assistant = PolicyResearchAssistant(
            policy_researcher=policy_answer_service,
            web_search_provider=web_search,
            tool_executor=ResilientToolExecutor(
                safe_tool_timeout_seconds=1.0,
                mutation_tool_timeout_seconds=1.0,
                max_attempts=1,
                retry_min_wait_seconds=0.0,
                retry_max_wait_seconds=0.0,
                error_id_factory=lambda: "ERR-DAY30-OFFLINE",
            ),
            prompt_guard=prompt_guard,
        )
        return cls(
            policy_answer_service=policy_answer_service,
            router=router,
            research_assistant=research_assistant,
            retriever=retriever,
            prompt_guard=prompt_guard,
            llm=llm,
            web_search=web_search,
            policy_document_count=len({chunk.document_id for chunk in chunks}),
        )
