from __future__ import annotations

from app.rag.collection_gc import CollectionGCMark, CollectionGCPlanEntry
from scripts import manage_vector_collection_gc


class _Manager:
    def __init__(self) -> None:
        self.closed = False
        self.schema_calls = 0

    def initialize_schema(self) -> None:
        self.schema_calls += 1

    def plan(self, *, retention_seconds: int):
        assert retention_seconds == 7 * 86_400
        return (
            CollectionGCPlanEntry(
                collection_name="policy-retired",
                record_count=199,
                fencing_token=4,
                last_activity_at="old",
                protection_reasons=(),
            ),
            CollectionGCPlanEntry(
                collection_name="policy-blue",
                record_count=201,
                fencing_token=5,
                last_activity_at="new",
                protection_reasons=("active:enterprise-policy",),
            ),
        )

    def mark(self, **kwargs):
        assert kwargs["collection_name"] == "policy-retired"
        return _mark(swept=False)

    def status(self, collection_name: str):
        assert collection_name == "policy-retired"
        return _mark(swept=False)

    def sweep(self, **kwargs):
        assert kwargs["mark_token"] == "a" * 32
        return _mark(swept=True)

    def close(self) -> None:
        self.closed = True


def _mark(*, swept: bool) -> CollectionGCMark:
    return CollectionGCMark(
        collection_name="policy-retired",
        mark_token="a" * 32,
        fencing_token=4,
        marked_record_count=199,
        last_activity_at="old",
        retention_seconds=604_800,
        marked_at="marked",
        sweep_after="ready-at",
        swept_at="swept" if swept else None,
        deleted_record_count=199 if swept else None,
        ready=swept,
    )


def test_plan_is_explicit_dry_run_and_only_initializes_schema(
    monkeypatch,
    capsys,
) -> None:
    manager = _Manager()
    monkeypatch.setattr(
        manage_vector_collection_gc.PgVectorCollectionGCManager,
        "from_dsn",
        lambda *args, **kwargs: manager,
    )

    exit_code = manage_vector_collection_gc.main(
        [
            "--dsn",
            "postgresql://user:secret@database/policies",
            "plan",
            "--retention-days",
            "7",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"dry_run": true' in output
    assert '"eligible_collection_count": 1' in output
    assert "secret" not in output
    assert manager.schema_calls == 1
    assert manager.closed is True


def test_mark_status_and_sweep_return_machine_readable_receipts(
    monkeypatch,
    capsys,
) -> None:
    managers: list[_Manager] = []

    def build_manager(*args, **kwargs):
        manager = _Manager()
        managers.append(manager)
        return manager

    monkeypatch.setattr(
        manage_vector_collection_gc.PgVectorCollectionGCManager,
        "from_dsn",
        build_manager,
    )

    mark_exit = manage_vector_collection_gc.main(
        [
            "mark",
            "--collection",
            "policy-retired",
            "--retention-days",
            "7",
            "--sweep-grace-seconds",
            "60",
        ]
    )
    mark_output = capsys.readouterr().out
    status_exit = manage_vector_collection_gc.main(["status", "--collection", "policy-retired"])
    status_output = capsys.readouterr().out
    sweep_exit = manage_vector_collection_gc.main(
        [
            "sweep",
            "--collection",
            "policy-retired",
            "--mark-token",
            "a" * 32,
        ]
    )
    sweep_output = capsys.readouterr().out

    assert mark_exit == status_exit == sweep_exit == 0
    assert '"action": "mark"' in mark_output
    assert '"mark_token": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"' in mark_output
    assert '"action": "status"' in status_output
    assert '"action": "sweep"' in sweep_output
    assert '"deleted_record_count": 199' in sweep_output
    assert all(manager.schema_calls == 1 for manager in managers)
    assert all(manager.closed for manager in managers)
