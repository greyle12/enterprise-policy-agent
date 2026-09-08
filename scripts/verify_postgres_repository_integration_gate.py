from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts.verify_ci_configuration import validate_ci_configuration

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_verification(project_root: Path = _PROJECT_ROOT) -> dict[str, object]:
    root = project_root.resolve()
    integration = (root / "tests/integration/test_postgres_repositories.py").read_text(
        encoding="utf-8"
    )
    integration_conftest = (root / "tests/integration/conftest.py").read_text(encoding="utf-8")
    compose_text = (root / "compose.yaml").read_text(encoding="utf-8")
    workflow_text = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    main_text = (root / "app/main.py").read_text(encoding="utf-8")
    compose = yaml.safe_load(compose_text)
    postgres_test = compose["services"]["postgres-test"]
    ci_report = validate_ci_configuration(root)

    checks = {
        "isolated_test_database_guard": 'database.endswith("_test")' in integration,
        "real_async_connection_pool": "AsyncConnectionPool" in integration,
        "windows_psycopg_selector_loop": (
            "pytest_asyncio_loop_factories" in integration_conftest
            and "asyncio.SelectorEventLoop" in integration_conftest
        ),
        "versioned_schema_setup": "PostgresAgentStateSchemaManager.from_dsn" in integration,
        "pool_restart_recovery": "replacement_pool" in integration,
        "session_cas_concurrency": "test_concurrent_session_heads_allow_one_winner" in integration,
        "conversation_turn_concurrency": (
            "test_concurrent_conversation_appends_allocate_complete_ordered_turns" in integration
        ),
        "same_key_idempotency_concurrency": (
            "test_concurrent_same_key_submission_has_one_receipt_and_replay_audits" in integration
        ),
        "different_key_conflict_concurrency": (
            "test_concurrent_different_keys_for_one_draft_fail_closed" in integration
        ),
        "compose_integration_profile": "integration" in postgres_test.get("profiles", []),
        "compose_ephemeral_test_storage": "tmpfs" in postgres_test,
        "ci_real_postgres_service": "postgres-repositories" in ci_report.jobs,
        "ci_machine_readable_evidence": "postgres-repositories.xml" in workflow_text,
        "runtime_remains_sqlite": "SQLiteAgentStateStore" in main_text,
        "checkpoint_remains_sqlite": "SQLiteCheckpointSaver" in main_text,
    }
    return {
        "schema_version": "1.0",
        "phase": 38,
        "step": 3,
        "substep": 4,
        "status": "integration_gate_ready",
        "passed": all(checks.values()),
        "checks": checks,
        "real_database_execution_required": True,
        "real_database_execution_deferred_to_ci": True,
        "runtime_backend_switched": False,
        "sqlite_data_migrated": False,
        "langgraph_checkpoint_backend_switched": False,
    }


def main() -> int:
    result = run_verification()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
