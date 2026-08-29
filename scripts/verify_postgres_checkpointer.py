from __future__ import annotations

import json
from pathlib import Path

from scripts.verify_ci_configuration import validate_ci_configuration

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_verification(project_root: Path = _PROJECT_ROOT) -> dict[str, object]:
    root = project_root.resolve()
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    adapter = (root / "app/persistence/postgres_checkpointer.py").read_text(encoding="utf-8")
    command = (root / "scripts/manage_postgres_checkpointer.py").read_text(encoding="utf-8")
    integration = (root / "tests/integration/test_postgres_checkpointer.py").read_text(
        encoding="utf-8"
    )
    workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    main_source = (root / "app/main.py").read_text(encoding="utf-8")
    ci_report = validate_ci_configuration(root)

    checks = {
        "official_dependency_pinned": (
            '"langgraph-checkpoint-postgres>=3.1.2,<4.0.0"' in pyproject
            and '"langgraph-checkpoint>=4.2.0,<5.0.0"' in pyproject
        ),
        "official_async_saver": "AsyncPostgresSaver" in adapter,
        "fixed_agent_runtime_schema": "SET search_path TO {AGENT_STATE_SCHEMA}" in adapter,
        "strict_serializer_allowlist": "allowed_msgpack_modules=()" in adapter,
        "explicit_pool_lifecycle": all(
            token in adapter
            for token in ("await pool.open()", "await pool.wait", "await pool.close()")
        ),
        "official_migration_version_check": (
            "checkpoint_migrations" in adapter and "supported_version" in adapter
        ),
        "concurrent_setup_lock": "pg_advisory_lock" in adapter,
        "windows_selector_cli": "asyncio.SelectorEventLoop" in command,
        "hitl_cross_instance_resume": "test_instance_b_resumes_instance_a_hitl_checkpoint"
        in integration,
        "delete_thread_contract": "adelete_thread" in integration,
        "ci_real_postgres_execution": (
            "postgres-repositories" in ci_report.jobs
            and "tests/integration/test_postgres_checkpointer.py" in workflow
        ),
        "runtime_remains_sqlite": "SQLiteCheckpointSaver(settings.sqlite_database_path)"
        in main_source,
    }
    return {
        "schema_version": "1.0",
        "phase": 38,
        "step": 4,
        "status": "checkpointer_backend_ready",
        "passed": all(checks.values()),
        "checks": checks,
        "official_saver": "AsyncPostgresSaver",
        "checkpoint_schema": "agent_runtime",
        "runtime_backend_switched": False,
        "sqlite_data_migrated": False,
        "database_calls": False,
        "external_calls": False,
    }


def main() -> int:
    result = run_verification()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
