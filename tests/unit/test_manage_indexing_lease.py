from __future__ import annotations

from types import SimpleNamespace

from scripts import manage_indexing_lease


class _Manager:
    def __init__(self, status) -> None:
        self._status = status
        self.initialized = False
        self.closed = False

    def initialize_schema(self) -> None:
        self.initialized = True

    def status(self, collection_name: str):
        assert collection_name == "policy-green"
        return self._status

    def close(self) -> None:
        self.closed = True


def test_status_outputs_owner_and_fence_without_lease_token(monkeypatch, capsys) -> None:
    manager = _Manager(
        SimpleNamespace(
            owner_id="builder-a",
            fencing_token=4,
            expires_at="later",
            active=True,
        )
    )
    monkeypatch.setattr(
        manage_indexing_lease.PgVectorIndexingLeaseManager,
        "from_dsn",
        lambda *args, **kwargs: manager,
    )

    exit_code = manage_indexing_lease.main(["status", "--collection", "policy-green"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"phase": 36' in output
    assert '"owner_id": "builder-a"' in output
    assert '"fencing_token": 4' in output
    assert "lease_token" not in output
    assert manager.initialized is True
    assert manager.closed is True
