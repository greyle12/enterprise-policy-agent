from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.verify_multi_instance_state_inventory import (
    StateInventoryError,
    main,
    validate_state_inventory,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_INVENTORY_PATH = _PROJECT_ROOT / "docs" / "multi_instance_state_inventory.json"


def _inventory_payload() -> dict[str, object]:
    return json.loads(_INVENTORY_PATH.read_text(encoding="utf-8"))


def _asset(payload: dict[str, object], identifier: str) -> dict[str, object]:
    assets = payload["state_assets"]
    assert isinstance(assets, list)
    for asset in assets:
        assert isinstance(asset, dict)
        if asset.get("id") == identifier:
            return asset
    raise AssertionError(f"asset not found: {identifier}")


def _write_inventory(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "inventory.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def test_phase38_state_inventory_covers_current_source_tree() -> None:
    report = validate_state_inventory(_PROJECT_ROOT)

    assert report["passed"] is True
    assert report["phase"] == 38
    assert report["step"] == 1
    assert report["status"] == "inventory_only"
    assert len(report["sqlite_tables"]) == 8
    assert report["current_backend_counts"]["sqlite"] == 6
    assert all(report["checks"].values())


def test_cli_prints_a_successful_inventory_report(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--project-root", str(_PROJECT_ROOT)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"passed": true' in output
    assert '"runtime_migration_has_not_started": true' in output
    assert '"workflow_checkpoints"' in output


def test_rejects_an_unaccounted_sqlite_table(tmp_path: Path) -> None:
    payload = _inventory_payload()
    checkpoint_asset = _asset(payload, "workflow_checkpoints")
    tables = checkpoint_asset["tables"]
    assert isinstance(tables, list)
    tables.remove("langgraph_writes")
    inventory = _write_inventory(tmp_path, payload)

    with pytest.raises(StateInventoryError, match="SQLite table coverage mismatch"):
        validate_state_inventory(_PROJECT_ROOT, inventory_path=inventory)


def test_rejects_durable_state_targeting_redis(tmp_path: Path) -> None:
    payload = _inventory_payload()
    _asset(payload, "application_draft_snapshots")["target_backend"] = "redis"
    inventory = _write_inventory(tmp_path, payload)

    with pytest.raises(
        StateInventoryError,
        match="durable state asset application_draft_snapshots must target PostgreSQL",
    ):
        validate_state_inventory(_PROJECT_ROOT, inventory_path=inventory)


def test_rejects_inventory_evidence_not_present_in_source(tmp_path: Path) -> None:
    payload = _inventory_payload()
    _asset(payload, "conversation_memory")["current_symbols"] = [
        "SQLiteConversationMemoryStore",
        "symbol_that_does_not_exist",
    ]
    inventory = _write_inventory(tmp_path, payload)

    with pytest.raises(StateInventoryError, match="symbols not found"):
        validate_state_inventory(_PROJECT_ROOT, inventory_path=inventory)
