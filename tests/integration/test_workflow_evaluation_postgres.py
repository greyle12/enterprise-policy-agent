"""Shared dataset against real PostgreSQL projections, submissions and checkpoints."""

import os
from pathlib import Path

import pytest
from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import AsyncConnectionPool

from app.evaluation.workflow_evaluation import evaluate_workflow_case, load_workflow_cases
from app.persistence.postgres_checkpointer import PostgresCheckpointRuntime
from app.persistence.postgres_runtime import PostgresAgentStateStore
from app.persistence.postgres_schema import PostgresAgentStateSchemaManager
from app.persistence.postgres_submission import PostgresMockApprovalSubmitter
from app.persistence import SQLiteAgentStateStore, SQLiteMockApprovalSubmitter

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.postgres_integration


@pytest.mark.parametrize("projections", ["sqlite", "postgresql"])
async def test_workflow_dataset_postgres_final_state(projections, tmp_path):
    dsn = os.getenv("AGENT_POSTGRES_TEST_DSN")
    if not dsn:
        pytest.skip("AGENT_POSTGRES_TEST_DSN is not configured")
    if not conninfo_to_dict(dsn).get("dbname", "").endswith("_test"):
        pytest.fail("evaluation requires isolated database ending in _test")
    PostgresAgentStateSchemaManager.from_dsn(dsn).setup()
    async with AsyncConnectionPool(dsn, min_size=1, max_size=4, open=False) as pool:
        async with PostgresCheckpointRuntime(dsn) as checkpoints:
            await checkpoints.setup()
            cases = [
                case
                for dataset in (
                    "workflow_cases.jsonl",
                    "workflow_challenge_cases.jsonl",
                    "workflow_safety_cases.jsonl",
                )
                for case in load_workflow_cases(ROOT / "tests/evaluation" / dataset)
            ]
            for case in cases:
                database = tmp_path / (case.case_id + ".db")
                result = await evaluate_workflow_case(
                    case,
                    store=PostgresAgentStateStore(pool=pool)
                    if projections == "postgresql"
                    else SQLiteAgentStateStore(database),
                    submitter=PostgresMockApprovalSubmitter(pool=pool)
                    if projections == "postgresql"
                    else SQLiteMockApprovalSubmitter(database),
                    checkpointer=checkpoints.checkpointer,
                    policy_directory=ROOT / "data/policies",
                )
                assert result["passed"], [a for a in result["assertions"] if not a["passed"]]
