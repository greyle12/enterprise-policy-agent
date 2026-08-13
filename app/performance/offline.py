from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from app.evaluation.runtime import (
    EvaluationRuntime,
    OfflineIntentClassifier,
    build_evaluation_runtime,
)
from app.llm.client import ChatMessage
from app.performance.benchmark import BenchmarkScenario, PerformanceBenchmarkRunner
from app.performance.models import (
    PerformanceBudget,
    PerformanceReport,
    PerformanceScenarioName,
)
from app.rag.policy_answer_service import PolicyAnswerService
from app.rag.policy_retriever import PolicyRetriever
from app.research import (
    PolicyResearchAssistant,
    WebSearchProviderName,
    WebSearchResult,
)
from app.resilience import ResilientToolExecutor

_QUESTION = "差旅住宿费如何报销？"

DEFAULT_PERFORMANCE_BUDGETS: dict[PerformanceScenarioName, PerformanceBudget] = {
    PerformanceScenarioName.RUNTIME_STARTUP: PerformanceBudget(
        scenario=PerformanceScenarioName.RUNTIME_STARTUP,
        max_p95_ms=750.0,
    ),
    PerformanceScenarioName.POLICY_RAG_ANSWER: PerformanceBudget(
        scenario=PerformanceScenarioName.POLICY_RAG_ANSWER,
        max_p95_ms=150.0,
    ),
    PerformanceScenarioName.AGENT_MATERIAL_ROUTE: PerformanceBudget(
        scenario=PerformanceScenarioName.AGENT_MATERIAL_ROUTE,
        max_p95_ms=250.0,
    ),
    PerformanceScenarioName.AGENT_APPROVAL_ROUTE: PerformanceBudget(
        scenario=PerformanceScenarioName.AGENT_APPROVAL_ROUTE,
        max_p95_ms=250.0,
    ),
    PerformanceScenarioName.POLICY_RESEARCH_HYBRID: PerformanceBudget(
        scenario=PerformanceScenarioName.POLICY_RESEARCH_HYBRID,
        max_p95_ms=250.0,
    ),
}


class DeterministicHashEmbeddingProvider:
    """只用于离线性能基线；不下载模型，也不衡量真实 BGE 推理。"""

    def __init__(self, dimension: int = 32) -> None:
        if dimension < 8 or dimension > 32:
            raise ValueError("dimension must be between 8 and 32")
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def _embed(self, text: str) -> list[float]:
        digest = sha256(text.encode("utf-8")).digest()[: self._dimension]
        vector = [(value - 127.5) / 127.5 for value in digest]
        magnitude = math.sqrt(sum(value * value for value in vector))
        return [value / magnitude for value in vector]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class _OfflineCitationLLM:
    """固定返回已存在的 S1；只测量 RAG 编排，不冒充真实 LLM 延迟。"""

    def __init__(self) -> None:
        self.call_count = 0

    async def chat(self, messages: list[ChatMessage]) -> str:
        self.call_count += 1
        if not messages:
            raise ValueError("messages must not be empty")
        return "离线性能基准已完成制度证据编排。[S1]"


class _OfflineWebSearchProvider:
    provider_name = WebSearchProviderName.TAVILY
    available = True

    def __init__(self) -> None:
        self.call_count = 0

    async def search(self, query: str) -> tuple[WebSearchResult, ...]:
        self.call_count += 1
        if not query:
            raise ValueError("query must not be blank")
        return (
            WebSearchResult(
                title="公开差旅凭证指南",
                url="https://example.gov.cn/travel-evidence",
                snippet="离线性能基准使用的固定外部摘要。",
                score=0.88,
                published_date="2026-08-01",
            ),
        )

    async def aclose(self) -> None:
        return None


