from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_INVENTORY_RELATIVE_PATH = Path("docs/multi_instance_state_inventory.json")
_SQLITE_SCHEMA_RELATIVE_PATH = Path("app/persistence/sqlite_schema.py")
_SQLITE_TABLE_PATTERN = re.compile(
    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_REQUIRED_STATE_ASSETS = {
    "agent_session_projection",
    "application_draft_snapshots",
    "approval_submission_receipts",
    "conversation_memory",
    "llm_response_cache",
    "rag_vector_control_plane",
    "submission_audit_log",
    "workflow_checkpoints",
}
_REQUIRED_SINGLE_INSTANCE_DEPENDENCIES = {
    "in_memory_submission_fallback",
    "in_memory_vector_provider",
    "llm_singleflight_registry",
    "provider_capacity_queue",
    "runtime_metrics_registry",
    "single_agent_compose_topology",
    "sqlite_runtime_file",
    "workflow_session_lock",
}
_ALLOWED_DURABILITY = {"durable", "ephemeral", "reconstructable"}
_ALLOWED_BACKENDS = {"filesystem", "postgresql", "redis", "sqlite"}
_ALLOWED_ACTIONS = {
    "defer",
    "migrate",
    "require_shared_backend",
    "restrict_to_tests",
    "retain",
}
_ALLOWED_IMPACTS = {"availability", "continuity", "correctness", "observability", "performance"}


class StateInventoryError(ValueError):
    """Raised when the checked-in Phase 38 state inventory is incomplete or unsafe."""


def _mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StateInventoryError(f"{label} must be a JSON object")
    return value


def _sequence(value: Any, *, label: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise StateInventoryError(f"{label} must be a JSON array")
    return value


def _required_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StateInventoryError(f"{label} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, *, label: str) -> tuple[str, ...]:
    items = tuple(
        _required_text(item, label=f"{label} item") for item in _sequence(value, label=label)
    )
    if len(items) != len(set(items)):
        raise StateInventoryError(f"{label} must not contain duplicates")
    return items


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StateInventoryError(f"cannot read {path}: {exc}") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StateInventoryError(f"invalid JSON in {path}: {exc}") from exc
    return _mapping(value, label=str(path))


def _read_source(project_root: Path, relative_path: str) -> str:
    path = project_root / relative_path
    if not path.is_file():
        raise StateInventoryError(f"inventory source file does not exist: {relative_path}")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StateInventoryError(
            f"cannot read inventory source file {relative_path}: {exc}"
        ) from exc


def _validate_source_evidence(
    project_root: Path,
    *,
    source_files: tuple[str, ...],
    symbols: tuple[str, ...],
    label: str,
) -> None:
    source_text = "\n".join(_read_source(project_root, path) for path in source_files)
    missing_symbols = sorted(symbol for symbol in symbols if symbol not in source_text)
    if missing_symbols:
        raise StateInventoryError(
            f"{label} references symbols not found in its source files: "
            + ", ".join(missing_symbols)
        )


def _planned_step(value: Any, *, label: str, required: bool) -> int | None:
    if value is None:
        if required:
            raise StateInventoryError(f"{label} must identify a Phase 38 step")
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 2 <= value <= 8:
        raise StateInventoryError(f"{label} must be null or an integer from 2 through 8")
    return value


def _validate_state_assets(
    project_root: Path,
    value: Any,
) -> tuple[tuple[Mapping[str, Any], ...], set[str]]:
    assets = tuple(
        _mapping(item, label="state_assets item") for item in _sequence(value, label="state_assets")
    )
    if not assets:
        raise StateInventoryError("state_assets must not be empty")

    identifiers: set[str] = set()
    sqlite_tables: set[str] = set()
    for index, asset in enumerate(assets):
        label = f"state_assets[{index}]"
        identifier = _required_text(asset.get("id"), label=f"{label}.id")
        if identifier in identifiers:
            raise StateInventoryError(f"duplicate state asset id: {identifier}")
        identifiers.add(identifier)

        durability = _required_text(asset.get("durability"), label=f"{label}.durability")
        if durability not in _ALLOWED_DURABILITY:
            raise StateInventoryError(f"{label}.durability is unsupported: {durability}")
        _required_text(asset.get("role"), label=f"{label}.role")

        current_backend = _required_text(
            asset.get("current_backend"), label=f"{label}.current_backend"
        )
        target_backend = _required_text(
            asset.get("target_backend"), label=f"{label}.target_backend"
        )
        if current_backend not in _ALLOWED_BACKENDS or target_backend not in _ALLOWED_BACKENDS:
            raise StateInventoryError(f"{label} contains an unsupported backend")

        action = _required_text(asset.get("phase38_action"), label=f"{label}.phase38_action")
        if action not in _ALLOWED_ACTIONS:
            raise StateInventoryError(f"{label}.phase38_action is unsupported: {action}")
        _planned_step(
            asset.get("planned_step"),
            label=f"{label}.planned_step",
            required=action in {"migrate", "require_shared_backend", "restrict_to_tests"},
        )

        tables = _string_list(asset.get("tables"), label=f"{label}.tables")
        source_files = _string_list(asset.get("source_files"), label=f"{label}.source_files")
        symbols = _string_list(asset.get("current_symbols"), label=f"{label}.current_symbols")
        if not source_files or not symbols:
            raise StateInventoryError(f"{label} must include source files and current symbols")
        _validate_source_evidence(
            project_root,
            source_files=source_files,
            symbols=symbols,
            label=label,
        )
        _required_text(asset.get("multi_instance_risk"), label=f"{label}.multi_instance_risk")
        _required_text(asset.get("required_guarantee"), label=f"{label}.required_guarantee")

        if durability == "durable" and target_backend != "postgresql":
            raise StateInventoryError(
                f"durable state asset {identifier} must target PostgreSQL, not {target_backend}"
            )
        if durability == "ephemeral" and target_backend != "redis":
            raise StateInventoryError(
                f"ephemeral shared state asset {identifier} must target Redis, not {target_backend}"
            )
        if current_backend == "sqlite":
            if durability != "durable" or target_backend != "postgresql" or action != "migrate":
                raise StateInventoryError(
                    f"SQLite state asset {identifier} must be durable and migrate to PostgreSQL"
                )
            sqlite_tables.update(tables)

    missing_assets = sorted(_REQUIRED_STATE_ASSETS.difference(identifiers))
    if missing_assets:
        raise StateInventoryError(
            "state inventory is missing required assets: " + ", ".join(missing_assets)
        )
    return assets, sqlite_tables


def _validate_sqlite_table_coverage(project_root: Path, inventoried_tables: set[str]) -> set[str]:
    schema_path = project_root / _SQLITE_SCHEMA_RELATIVE_PATH
    schema = _read_source(project_root, str(_SQLITE_SCHEMA_RELATIVE_PATH))
    actual_tables = set(_SQLITE_TABLE_PATTERN.findall(schema))
    if not actual_tables:
        raise StateInventoryError(f"no SQLite tables were found in {schema_path}")
    if inventoried_tables != actual_tables:
        missing = sorted(actual_tables.difference(inventoried_tables))
        unknown = sorted(inventoried_tables.difference(actual_tables))
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unknown:
            details.append("unknown=" + ",".join(unknown))
        raise StateInventoryError("SQLite table coverage mismatch: " + "; ".join(details))
    return actual_tables


def _validate_single_instance_dependencies(project_root: Path, value: Any) -> int:
    dependencies = tuple(
        _mapping(item, label="single_instance_dependencies item")
        for item in _sequence(value, label="single_instance_dependencies")
    )
    identifiers: set[str] = set()
    for index, dependency in enumerate(dependencies):
        label = f"single_instance_dependencies[{index}]"
        identifier = _required_text(dependency.get("id"), label=f"{label}.id")
        if identifier in identifiers:
            raise StateInventoryError(f"duplicate single-instance dependency id: {identifier}")
        identifiers.add(identifier)

        source_file = _required_text(dependency.get("source_file"), label=f"{label}.source_file")
        symbol = _required_text(dependency.get("current_symbol"), label=f"{label}.current_symbol")
        _validate_source_evidence(
            project_root,
            source_files=(source_file,),
            symbols=(symbol,),
            label=label,
        )
        _required_text(dependency.get("current_scope"), label=f"{label}.current_scope")
        impact = _required_text(dependency.get("impact"), label=f"{label}.impact")
        if impact not in _ALLOWED_IMPACTS:
            raise StateInventoryError(f"{label}.impact is unsupported: {impact}")
        action = _required_text(dependency.get("phase38_action"), label=f"{label}.phase38_action")
        if action not in _ALLOWED_ACTIONS:
            raise StateInventoryError(f"{label}.phase38_action is unsupported: {action}")
        _required_text(dependency.get("target"), label=f"{label}.target")
        resolution_phase = dependency.get("resolution_phase")
        if isinstance(resolution_phase, bool) or not isinstance(resolution_phase, int):
            raise StateInventoryError(f"{label}.resolution_phase must be an integer")
        if resolution_phase == 38:
            if action == "defer":
                raise StateInventoryError(f"Phase 38 dependency {identifier} cannot be deferred")
            _planned_step(
                dependency.get("planned_step"),
                label=f"{label}.planned_step",
                required=True,
            )
        else:
            if resolution_phase not in {40, 43} or action != "defer":
                raise StateInventoryError(
                    f"deferred dependency {identifier} must belong to Phase 40 or 43"
                )
            _planned_step(
                dependency.get("planned_step"),
                label=f"{label}.planned_step",
                required=False,
            )

    missing = sorted(_REQUIRED_SINGLE_INSTANCE_DEPENDENCIES.difference(identifiers))
    if missing:
        raise StateInventoryError(
            "state inventory is missing single-instance dependencies: " + ", ".join(missing)
        )
    return len(dependencies)


def validate_state_inventory(
    project_root: Path,
    *,
    inventory_path: Path | None = None,
) -> dict[str, object]:
    """Validate the Phase 38 Step 1 inventory against the current source tree."""

    root = project_root.resolve()
    selected_inventory = (
        inventory_path.resolve() if inventory_path is not None else root / _INVENTORY_RELATIVE_PATH
    )
    inventory = _read_json(selected_inventory)
    if inventory.get("schema_version") != "1.0":
        raise StateInventoryError("schema_version must be 1.0")
    if inventory.get("phase") != 38 or inventory.get("step") != 1:
        raise StateInventoryError("inventory must describe Phase 38 Step 1")
    if inventory.get("status") != "inventory_only":
        raise StateInventoryError("Step 1 status must remain inventory_only")
    if inventory.get("runtime_migration_applied") is not False:
        raise StateInventoryError("Step 1 must not claim that runtime migration was applied")
    _required_text(inventory.get("audited_branch"), label="audited_branch")
    _required_text(inventory.get("audited_commit"), label="audited_commit")

    backend_policy = _mapping(inventory.get("backend_policy"), label="backend_policy")
    expected_policy = {
        "durable": "postgresql",
        "ephemeral_shared": "redis",
        "process_local": "reconstructable_or_telemetry_only",
    }
    if dict(backend_policy) != expected_policy:
        raise StateInventoryError(
            "backend_policy does not match the Phase 38 architecture decision"
        )

    assets, inventoried_sqlite_tables = _validate_state_assets(
        root,
        inventory.get("state_assets"),
    )
    actual_sqlite_tables = _validate_sqlite_table_coverage(root, inventoried_sqlite_tables)
    dependency_count = _validate_single_instance_dependencies(
        root,
        inventory.get("single_instance_dependencies"),
    )
    non_goals = _string_list(inventory.get("step_1_non_goals"), label="step_1_non_goals")
    if len(non_goals) < 5:
        raise StateInventoryError(
            "step_1_non_goals must make the no-runtime-change boundary explicit"
        )

    backend_counts = Counter(
        _required_text(asset.get("current_backend"), label="state asset current_backend")
        for asset in assets
    )
    pending_assets = sorted(
        _required_text(asset.get("id"), label="state asset id")
        for asset in assets
        if asset.get("phase38_action") == "migrate"
    )
    checks = {
        "inventory_is_step_1_only": True,
        "all_sqlite_tables_are_accounted_for": True,
        "durable_state_targets_postgresql": True,
        "ephemeral_shared_state_targets_redis": True,
        "source_files_and_symbols_exist": True,
        "single_instance_dependencies_have_explicit_owners": True,
        "runtime_migration_has_not_started": True,
    }
    return {
        "schema_version": "1.0",
        "phase": 38,
        "step": 1,
        "status": "inventory_only",
        "passed": all(checks.values()),
        "audited_branch": inventory["audited_branch"],
        "audited_commit": inventory["audited_commit"],
        "state_asset_count": len(assets),
        "single_instance_dependency_count": dependency_count,
        "current_backend_counts": dict(sorted(backend_counts.items())),
        "sqlite_tables": sorted(actual_sqlite_tables),
        "pending_phase38_assets": pending_assets,
        "checks": checks,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify the Phase 38 Step 1 multi-instance state inventory."
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=_PROJECT_ROOT,
        help="Repository root containing app/, docs/, and scripts/.",
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=None,
        help="Optional inventory JSON path used by tests or local review.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = validate_state_inventory(
            args.project_root,
            inventory_path=args.inventory,
        )
    except StateInventoryError as exc:
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "phase": 38,
                    "step": 1,
                    "passed": False,
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
