from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scripts.verify_ci_configuration import (
    CIConfigurationError,
    main,
    validate_ci_configuration,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _copy_configuration(tmp_path: Path) -> tuple[Path, Path]:
    source = _PROJECT_ROOT / ".github"
    destination = tmp_path / ".github"
    shutil.copytree(source, destination)
    return destination / "workflows/ci.yml", destination / "dependabot.yml"


def _replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_cli_prints_a_successful_json_report(capsys) -> None:
    exit_code = main(["--project-root", str(_PROJECT_ROOT)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"passed": true' in output
    assert '"container-build"' in output
    assert '"github-actions"' in output


def test_rejects_mutable_action_reference(tmp_path: Path) -> None:
    workflow, _ = _copy_configuration(tmp_path)
    _replace_once(
        workflow,
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/checkout@v7",
    )

    with pytest.raises(CIConfigurationError, match="full 40-character SHA"):
        validate_ci_configuration(tmp_path)


def test_rejects_inconsistent_action_pins(tmp_path: Path) -> None:
    workflow, _ = _copy_configuration(tmp_path)
    _replace_once(
        workflow,
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/checkout@1111111111111111111111111111111111111111",
    )

    with pytest.raises(CIConfigurationError, match="inconsistent SHAs"):
        validate_ci_configuration(tmp_path)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            'PYTHONUTF8: "1"',
            "LLM_API_KEY: ${{ secrets.LLM_API_KEY }}",
            "must not read repository secrets",
        ),
        (
            "  pull_request:\n",
            "  pull_request_target:\n",
            "pull_request_target is forbidden",
        ),
        (
            "  contents: read",
            "  contents: write",
            "permissions must be exactly contents: read",
        ),
    ],
)
def test_rejects_unsafe_workflow_configuration(
    tmp_path: Path,
    old: str,
    new: str,
    message: str,
) -> None:
    workflow, _ = _copy_configuration(tmp_path)
    _replace_once(workflow, old, new)

    with pytest.raises(CIConfigurationError, match=message):
        validate_ci_configuration(tmp_path)


def test_rejects_missing_quality_gate_command(tmp_path: Path) -> None:
    workflow, _ = _copy_configuration(tmp_path)
    _replace_once(
        workflow,
        "python -m ruff check .",
        "python -m ruff check app",
    )

    with pytest.raises(CIConfigurationError, match="quality job is missing command"):
        validate_ci_configuration(tmp_path)


def test_rejects_missing_format_gate_command(tmp_path: Path) -> None:
    workflow, _ = _copy_configuration(tmp_path)
    _replace_once(
        workflow,
        "python -m ruff format --check .",
        "python -m ruff format --check app",
    )

    with pytest.raises(CIConfigurationError, match="quality job is missing command"):
        validate_ci_configuration(tmp_path)


def test_rejects_missing_runtime_observability_gate(tmp_path: Path) -> None:
    workflow, _ = _copy_configuration(tmp_path)
    _replace_once(
        workflow,
        "python -X utf8 -m scripts.verify_runtime_observability",
        "python -X utf8 -c \"print('observability gate removed')\"",
    )

    with pytest.raises(CIConfigurationError, match="quality job is missing command"):
        validate_ci_configuration(tmp_path)


def test_rejects_missing_rag_security_gate(tmp_path: Path) -> None:
    workflow, _ = _copy_configuration(tmp_path)
    _replace_once(
        workflow,
        "python -X utf8 -m scripts.verify_rag_security",
        "python -X utf8 -c \"print('security gate removed')\"",
    )

    with pytest.raises(CIConfigurationError, match="quality job is missing command"):
        validate_ci_configuration(tmp_path)


def test_rejects_missing_document_loader_gate(tmp_path: Path) -> None:
    workflow, _ = _copy_configuration(tmp_path)
    _replace_once(
        workflow,
        "python -X utf8 -m scripts.verify_document_loader",
        "python -X utf8 -c \"print('document loader gate removed')\"",
    )

    with pytest.raises(CIConfigurationError, match="quality job is missing command"):
        validate_ci_configuration(tmp_path)


def test_rejects_missing_pdf_document_parsing_gate(tmp_path: Path) -> None:
    workflow, _ = _copy_configuration(tmp_path)
    _replace_once(
        workflow,
        "python -X utf8 -m scripts.verify_pdf_document_parsing",
        "python -X utf8 -c \"print('PDF parsing gate removed')\"",
    )

    with pytest.raises(CIConfigurationError, match="quality job is missing command"):
        validate_ci_configuration(tmp_path)


def test_rejects_missing_docx_document_parsing_gate(tmp_path: Path) -> None:
    workflow, _ = _copy_configuration(tmp_path)
    _replace_once(
        workflow,
        "python -X utf8 -m scripts.verify_docx_document_parsing",
        "python -X utf8 -c \"print('DOCX parsing gate removed')\"",
    )

    with pytest.raises(CIConfigurationError, match="quality job is missing command"):
        validate_ci_configuration(tmp_path)


def test_rejects_missing_ocr_fallback_gate(tmp_path: Path) -> None:
    workflow, _ = _copy_configuration(tmp_path)
    _replace_once(
        workflow,
        "python -X utf8 -m scripts.verify_ocr_fallback",
        "python -X utf8 -c \"print('OCR fallback gate removed')\"",
    )

    with pytest.raises(CIConfigurationError, match="quality job is missing command"):
        validate_ci_configuration(tmp_path)


