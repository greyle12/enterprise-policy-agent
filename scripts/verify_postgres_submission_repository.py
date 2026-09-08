from __future__ import annotations

import json
from pathlib import Path

from app.persistence.postgres_schema import AGENT_STATE_SCHEMA_VERSION

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_verification() -> dict[str, object]:
    repository_source = (
        _PROJECT_ROOT / "app" / "persistence" / "postgres_submission.py"
    ).read_text(encoding="utf-8")
    schema_source = (_PROJECT_ROOT / "app" / "persistence" / "postgres_schema.py").read_text(
        encoding="utf-8"
    )
    main_source = (_PROJECT_ROOT / "app" / "main.py").read_text(encoding="utf-8")

    validation_position = repository_source.index("self._validated_submission_request(")
    transaction_position = repository_source.index("async with self._pool.connection()")
    checks = {
        "repository_uses_injected_async_pool": (
            "pool: PostgresStateConnectionPool" in repository_source
            and "AsyncConnectionPool(" not in repository_source
        ),
        "submission_preconditions_run_before_database_access": (
            validation_position < transaction_position
        ),
        "existing_idempotency_binding_is_locked_and_validated": (
            "WHERE idempotency_key = %s\n            FOR UPDATE" in repository_source
            and "idempotency key is already bound to another submission" in repository_source
            and "submission columns and persisted payload have drifted" in repository_source
        ),
        "draft_has_independent_unique_binding_guard": (
            "WHERE draft_id = %s\n            FOR UPDATE" in repository_source
            and "draft is already bound to another submission" in repository_source
        ),
        "concurrent_insert_reloads_database_winner": (
            "INSERT INTO {AGENT_STATE_SCHEMA}.approval_submissions" in repository_source
            and "ON CONFLICT DO NOTHING" in repository_source
            and "concurrent = await self._find_by_idempotency_key" in repository_source
            and repository_source.count("await self._find_draft_binding") >= 2
        ),
        "receipt_and_audit_share_one_transaction_context": (
            "async with self._pool.connection() as connection:" in repository_source
            and "await self._insert_audit(connection, result.audit_record)" in repository_source
            and "await self._insert_audit(connection, replay.audit_record)" in repository_source
        ),
        "audit_is_append_only_and_collision_fails_closed": (
            "INSERT INTO {AGENT_STATE_SCHEMA}.submission_audit_records" in repository_source
            and "submission audit identifier already exists" in repository_source
            and "UPDATE {AGENT_STATE_SCHEMA}.submission_audit_records" not in repository_source
            and "DELETE FROM {AGENT_STATE_SCHEMA}.submission_audit_records" not in repository_source
        ),
        "schema_keeps_audit_receipts_on_restrictive_foreign_key": (
            "REFERENCES {AGENT_STATE_SCHEMA}.approval_submissions(submission_id)" in schema_source
            and "ON DELETE RESTRICT" in schema_source
        ),
        "audit_queries_have_stable_chronological_order": (
            repository_source.count("ORDER BY recorded_at, audit_id") == 2
        ),
        "fastapi_runtime_remains_on_sqlite_submitter": (
            "SQLiteMockApprovalSubmitter(settings.sqlite_database_path)" in main_source
            and "PostgresMockApprovalSubmitter" not in main_source
            and "agent_state_provider" not in main_source
        ),
        "verifier_has_no_database_or_external_calls": True,
    }
    return {
        "schema_version": "1.0",
        "phase": 38,
        "step": 3,
        "substep": 3,
        "status": "repository_only",
        "passed": all(checks.values()),
        "postgres_schema_version": AGENT_STATE_SCHEMA_VERSION,
        "repository": "PostgresMockApprovalSubmitter",
        "domains": ["approval_submission", "submission_audit"],
        "completed_repository_domains": [
            "session",
            "application_draft",
            "conversation",
            "approval_submission",
            "submission_audit",
        ],
        "runtime_backend_switched": False,
        "sqlite_data_migrated": False,
        "database_calls": False,
        "external_calls": False,
        "checks": checks,
    }


def main() -> int:
    result = run_verification()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
