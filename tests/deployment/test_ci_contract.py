from __future__ import annotations

import re
from pathlib import Path

from scripts.verify_ci_configuration import validate_ci_configuration

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def test_checked_in_ci_configuration_passes_its_contract() -> None:
    report = validate_ci_configuration(_PROJECT_ROOT)

    assert report.jobs == (
        "container-build",
        "dependency-review",
        "quality",
    )
    assert report.dependency_ecosystems == ("github-actions", "pip")
    assert len(report.workflow_sha256) == 64
    assert len(report.dependabot_sha256) == 64


def test_every_external_action_is_pinned_to_a_full_commit_sha() -> None:
    report = validate_ci_configuration(_PROJECT_ROOT)

    assert set(report.action_pins) == {
        "actions/checkout",
        "actions/dependency-review-action",
        "actions/setup-python",
        "actions/upload-artifact",
    }
    assert all(_FULL_SHA.fullmatch(sha) for sha in report.action_pins.values())


def test_ci_is_secretless_and_uses_unprivileged_pull_request_event() -> None:
    workflow = (_PROJECT_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "${{ secrets." not in workflow
    assert "pull_request_target" not in workflow
    assert "contents: read" in workflow
    assert "persist-credentials: false" in workflow


def test_ci_keeps_machine_readable_evidence_and_builds_container() -> None:
    workflow = (_PROJECT_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "artifacts/test-results/pytest.xml" in workflow
    assert "python -m ruff format --check ." in workflow
    assert "golden-evaluation-report.json" in workflow
    assert "agent-performance-report.json" in workflow
    assert "scripts.run_performance_benchmark --warmups 1 --iterations 5" in workflow
    assert "scripts.verify_async_singleflight" in workflow
    assert "scripts.verify_provider_backpressure" in workflow
    assert "scripts.verify_runtime_observability" in workflow
    assert "scripts.verify_rag_security" in workflow
    assert "scripts.verify_document_loader" in workflow
    assert "scripts.verify_pdf_document_parsing" in workflow
    assert "scripts.verify_docx_document_parsing" in workflow
    assert "scripts.verify_ocr_fallback" in workflow
    assert "scripts.verify_bm25_retrieval" in workflow
    assert "scripts.verify_hybrid_search" in workflow
    assert "scripts.run_portfolio_demo" in workflow
    assert "--output-dir artifacts/portfolio" in workflow
    assert "scripts.verify_portfolio_release" in workflow
    assert "portfolio-demo-report.json" in workflow
    assert "portfolio-demo-report.md" in workflow
    assert "scripts.run_concurrency_load_test" in workflow
    assert "agent-concurrency-load-report.json" in workflow
    assert "agent-concurrency-load-report.md" in workflow
    assert "scripts.run_batch_optimization" in workflow
    assert "--items 32 --batch-size 8" in workflow
    assert "agent-batch-optimization-report.json" in workflow
    assert "agent-batch-optimization-report.md" in workflow
    assert "torch-2.12.1+cpu-cp312-cp312-manylinux_2_28_x86_64.whl" in workflow
    assert "ae4bb28409f5370852bd71af221066236c38d647f780d9b0a7240c330a9c12df" in workflow
    assert "sha256sum --check" in workflow
    assert "dist/*.whl" in workflow
    assert "docker compose config --quiet" in workflow
    assert "docker build --pull --tag enterprise-policy-agent:ci ." in workflow
