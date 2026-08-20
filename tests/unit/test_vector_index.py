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


def test_upsert_replaces_existing_record() -> None:
    index = InMemoryVectorIndex(dimension=3)
    index.upsert([make_records()[0]])
    index.upsert(
        [
            VectorRecord(
                record_id="travel",
                text="更新后的差旅规则",
                vector=[1.0, 0.0, 0.0],
            )
        ]
    )

    assert index.size == 1
    assert index.search([1.0, 0.0, 0.0])[0].record.text == "更新后的差旅规则"


def test_lists_entries_without_exposing_vector_payloads() -> None:
    index = InMemoryVectorIndex(dimension=3)
    index.upsert(make_records())

    entries = index.list_entries()

    assert [entry.record_id for entry in entries] == ["purchase", "security", "travel"]
    assert entries[2].metadata == {"policy_name": "差旅报销制度"}


def test_apply_changes_upserts_and_deletes_as_one_validated_snapshot() -> None:
    index = InMemoryVectorIndex(dimension=3)
    index.upsert(make_records())

    index.apply_changes(
        [
            VectorRecord(
                record_id="travel",
                text="更新后的差旅规则",
                vector=[1.0, 0.0, 0.0],
            )
        ],
        delete_record_ids={"purchase"},
    )

    assert [entry.record_id for entry in index.list_entries()] == ["security", "travel"]
    assert index.search([1.0, 0.0, 0.0], top_k=1)[0].record.text == "更新后的差旅规则"


def test_apply_changes_rejects_overlapping_upsert_and_delete_without_mutation() -> None:
    index = InMemoryVectorIndex(dimension=3)
    index.upsert(make_records())

    with pytest.raises(ValueError, match="upserted and deleted"):
        index.apply_changes(
            [make_records()[0]],
            delete_record_ids={"travel"},
        )

    assert index.size == 3


def test_index_rejects_non_finite_vectors() -> None:
    index = InMemoryVectorIndex(dimension=3)

    with pytest.raises(ValueError, match="finite"):
        index.upsert(
            [
                VectorRecord(
                    record_id="invalid",
                    text="非法向量",
                    vector=[float("inf"), 0.0, 0.0],
                )
            ]
        )


def test_search_rejects_non_positive_top_k() -> None:
    index = InMemoryVectorIndex(dimension=3)

    with pytest.raises(ValueError, match="top_k"):
        index.search(
            [1.0, 0.0, 0.0],
            top_k=0,
        )