@dataclass(slots=True)
class OfflinePerformanceRuntime:
    """复用真实解析、检索、规则和 LangGraph，替换真实模型与网络。"""

    policy_directory: Path
    policy_answer_service: PolicyAnswerService
    evaluation_runtime: EvaluationRuntime
    research_assistant: PolicyResearchAssistant

    @classmethod
    def build(cls, policy_directory: str | Path) -> OfflinePerformanceRuntime:
        directory = Path(policy_directory)
        retriever = PolicyRetriever.from_directory(
            directory,
            embedding_provider=DeterministicHashEmbeddingProvider(),
        )
        policy_answer_service = PolicyAnswerService(
            retriever=retriever,
            llm_client=_OfflineCitationLLM(),
        )
        evaluation_runtime = build_evaluation_runtime(
            policy_directory=directory,
            intent_classifier=OfflineIntentClassifier(),
        )
        research_assistant = PolicyResearchAssistant(
            policy_researcher=policy_answer_service,
            web_search_provider=_OfflineWebSearchProvider(),
            tool_executor=ResilientToolExecutor(
                safe_tool_timeout_seconds=1.0,
                mutation_tool_timeout_seconds=1.0,
                max_attempts=1,
                retry_min_wait_seconds=0.0,
                retry_max_wait_seconds=0.0,
                error_id_factory=lambda: "ERR-DAY22-OFFLINE",
            ),
        )
        return cls(
            policy_directory=directory,
            policy_answer_service=policy_answer_service,
            evaluation_runtime=evaluation_runtime,
            research_assistant=research_assistant,
        )

    @staticmethod
    def _session_id(scenario: PerformanceScenarioName, iteration: int) -> str:
        run = f"W{abs(iteration)}" if iteration < 0 else f"M{iteration + 1}"
        scenario_token = scenario.value.replace("_", "-").upper()
        return f"PERF-{scenario_token}-{run}"

    def scenarios(self) -> tuple[BenchmarkScenario, ...]:
        router = self.evaluation_runtime.router

        async def policy_rag_answer(_: int) -> object:
            return await self.policy_answer_service.answer(_QUESTION)

        async def material_route(iteration: int) -> object:
            scenario = PerformanceScenarioName.AGENT_MATERIAL_ROUTE
            return await router.route(
                "出差报销需要哪些材料？",
                session_id=self._session_id(scenario, iteration),
            )

        async def approval_route(iteration: int) -> object:
            scenario = PerformanceScenarioName.AGENT_APPROVAL_ROUTE
            return await router.route(
                "采购人民币 6000 元的设备需要谁审批？",
                session_id=self._session_id(scenario, iteration),
            )

        async def hybrid_research(_: int) -> object:
            return await self.research_assistant.answer(
                "对比内部差旅凭证要求和公开资料",
                include_web=True,
            )

        return (
            BenchmarkScenario(
                name=PerformanceScenarioName.RUNTIME_STARTUP,
                description="解析五份制度、构建离线检索器、业务规则和 LangGraph 运行时。",
                operation=lambda _: OfflinePerformanceRuntime.build(self.policy_directory),
                budget=DEFAULT_PERFORMANCE_BUDGETS[PerformanceScenarioName.RUNTIME_STARTUP],
            ),
            BenchmarkScenario(
                name=PerformanceScenarioName.POLICY_RAG_ANSWER,
                description="执行确定性向量检索、上下文构造和引用校验；不调用真实 BGE 或 LLM。",
                operation=policy_rag_answer,
                budget=DEFAULT_PERFORMANCE_BUDGETS[PerformanceScenarioName.POLICY_RAG_ANSWER],
            ),
            BenchmarkScenario(
                name=PerformanceScenarioName.AGENT_MATERIAL_ROUTE,
                description="执行离线意图识别、LangGraph 路由和真实材料规则检查。",
                operation=material_route,
                budget=DEFAULT_PERFORMANCE_BUDGETS[PerformanceScenarioName.AGENT_MATERIAL_ROUTE],
            ),
            BenchmarkScenario(
                name=PerformanceScenarioName.AGENT_APPROVAL_ROUTE,
                description="执行离线意图识别、LangGraph 路由和真实审批规则计算。",
                operation=approval_route,
                budget=DEFAULT_PERFORMANCE_BUDGETS[PerformanceScenarioName.AGENT_APPROVAL_ROUTE],
            ),
            BenchmarkScenario(
                name=PerformanceScenarioName.POLICY_RESEARCH_HYBRID,
                description="执行内部制度 RAG、研究编排和固定 Web Provider；不产生网络调用。",
                operation=hybrid_research,
                budget=DEFAULT_PERFORMANCE_BUDGETS[PerformanceScenarioName.POLICY_RESEARCH_HYBRID],
            ),
        )


async def run_offline_performance_benchmark(
    *,
    policy_directory: str | Path,
    warmup_iterations: int = 1,
    measured_iterations: int = 5,
) -> PerformanceReport:
    runtime = OfflinePerformanceRuntime.build(policy_directory)
    runner = PerformanceBenchmarkRunner(
        scenarios=runtime.scenarios(),
        warmup_iterations=warmup_iterations,
        measured_iterations=measured_iterations,
    )
    return await runner.run()