def test_rejects_missing_bm25_retrieval_gate(tmp_path: Path) -> None:
    workflow, _ = _copy_configuration(tmp_path)
    _replace_once(
        workflow,
        "python -X utf8 -m scripts.verify_bm25_retrieval",
        "python -X utf8 -c \"print('BM25 retrieval gate removed')\"",
    )

    with pytest.raises(CIConfigurationError, match="quality job is missing command"):
        validate_ci_configuration(tmp_path)


def test_rejects_missing_hybrid_search_gate(tmp_path: Path) -> None:
    workflow, _ = _copy_configuration(tmp_path)
    _replace_once(
        workflow,
        "python -X utf8 -m scripts.verify_hybrid_search",
        "python -X utf8 -c \"print('Hybrid Search gate removed')\"",
    )

    with pytest.raises(CIConfigurationError, match="quality job is missing command"):
        validate_ci_configuration(tmp_path)


def test_rejects_missing_reranker_integration_gate(tmp_path: Path) -> None:
    workflow, _ = _copy_configuration(tmp_path)
    _replace_once(
        workflow,
        "python -X utf8 -m scripts.verify_reranker_integration",
        "python -X utf8 -c \"print('Reranker integration gate removed')\"",
    )

    with pytest.raises(CIConfigurationError, match="quality job is missing command"):
        validate_ci_configuration(tmp_path)


def test_rejects_missing_pgvector_store_gate(tmp_path: Path) -> None:
    workflow, _ = _copy_configuration(tmp_path)
    _replace_once(
        workflow,
        "python -X utf8 -m scripts.verify_pgvector_store",
        "python -X utf8 -c \"print('pgvector store gate removed')\"",
    )

    with pytest.raises(CIConfigurationError, match="quality job is missing command"):
        validate_ci_configuration(tmp_path)


def test_rejects_missing_document_indexing_gate(tmp_path: Path) -> None:
    workflow, _ = _copy_configuration(tmp_path)
    _replace_once(
        workflow,
        "python -X utf8 -m scripts.verify_document_indexing",
        "python -X utf8 -c \"print('document indexing gate removed')\"",
    )

    with pytest.raises(CIConfigurationError, match="quality job is missing command"):
        validate_ci_configuration(tmp_path)


def test_rejects_missing_portfolio_demo_gate(tmp_path: Path) -> None:
    workflow, _ = _copy_configuration(tmp_path)
    _replace_once(
        workflow,
        "python -X utf8 -m scripts.run_portfolio_demo",
        "python -X utf8 -c \"print('portfolio demo removed')\"",
    )

    with pytest.raises(CIConfigurationError, match="quality job is missing command"):
        validate_ci_configuration(tmp_path)


def test_rejects_missing_portfolio_release_gate(tmp_path: Path) -> None:
    workflow, _ = _copy_configuration(tmp_path)
    _replace_once(
        workflow,
        "python -X utf8 -m scripts.verify_portfolio_release",
        "python -X utf8 -c \"print('portfolio release removed')\"",
    )

    with pytest.raises(CIConfigurationError, match="quality job is missing command"):
        validate_ci_configuration(tmp_path)


def test_rejects_missing_retrieval_evaluation_gate(tmp_path: Path) -> None:
    workflow, _ = _copy_configuration(tmp_path)
    _replace_once(
        workflow,
        "python -X utf8 -m scripts.run_retrieval_evaluation --mode offline",
        "python -X utf8 -c \"print('retrieval evaluation removed')\"",
    )

    with pytest.raises(CIConfigurationError, match="quality job is missing command"):
        validate_ci_configuration(tmp_path)


def test_rejects_checkout_credentials_persistence(tmp_path: Path) -> None:
    workflow, _ = _copy_configuration(tmp_path)
    _replace_once(
        workflow,
        "persist-credentials: false",
        "persist-credentials: true",
    )

    with pytest.raises(CIConfigurationError, match="persist-credentials: false"):
        validate_ci_configuration(tmp_path)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            "if: ${{ github.event_name != 'pull_request' }}",
            "if: ${{ always() }}",
            "container-build job must not run for pull requests",
        ),
        (
            "    needs:\n      - quality",
            "    needs:\n      - dependency-review",
            "container-build job must depend only on quality",
        ),
        (
            "if: ${{ github.event_name == 'pull_request' }}",
            "if: ${{ always() }}",
            "dependency-review job must run only for pull requests",
        ),
    ],
)
def test_rejects_unsafe_job_routing(
    tmp_path: Path,
    old: str,
    new: str,
    message: str,
) -> None:
    workflow, _ = _copy_configuration(tmp_path)
    _replace_once(workflow, old, new)

    with pytest.raises(CIConfigurationError, match=message):
        validate_ci_configuration(tmp_path)


def test_rejects_missing_dependabot_ecosystem(tmp_path: Path) -> None:
    _, dependabot = _copy_configuration(tmp_path)
    _replace_once(
        dependabot,
        "package-ecosystem: github-actions",
        "package-ecosystem: docker",
    )

    with pytest.raises(CIConfigurationError, match="missing ecosystems"):
        validate_ci_configuration(tmp_path)


def test_rejects_invalid_workflow_yaml(tmp_path: Path) -> None:
    workflow, _ = _copy_configuration(tmp_path)
    workflow.write_text("jobs: [\n", encoding="utf-8")

    with pytest.raises(CIConfigurationError, match="invalid YAML"):
        validate_ci_configuration(tmp_path)
