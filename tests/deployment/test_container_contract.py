from __future__ import annotations

from pathlib import Path

import yaml

from scripts.verify_docker_deployment import (
    _compose_command,
    _pgvector_probe_command,
    _probe_command,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_dockerfile_uses_pinned_python_and_non_root_runtime() -> None:
    dockerfile = (_PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12.10-slim AS builder" in dockerfile
    assert "FROM python:3.12.10-slim AS runtime" in dockerfile
    assert "USER agent" in dockerfile
    assert "libgomp1" in dockerfile
    assert "EXPOSE 8000" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert 'CMD ["python", "-m", "app"]' in dockerfile
    assert "scripts/pgvector_persistence_probe.py" in dockerfile
    assert "scripts/index_policy_documents.py" in dockerfile
    assert "COPY . ." not in dockerfile


def test_compose_mounts_runtime_and_model_cache_volumes() -> None:
    compose = yaml.safe_load((_PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    service = compose["services"]["agent"]
    volume_targets = {item["target"]: item["source"] for item in service["volumes"]}

    assert service["environment"]["SQLITE_DATABASE_PATH"] == (
        "/app/data/runtime/enterprise_policy_agent.db"
    )
    assert volume_targets["/app/data/runtime"] == "agent_runtime"
    assert volume_targets["/app/data/model-cache"] == "model_cache"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert "agent_runtime" in compose["volumes"]
    assert "model_cache" in compose["volumes"]


def test_compose_healthcheck_targets_readiness_endpoint() -> None:
    compose = yaml.safe_load((_PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    healthcheck = compose["services"]["agent"]["healthcheck"]

    assert "scripts.check_container_health" in healthcheck["test"]
    assert "http://127.0.0.1:8000/health/ready" in healthcheck["test"]
    assert healthcheck["start_period"] == "10m"


def test_compose_provides_ephemeral_hardened_redis_cache() -> None:
    compose = yaml.safe_load((_PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    redis_service = compose["services"]["redis"]
    agent_environment = compose["services"]["agent"]["environment"]

    assert redis_service["image"] == "redis:8.10.0-alpine"
    assert redis_service["ports"] == ["127.0.0.1:${REDIS_PORT:-6379}:6379"]
    assert redis_service["read_only"] is True
    assert redis_service["cap_drop"] == ["ALL"]
    assert redis_service["cap_add"] == ["CHOWN", "SETGID", "SETUID"]
    assert redis_service["security_opt"] == ["no-new-privileges:true"]
    assert any(item.startswith("/data:") for item in redis_service["tmpfs"])
    assert "--maxmemory-policy" in redis_service["command"]
    assert "allkeys-lru" in redis_service["command"]
    assert redis_service["healthcheck"]["test"] == ["CMD", "redis-cli", "ping"]
    assert agent_environment["LLM_CACHE_PROVIDER"] == "redis"
    assert agent_environment["REDIS_URL"] == "redis://redis:6379/0"


def test_compose_provides_persistent_pgvector_and_authorized_store_configuration() -> None:
    compose = yaml.safe_load((_PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    postgres = compose["services"]["postgres"]
    agent = compose["services"]["agent"]

    assert postgres["image"] == "pgvector/pgvector:0.8.6-pg17-bookworm"
    assert postgres["ports"] == ["127.0.0.1:${POSTGRES_PORT:-5432}:5432"]
    assert postgres["healthcheck"]["test"][0] == "CMD-SHELL"
    assert "pg_isready" in postgres["healthcheck"]["test"][1]
    assert postgres["security_opt"] == ["no-new-privileges:true"]
    assert postgres["volumes"] == [
        {
            "type": "volume",
            "source": "pgvector_data",
            "target": "/var/lib/postgresql/data",
        }
    ]
    assert "pgvector_data" in compose["volumes"]
    assert agent["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert agent["environment"]["RAG_VECTOR_STORE_PROVIDER"] == "pgvector"
    assert agent["environment"]["RAG_INDEX_PIPELINE_VERSION"] == (
        "${RAG_INDEX_PIPELINE_VERSION:-policy-index-v1}"
    )
    assert "@postgres:5432/" in agent["environment"]["RAG_PGVECTOR_DSN"]


def test_runtime_dependencies_include_psycopg_binary_and_pool() -> None:
    pyproject = (_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '"psycopg[binary,pool]>=3.2,<4.0"' in pyproject


def test_docker_build_context_excludes_secrets_and_runtime_data() -> None:
    patterns = {
        line.strip()
        for line in (_PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }

    assert ".env" in patterns
    assert ".env.*" in patterns
    assert "data/runtime" in patterns
    assert "*.db" in patterns
    assert ".test-venv" in patterns


def test_verifier_recreates_service_and_uses_isolated_probe() -> None:
    compose_file = _PROJECT_ROOT / "compose.yaml"
    compose_command = _compose_command(compose_file)
    probe_command = _probe_command(
        compose_command,
        "write",
        "DAY17-TEST-PROBE",
    )
    pgvector_probe_command = _pgvector_probe_command(
        compose_command,
        "read",
        "PHASE29-TEST-PROBE",
    )

    assert compose_command[:2] == ["docker", "compose"]
    assert probe_command[-3:] == [
        "write",
        "--probe-id",
        "DAY17-TEST-PROBE",
    ]
    assert "scripts.container_persistence_probe" in probe_command
    assert pgvector_probe_command[-3:] == [
        "read",
        "--probe-id",
        "PHASE29-TEST-PROBE",
    ]
    assert "scripts.pgvector_persistence_probe" in pgvector_probe_command
