from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.rag.policy_chunker import chunk_policy_directory
from app.rag.policy_retriever import PolicyRetriever
from app.schemas.chunk import PolicyChunk
from app.schemas.policy import PolicyStatus, SecurityLevel
from app.security import (
    PolicyAccessContext,
    PolicyAccessDenialReason,
    TrustedIdentitySource,
    evaluate_policy_access,
)

POLICY_DIRECTORY = Path("data/policies")
AS_OF_DATE = date(2026, 8, 18)


class FakeEmbeddingProvider:
    @property
    def dimension(self) -> int:
        return 2

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if index == 0 else [0.0, 1.0] for index, _ in enumerate(texts)]

    def embed_query(self, text: str) -> list[float]:
        del text
        return [1.0, 0.0]


@pytest.fixture
def context() -> PolicyAccessContext:
    return PolicyAccessContext(
        employee_id="EMP-001",
        department="技术部",
        roles=("employee", "it"),
        security_clearance=SecurityLevel.INTERNAL,
        region="中国大陆",
        identity_source=TrustedIdentitySource.TEST_FIXTURE,
    )


@pytest.fixture
def chunk() -> PolicyChunk:
    return chunk_policy_directory(POLICY_DIRECTORY)[0]


def test_allows_effective_policy_inside_trusted_scope(
    chunk: PolicyChunk,
    context: PolicyAccessContext,
) -> None:
    decision = evaluate_policy_access(
        chunk,
        context,
        as_of_date=AS_OF_DATE,
    )

    assert decision.allowed is True
    assert decision.denial_reasons == ()


@pytest.mark.parametrize(
    ("update", "reason"),
    [
        (
            {"document_status": PolicyStatus.DRAFT},
            PolicyAccessDenialReason.DOCUMENT_NOT_EFFECTIVE,
        ),
        (
            {"effective_date": date(2027, 1, 1)},
            PolicyAccessDenialReason.NOT_YET_EFFECTIVE,
        ),
        (
            {"expiry_date": date(2026, 1, 1)},
            PolicyAccessDenialReason.EXPIRED,
        ),
        (
            {"security_level": SecurityLevel.CORE},
            PolicyAccessDenialReason.CLEARANCE,
        ),
        (
            {"allowed_departments": ["财务部"]},
            PolicyAccessDenialReason.DEPARTMENT,
        ),
        (
            {"allowed_roles": ["FINANCE"]},
            PolicyAccessDenialReason.ROLE,
        ),
        (
            {"region": "加拿大"},
            PolicyAccessDenialReason.REGION,
        ),
    ],
)
def test_denies_each_policy_metadata_boundary(
    chunk: PolicyChunk,
    context: PolicyAccessContext,
    update: dict[str, object],
    reason: PolicyAccessDenialReason,
) -> None:
    decision = evaluate_policy_access(
        chunk.model_copy(update=update),
        context,
        as_of_date=AS_OF_DATE,
    )

    assert decision.allowed is False
    assert reason in decision.denial_reasons


def test_restricted_retriever_filters_before_vector_scoring(
    chunk: PolicyChunk,
    context: PolicyAccessContext,
) -> None:
    unauthorized = chunk.model_copy(
        update={
            "chunk_id": "core-policy",
            "security_level": SecurityLevel.CORE,
        }
    )
    authorized = chunk.model_copy(update={"chunk_id": "internal-policy"})
    retriever = PolicyRetriever(
        embedding_provider=FakeEmbeddingProvider(),
        chunks=[unauthorized, authorized],
    )

    raw_results = retriever.search("core", top_k=2)
    restricted = retriever.restrict(
        context,
        as_of_date=AS_OF_DATE,
    )
    authorized_results = restricted.search("core", top_k=2)

    assert raw_results[0].chunk.chunk_id == "core-policy"
    assert restricted.allowed_chunk_count == 1
    assert [result.chunk.chunk_id for result in authorized_results] == ["internal-policy"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"employee_id": " "},
        {"department": " "},
        {"roles": ()},
        {"region": " "},
    ],
)
def test_rejects_incomplete_trusted_identity(kwargs: dict[str, object]) -> None:
    values = {
        "employee_id": "EMP-001",
        "department": "技术部",
        "roles": ("EMPLOYEE",),
        "security_clearance": SecurityLevel.INTERNAL,
        "region": "中国大陆",
        "identity_source": TrustedIdentitySource.TEST_FIXTURE,
    }
    values.update(kwargs)

    with pytest.raises(ValueError):
        PolicyAccessContext(**values)
