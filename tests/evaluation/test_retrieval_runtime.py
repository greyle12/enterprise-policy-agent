from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.evaluation.retrieval_models import RetrievalCase
from app.evaluation.retrieval_runtime import (
    RetrievalJudgmentError,
    corpus_sha256,
    validate_retrieval_judgments,
)
from app.rag.policy_chunker import chunk_policy_directory
from app.schemas.policy import SecurityLevel
from app.security import PolicyAccessContext, TrustedIdentitySource

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _context(clearance: SecurityLevel = SecurityLevel.INTERNAL) -> PolicyAccessContext:
    return PolicyAccessContext(
        employee_id="EVAL-001",
        department="评测部门",
        roles=("EMPLOYEE",),
        security_clearance=clearance,
        region="中国大陆",
        identity_source=TrustedIdentitySource.TEST_FIXTURE,
    )


def _case(chunk_id: str) -> RetrievalCase:
    return RetrievalCase(
        case_id="RET-001",
        title="judgment",
        query="find the chunk",
        relevant_chunk_ids=(chunk_id,),
    )


def test_corpus_digest_is_independent_of_input_order() -> None:
    chunks = chunk_policy_directory(_PROJECT_ROOT / "data" / "policies")[:3]

    assert corpus_sha256(chunks) == corpus_sha256(tuple(reversed(chunks)))


def test_judgments_must_reference_existing_chunks() -> None:
    chunks = chunk_policy_directory(_PROJECT_ROOT / "data" / "policies")

    with pytest.raises(RetrievalJudgmentError, match="missing chunks"):
        validate_retrieval_judgments(
            [_case("missing-chunk")],
            chunks,
            access_context=_context(),
            as_of_date=date(2026, 8, 20),
        )


def test_judgments_cannot_label_an_unauthorized_chunk_as_relevant() -> None:
    base = chunk_policy_directory(_PROJECT_ROOT / "data" / "policies")[0]
    restricted = base.model_copy(
        update={"chunk_id": "core-only", "security_level": SecurityLevel.CORE}
    )

    with pytest.raises(RetrievalJudgmentError, match="unauthorized chunks"):
        validate_retrieval_judgments(
            [_case("core-only")],
            [restricted],
            access_context=_context(),
            as_of_date=date(2026, 8, 20),
        )
