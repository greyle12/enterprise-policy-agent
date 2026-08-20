from __future__ import annotations

import pytest

from scripts import pgvector_persistence_probe as probe_module


class _FakeIndex:
    def __init__(self) -> None:
        self.records: dict[str, object] = {}
        self.closed = False

    @property
    def size(self) -> int:
        return len(self.records)

    def upsert(self, records) -> None:
        for record in records:
            self.records[record.record_id] = record

    def search(self, query_vector, top_k=5, *, allowed_record_ids=None):
        del query_vector, top_k
        return [
            type("Result", (), {"record": record})()
            for record_id, record in self.records.items()
            if allowed_record_ids is None or record_id in allowed_record_ids
        ]

    def delete_collection(self) -> None:
        self.records.clear()

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("operation", "expected_key"),
    (("write", "written"), ("read", "persisted"), ("delete", "deleted")),
)
def test_probe_operations_close_index(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    expected_key: str,
) -> None:
    index = _FakeIndex()
    if operation == "read":
        record_id = "PHASE29-PROBE"
        index.upsert(
            [
                type(
                    "Record",
                    (),
                    {"record_id": record_id},
                )()
            ]
        )
    monkeypatch.setattr(probe_module, "_open_index", lambda probe_id: index)

    result = probe_module.run(operation, "PHASE29-PROBE")

    assert result[expected_key] is True
    assert index.closed is True


def test_probe_collection_name_does_not_expose_probe_id() -> None:
    collection = probe_module._collection_name("SECRET-PROBE-ID")

    assert collection.startswith("phase29-probe-")
    assert "SECRET" not in collection
