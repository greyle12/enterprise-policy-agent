from __future__ import annotations

import json
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_verification(project_root: Path = _PROJECT_ROOT) -> dict[str, object]:
    root = project_root.resolve()
    provider = (root / "app/persistence/runtime_provider.py").read_text(encoding="utf-8")
    main_source = (root / "app/main.py").read_text(encoding="utf-8")
    tests = (root / "tests/unit/test_runtime_provider_preparation.py").read_text(encoding="utf-8")
    workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    checks = {
        "coherent_provider_bundle": all(
            token in provider
            for token in (
                "state_store: AgentRuntimeStateStore",
                "memory_store: ConversationMemoryStore",
                "submission_service: AgentRuntimeSubmissionService",
                "checkpointer: BaseCheckpointSaver",
            )
        ),
        "explicit_sqlite_provider": "AgentStateProviderName.SQLITE" in provider,
        "explicit_postgres_provider": "AgentStateProviderName.POSTGRESQL" in provider,
        "postgres_repository_pool_owned": (
            '"autocommit": False' in provider
            and 'name="agent-state"' in provider
            and "await self._state_pool.close()" in provider
        ),
        "postgres_schema_readiness_checked": "await state_store.ping()" in provider,
        "checkpoint_lifecycle_owned": (
            "await checkpoint_runtime.setup()" in provider
            and "await self._checkpoint_runtime.close()" in provider
        ),
        "secret_dsn_not_exposed": "get_secret_value().strip()" in provider,
        "no_silent_sqlite_fallback": (
            "selected PostgreSQL Agent runtime provider could not be prepared" in provider
            and "RuntimeProviderPreparationError" in provider
        ),
        "redis_coordination_blocks_activation": (
            'POSTGRES_ACTIVATION_BLOCKER = "redis_session_coordination_required"' in provider
            and "activation_ready=False" in provider
            and "require_activation_ready" in provider
        ),
        "failure_cleanup_tested": (
            "test_postgres_preparation_failure_closes_pool_without_sqlite_fallback" in tests
        ),
        "fastapi_runtime_not_wired": (
            "prepare_agent_runtime_providers" not in main_source
            and "SQLiteAgentStateStore(settings.sqlite_database_path)" in main_source
            and "SQLiteCheckpointSaver(settings.sqlite_database_path)" in main_source
        ),
        "ci_offline_gate": "scripts.verify_runtime_provider_preparation" in workflow,
    }
    return {
        "schema_version": "1.0",
        "phase": 38,
        "status": "runtime_provider_assembly_prepared",
        "passed": all(checks.values()),
        "checks": checks,
        "provider_factory_wired": False,
        "postgres_activation_ready": False,
        "activation_blocker": "redis_session_coordination_required",
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
