from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.agent.intent_classifier import IntentClassifier
from app.agent.router import AgentRouter
from app.api.provider_errors import provider_capacity_error_response
from app.api.runtime_errors import unhandled_application_error_response
from app.api.security_errors import prompt_injection_blocked_response
from app.api.routes.agent_messages import (
    router as agent_messages_router,
)
from app.api.routes.agent_sessions import (
    router as agent_sessions_router,
)
from app.api.routes.cache_status import router as cache_status_router
from app.api.routes.health import router as health_router
from app.api.routes.observability import (
    metrics_router,
    router as observability_router,
)
from app.api.routes.policy_answers import (
    router as policy_answers_router,
)
from app.api.routes.provider_status import router as provider_status_router
from app.api.routes.research_answers import (
    router as research_answers_router,
)
from app.api.routes.security import router as security_router
from app.core.config import Settings, get_settings
from app.cache import (
    CacheProviderName,
    CachedLLMClient,
    DisabledLLMCache,
    LLMCacheBackend,
    RedisLLMCache,
    build_llm_cache_identity,
)
from app.llm.openai_compatible_client import (
    OpenAICompatibleLLMClient,
)
from app.llm import ConcurrencyLimitedLLMClient, ProviderCapacityError
from app.observability import HttpMetricsRegistry, RuntimeObservabilityMiddleware
from app.persistence import (
    SQLiteAgentStateStore,
    SQLiteCheckpointSaver,
    SQLiteConversationMemoryStore,
    SQLiteMockApprovalSubmitter,
)
from app.rag.embeddings import BGEEmbeddingProvider, DEFAULT_BGE_MODEL_NAME
from app.rag.indexing import PolicyDocumentIndexer
from app.rag.policy_answer_service import (
    PolicyAnswerService,
)
from app.rag.policy_retriever import PolicyRetriever
from app.rag.reranking import (
    BGERerankingProvider,
    RerankerProviderName,
    RerankingProvider,
)
from app.rag.vector_index import VectorIndex
from app.rag.vector_store import build_policy_vector_index
from app.resilience import ResilientToolExecutor
from app.research import (
    DisabledWebSearchProvider,
    PolicyResearchAssistant,
    TavilyWebSearchProvider,
    WebSearchProvider,
    WebSearchProviderName,
)
from app.tools.approval_check import ApprovalRuleChecker
from app.tools.draft_generation import ApplicationDraftGenerator
from app.tools.draft_models import DraftUserContext
from app.tools.material_check import RequiredMaterialsChecker
from app.schemas.policy import SecurityLevel
from app.security import (
    PolicyAccessContext,
    PromptInjectionBlockedError,
    PromptInjectionGuard,
    TrustedIdentitySource,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
_EMBEDDING_MODEL_NAME = DEFAULT_BGE_MODEL_NAME
_DEMO_DRAFT_USER_CONTEXT = DraftUserContext(
    employee_id="DEMO-EMP-001",
    employee_name="演示用户",
    department="演示部门",
    roles=("EMPLOYEE",),
    region="中国大陆",
    identity_source="trusted_demo_context",
)
_DEMO_POLICY_ACCESS_CONTEXT = PolicyAccessContext(
    employee_id="DEMO-EMP-001",
    department="演示部门",
    roles=("EMPLOYEE",),
    security_clearance=SecurityLevel.INTERNAL,
    region="中国大陆",
    identity_source=TrustedIdentitySource.TRUSTED_DEMO_CONTEXT,
)


def _build_policy_answer_service(
    *,
    prompt_guard: PromptInjectionGuard | None = None,
) -> tuple[
    PolicyAnswerService,
    CachedLLMClient,
    ConcurrencyLimitedLLMClient,
    VectorIndex,
]:
    """创建真实制度问答服务及其 LLM 客户端。"""

    settings = get_settings()
    embedding_provider = BGEEmbeddingProvider(
        model_name=_EMBEDDING_MODEL_NAME,
    )
    reranking_provider = _build_reranking_provider(settings)
    vector_index = build_policy_vector_index(
        settings,
        dimension=embedding_provider.dimension,
    )
    try:
        indexing_run = PolicyDocumentIndexer(
            embedding_provider=embedding_provider,
            vector_index=vector_index,
            embedding_identity=_EMBEDDING_MODEL_NAME,
            pipeline_version=settings.rag_index_pipeline_version,
        ).synchronize_directory(_POLICY_DIRECTORY)
        raw_retriever = PolicyRetriever(
            chunks=indexing_run.chunks,
            embedding_provider=embedding_provider,
            reranking_provider=reranking_provider,
            rerank_candidate_k=settings.rag_reranker_candidate_k,
            vector_index=vector_index,
            index_vectors=False,
        )
        retriever = raw_retriever.restrict(_DEMO_POLICY_ACCESS_CONTEXT)

        raw_llm_client = OpenAICompatibleLLMClient.from_settings(settings)
        provider_limiter = _build_llm_provider_limiter(settings, raw_llm_client)
        cache_backend = _build_llm_cache_backend(settings)
        llm_client = CachedLLMClient(
            upstream=provider_limiter,
            backend=cache_backend,
            identity=build_llm_cache_identity(
                base_url=settings.llm_base_url,
                model=settings.llm_model,
            ),
            ttl_seconds=settings.llm_cache_ttl_seconds,
            max_request_bytes=settings.llm_cache_max_request_bytes,
            singleflight_enabled=settings.llm_singleflight_enabled,
            singleflight_max_keys=settings.llm_singleflight_max_keys,
        )

        service = PolicyAnswerService(
            retriever=retriever,
            llm_client=llm_client,
            prompt_guard=prompt_guard,
        )
    except BaseException:
        vector_index.close()
        raise

    return service, llm_client, provider_limiter, vector_index


def _build_policy_vector_index(settings: Settings, *, dimension: int) -> VectorIndex:
    """Backward-compatible wrapper around the shared Vector Store factory."""

    return build_policy_vector_index(settings, dimension=dimension)


def _build_reranking_provider(settings: Settings) -> RerankingProvider | None:
    """Create the explicit optional Cross-Encoder boundary."""

    if settings.rag_reranker_provider is RerankerProviderName.DISABLED:
        return None
    return BGERerankingProvider(
        model_name=settings.rag_reranker_model_name,
        device=settings.rag_reranker_device,
        batch_size=settings.rag_reranker_batch_size,
    )


def _build_llm_provider_limiter(
    settings: Settings,
    upstream: OpenAICompatibleLLMClient,
) -> ConcurrencyLimitedLLMClient:
    """Create the optional process-local provider capacity boundary."""

    return ConcurrencyLimitedLLMClient(
        upstream=upstream,
        enabled=settings.llm_provider_limit_enabled,
        max_concurrency=settings.llm_provider_max_concurrency,
        max_queue=settings.llm_provider_max_queue,
        queue_timeout_seconds=settings.llm_provider_queue_timeout_seconds,
    )


def _build_llm_cache_backend(settings: Settings) -> LLMCacheBackend:
    """Create the explicitly configured optional LLM cache backend."""

    if settings.llm_cache_provider is CacheProviderName.DISABLED:
        return DisabledLLMCache()
    return RedisLLMCache.from_url(
        url=settings.redis_url,
        namespace=settings.llm_cache_namespace,
        timeout_seconds=settings.redis_timeout_seconds,
        max_value_bytes=settings.llm_cache_max_value_bytes,
    )


def _build_web_search_provider(
    settings: Settings,
) -> WebSearchProvider:
    """根据显式配置创建外部搜索；默认保持关闭。"""

    if settings.web_search_provider is WebSearchProviderName.DISABLED:
        return DisabledWebSearchProvider()
    api_key = settings.tavily_api_key
    if api_key is None:
        raise RuntimeError("validated Tavily configuration is missing api key")
    return TavilyWebSearchProvider(
        api_key=api_key.get_secret_value(),
        timeout_seconds=settings.web_search_timeout_seconds,
        max_results=settings.web_search_max_results,
    )


@asynccontextmanager
async def _lifespan(
    application: FastAPI,
) -> AsyncIterator[None]:
    """初始化并释放应用级共享资源。"""

    prompt_guard = application.state.prompt_security_guard
    service, llm_client, provider_limiter, vector_index = _build_policy_answer_service(
        prompt_guard=prompt_guard,
    )
    material_checker = RequiredMaterialsChecker.from_policy_directory(_POLICY_DIRECTORY)
    approval_checker = ApprovalRuleChecker.from_policy_directory(_POLICY_DIRECTORY)
    settings = get_settings()
    state_store = SQLiteAgentStateStore(settings.sqlite_database_path)
    tool_executor = ResilientToolExecutor(
        safe_tool_timeout_seconds=(settings.agent_safe_tool_timeout_seconds),
        mutation_tool_timeout_seconds=(settings.agent_mutation_tool_timeout_seconds),
        max_attempts=settings.agent_tool_max_attempts,
        retry_min_wait_seconds=(settings.agent_retry_min_wait_seconds),
        retry_max_wait_seconds=(settings.agent_retry_max_wait_seconds),
    )
    web_search_provider = _build_web_search_provider(settings)
    agent_router = AgentRouter(
        intent_classifier=IntentClassifier(
            llm_client=llm_client,
        ),
        policy_answer_service=service,
        material_checker=material_checker,
        approval_checker=approval_checker,
        draft_generator=(
            ApplicationDraftGenerator.from_policy_directory(
                _POLICY_DIRECTORY,
                material_checker=material_checker,
                approval_checker=approval_checker,
                user_context=_DEMO_DRAFT_USER_CONTEXT,
            )
        ),
        submission_service=SQLiteMockApprovalSubmitter(settings.sqlite_database_path),
        checkpointer=SQLiteCheckpointSaver(settings.sqlite_database_path),
        state_persister=state_store,
        memory_store=SQLiteConversationMemoryStore(settings.sqlite_database_path),
        tool_executor=tool_executor,
        prompt_guard=prompt_guard,
    )
    policy_research_assistant = PolicyResearchAssistant(
        policy_researcher=service,
        web_search_provider=web_search_provider,
        tool_executor=tool_executor,
        prompt_guard=prompt_guard,
    )
    application.state.policy_answer_service = service
    application.state.policy_research_assistant = policy_research_assistant
    application.state.agent_router = agent_router
    application.state.agent_state_store = state_store
    application.state.llm_cache = llm_client
    application.state.llm_provider_limiter = provider_limiter
    application.state.policy_vector_index = vector_index

    try:
        yield
    finally:
        try:
            await web_search_provider.aclose()
        finally:
            try:
                await llm_client.close()
            finally:
                vector_index.close()
        del application.state.policy_research_assistant
        del application.state.agent_state_store
        del application.state.agent_router
        del application.state.policy_answer_service
        del application.state.llm_cache
        del application.state.llm_provider_limiter
        del application.state.policy_vector_index


def create_app(
    *,
    enable_lifespan: bool = True,
    http_metrics_max_route_keys: int = 64,
) -> FastAPI:
    """创建 FastAPI 应用。"""

    http_metrics = HttpMetricsRegistry(
        max_route_keys=http_metrics_max_route_keys,
    )
    prompt_guard = PromptInjectionGuard()
    application = FastAPI(
        title="Enterprise Policy Agent",
        version="0.1.0",
        lifespan=(_lifespan if enable_lifespan else None),
    )
    application.state.http_metrics = http_metrics
    application.state.prompt_security_guard = prompt_guard
    application.add_middleware(
        RuntimeObservabilityMiddleware,
        metrics=http_metrics,
    )
    application.add_exception_handler(
        ProviderCapacityError,
        provider_capacity_error_response,
    )
    application.add_exception_handler(
        PromptInjectionBlockedError,
        prompt_injection_blocked_response,
    )
    application.add_exception_handler(
        Exception,
        unhandled_application_error_response,
    )
    application.include_router(
        health_router,
    )
    application.include_router(
        policy_answers_router,
        prefix="/api/v1",
    )
    application.include_router(
        agent_messages_router,
        prefix="/api/v1",
    )
    application.include_router(
        agent_sessions_router,
        prefix="/api/v1",
    )
    application.include_router(
        research_answers_router,
        prefix="/api/v1",
    )
    application.include_router(
        cache_status_router,
        prefix="/api/v1",
    )
    application.include_router(
        provider_status_router,
        prefix="/api/v1",
    )
    application.include_router(
        observability_router,
        prefix="/api/v1",
    )
    application.include_router(
        security_router,
        prefix="/api/v1",
    )
    application.include_router(metrics_router)

    return application


app = create_app()
