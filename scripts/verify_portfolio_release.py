from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.evaluation.dataset import load_golden_dataset
from app.portfolio import PortfolioDemoScenario, run_offline_portfolio_demo
from app.rag.policy_chunker import chunk_policy_directory

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_REQUIRED_DOCUMENTS = (
    Path("docs/system_architecture.md"),
    Path("docs/portfolio_demo.md"),
    Path("docs/interview_guide.md"),
)
_PORTFOLIO_COMMAND = "python -X utf8 -m scripts.run_portfolio_demo"
_RELEASE_COMMAND = "python -X utf8 -m scripts.verify_portfolio_release"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def run_verification(project_root: str | Path = _PROJECT_ROOT) -> dict[str, object]:
    """Verify the Day 30 release evidence without network or live providers."""

    root = Path(project_root)
    policy_directory = root / "data" / "policies"
    report = asyncio.run(run_offline_portfolio_demo(policy_directory=policy_directory))
    chunks = tuple(chunk_policy_directory(policy_directory))
    policy_document_count = len({chunk.document_id for chunk in chunks})
    application_sample_count = len(tuple((root / "data/samples/applications").glob("*.json")))
    golden_dataset = load_golden_dataset(root / "tests/evaluation/golden_test_cases.jsonl")
    workflow = _read(root / ".github/workflows/ci.yml")
    readme = _read(root / "README.md")
    compose = _read(root / "compose.yaml")
    documents_present = all(
        (root / relative_path).is_file() for relative_path in _REQUIRED_DOCUMENTS
    )
    architecture = (
        _read(root / "docs/system_architecture.md")
        if (root / "docs/system_architecture.md").is_file()
        else ""
    )
    demo_guide = (
        _read(root / "docs/portfolio_demo.md")
        if (root / "docs/portfolio_demo.md").is_file()
        else ""
    )
    interview_guide = (
        _read(root / "docs/interview_guide.md")
        if (root / "docs/interview_guide.md").is_file()
        else ""
    )
    serialized_report = report.model_dump_json().casefold()
    expected_scenarios = {scenario.value for scenario in PortfolioDemoScenario}
    actual_scenarios = {scenario.scenario.value for scenario in report.scenarios}

    checks = {
        "portfolio_demo_passes": report.quality_gate_passed,
        "all_six_capabilities_are_demonstrated": (
            report.total_scenarios == 6 and actual_scenarios == expected_scenarios
        ),
        "demo_is_fully_offline": (
            report.execution_mode == "offline"
            and report.network_calls is False
            and report.live_llm_calls is False
        ),
        "demo_report_contains_no_attack_or_secret": (
            "ignore all previous" not in serialized_report
            and "api key" not in serialized_report
            and "password" not in serialized_report
        ),
        "checked_in_demo_assets_match_claims": (
            policy_document_count == 5
            and application_sample_count == 6
            and len(golden_dataset.cases) == 30
        ),
        "release_documents_are_present": documents_present,
        "architecture_declares_trust_boundaries": (
            "```mermaid" in architecture
            and "安全边界" in architecture
            and "外部系统边界" in architecture
        ),
        "demo_guide_is_reproducible": (
            "scripts.run_portfolio_demo" in demo_guide
            and "完全离线" in demo_guide
            and "portfolio-demo-report.json" in demo_guide
        ),
        "interview_material_is_honest": (
            "不能宣称" in interview_guide
            and "真实 BGE" in interview_guide
            and "生产" in interview_guide
        ),
        "readme_marks_day30_release": (
            "Phase 21" in readme
            and "Day 30 已完成" in readme
            and "scripts.run_portfolio_demo" in readme
        ),
        "ci_runs_and_persists_portfolio_evidence": (
            _PORTFOLIO_COMMAND in workflow
            and _RELEASE_COMMAND in workflow
            and "artifacts/portfolio/portfolio-demo-report.json" in workflow
            and "artifacts/portfolio/portfolio-demo-report.md" in workflow
        ),
        "compose_uses_day30_image": "enterprise-policy-agent:day30" in compose,
    }
    return {
        "passed": all(checks.values()),
        "schema_version": "1.0",
        "release_label": "day30",
        "portfolio_scenarios": report.total_scenarios,
        "portfolio_scenarios_passed": report.passed_scenarios,
        "policy_documents": policy_document_count,
        "application_samples": application_sample_count,
        "golden_cases": len(golden_dataset.cases),
        "checks": checks,
        "network_calls": False,
        "live_llm_calls": False,
    }


def main() -> int:
    report = run_verification()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
