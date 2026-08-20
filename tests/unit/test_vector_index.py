import pytest

from app.rag.vector_index import (
    InMemoryVectorIndex,
    VectorRecord,
)


def make_records() -> list[VectorRecord]:
    return [
        VectorRecord(
            record_id="travel",
            text="员工出差住宿费应当凭发票报销。",
            vector=[1.0, 0.0, 0.0],
            metadata={"policy_name": "差旅报销制度"},
        ),
        VectorRecord(
            record_id="purchase",
            text="采购金额达到标准时需要提交采购申请。",
            vector=[0.0, 1.0, 0.0],
            metadata={"policy_name": "采购管理办法"},
        ),
        VectorRecord(
            record_id="security",
            text="员工不得向他人泄露账号和密码。",
            vector=[0.0, 0.0, 1.0],
            metadata={"policy_name": "信息安全制度"},
        ),
    ]


def test_index_reports_dimension_and_initial_size() -> None:
    index = InMemoryVectorIndex(dimension=3)

    assert index.dimension == 3
    assert index.size == 0


def test_add_records_and_search_by_cosine_similarity() -> None:
    index = InMemoryVectorIndex(dimension=3)
    index.add(make_records())

    results = index.search([1.0, 0.0, 0.0])

    assert index.size == 3
    assert results[0].record.record_id == "travel"
    assert results[0].score == pytest.approx(1.0)
    assert results[0].record.metadata["policy_name"] == "差旅报销制度"


def test_search_respects_top_k() -> None:
    index = InMemoryVectorIndex(dimension=3)
    index.add(make_records())

    results = index.search(
        [1.0, 0.0, 0.0],
        top_k=2,
    )

    assert len(results) == 2


def test_search_returns_empty_list_when_index_is_empty() -> None:
    index = InMemoryVectorIndex(dimension=3)

    assert index.search([1.0, 0.0, 0.0]) == []


def test_add_rejects_vector_with_wrong_dimension() -> None:
    index = InMemoryVectorIndex(dimension=3)
    record = VectorRecord(
        record_id="invalid",
        text="错误维度",
        vector=[1.0, 0.0],
    )

    with pytest.raises(ValueError, match="vector dimension mismatch"):
        index.add([record])


def test_search_rejects_query_with_wrong_dimension() -> None:
    index = InMemoryVectorIndex(dimension=3)

    with pytest.raises(ValueError, match="vector dimension mismatch"):
        index.search([1.0, 0.0])


def test_index_rejects_zero_vectors() -> None:
    index = InMemoryVectorIndex(dimension=3)
    record = VectorRecord(
        record_id="zero",
        text="零向量",
        vector=[0.0, 0.0, 0.0],
    )

    with pytest.raises(ValueError, match="zero vectors"):
        index.add([record])


def test_index_rejects_duplicate_record_ids() -> None:
    index = InMemoryVectorIndex(dimension=3)
    record = make_records()[0]

    index.add([record])

    with pytest.raises(ValueError, match="already exists"):
        index.add([record])


def test_search_rejects_non_positive_top_k() -> None:
    index = InMemoryVectorIndex(dimension=3)

    with pytest.raises(ValueError, match="top_k"):
        index.search(
            [1.0, 0.0, 0.0],
            top_k=0,
        )
