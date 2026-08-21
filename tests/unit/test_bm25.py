from __future__ import annotations

import pytest

from app.rag.bm25 import BM25Record, InMemoryBM25Index, PolicyKeywordTokenizer


def _records() -> list[BM25Record]:
    return [
        BM25Record(
            record_id="travel",
            text="差旅报销制度 第八条 住宿费必须提供住宿发票。",
            metadata={"document_id": "TRAVEL_POLICY_001"},
        ),
        BM25Record(
            record_id="purchase",
            text="采购管理办法 第五条 采购申请单编号 PROC-2026-001。",
            metadata={"document_id": "PROCUREMENT_POLICY_001"},
        ),
        BM25Record(
            record_id="security",
            text="信息安全制度 禁止向公共 AI 平台上传客户资料。",
            metadata={"document_id": "INFORMATION_SECURITY_POLICY_001"},
        ),
    ]


def test_policy_tokenizer_normalizes_unicode_case_identifiers_and_cjk_bigrams() -> None:
    tokens = PolicyKeywordTokenizer().tokenize("ＡＢＣ-００１ 住宿费 AI")

    assert "abc-001" in tokens
    assert "住宿费" in tokens
    assert "住宿" in tokens
    assert "宿费" in tokens
    assert "ai" in tokens


def test_bm25_ranks_exact_policy_terms_and_preserves_metadata() -> None:
    index = InMemoryBM25Index()
    index.add(_records())

    results = index.search("住宿费 发票", top_k=2)

    assert results[0].record.record_id == "travel"
    assert results[0].score > 0.0
    assert results[0].record.metadata["document_id"] == "TRAVEL_POLICY_001"


def test_bm25_matches_enterprise_identifier_case_insensitively() -> None:
    index = InMemoryBM25Index()
    index.add(_records())

    results = index.search("proc-2026-001")

    assert [result.record.record_id for result in results] == ["purchase"]


def test_bm25_returns_only_positive_matches_and_respects_top_k() -> None:
    index = InMemoryBM25Index()
    index.add(_records())

    assert index.search("不存在的词语") == []
    assert len(index.search("制度", top_k=1)) == 1


def test_authorization_scope_precedes_bm25_statistics_and_scoring() -> None:
    authorized = BM25Record(record_id="authorized", text="差旅 住宿 发票")
    unauthorized = BM25Record(record_id="unauthorized", text="差旅 差旅 差旅 核心机密")
    scoped_index = InMemoryBM25Index()
    scoped_index.add([authorized])
    full_index = InMemoryBM25Index()
    full_index.add([authorized, unauthorized])

    expected = scoped_index.search("差旅", allowed_record_ids={"authorized"})
    actual = full_index.search("差旅", allowed_record_ids={"authorized"})

    assert [result.record.record_id for result in actual] == ["authorized"]
    assert actual[0].score == pytest.approx(expected[0].score)
    assert full_index.search("核心机密", allowed_record_ids={"authorized"}) == []


def test_bm25_ties_are_deterministic_by_record_id() -> None:
    index = InMemoryBM25Index()
    index.add(
        [
            BM25Record(record_id="b", text="相同关键词"),
            BM25Record(record_id="a", text="相同关键词"),
        ]
    )

    results = index.search("相同关键词")

    assert [result.record.record_id for result in results] == ["a", "b"]


@pytest.mark.parametrize("query", ["", "   ", "!!!"])
def test_bm25_rejects_blank_or_unsearchable_query(query: str) -> None:
    index = InMemoryBM25Index()
    index.add(_records())

    with pytest.raises(ValueError, match="query"):
        index.search(query)


def test_bm25_validates_parameters_records_and_limits() -> None:
    with pytest.raises(ValueError, match="k1"):
        InMemoryBM25Index(k1=-1)
    with pytest.raises(ValueError, match="b"):
        InMemoryBM25Index(b=1.1)

    index = InMemoryBM25Index(max_document_tokens=1)
    with pytest.raises(ValueError, match="token limit"):
        index.add([BM25Record(record_id="too-long", text="采购 申请")])

    duplicate_index = InMemoryBM25Index()
    duplicate_index.add([BM25Record(record_id="same", text="采购")])
    with pytest.raises(ValueError, match="already exists"):
        duplicate_index.add([BM25Record(record_id="same", text="申请")])
