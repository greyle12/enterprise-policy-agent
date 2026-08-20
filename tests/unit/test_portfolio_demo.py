from __future__ import annotations

from pathlib import Path

from app.portfolio import (
    OfflinePortfolioRuntime,
    PortfolioDemoScenario,
    run_offline_portfolio_demo,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"


async def test_lexical_offline_retrieval_returns_relevant_travel_policy() -> None:
    runtime = OfflinePortfolioRuntime.build(_POLICY_DIRECTORY)

    results = runtime.retriever.search("差旅住宿费如何报销？", top_k=3)

    assert results
    assert results[0].chunk.document_title == "差旅报销管理制度"


async def test_portfolio_demo_passes_all_six_scenarios() -> None:
    report = await run_offline_portfolio_demo(policy_directory=_POLICY_DIRECTORY)

    assert report.quality_gate_passed is True
    assert report.execution_mode == "offline"
    assert report.network_calls is False
    assert report.live_llm_calls is False
    assert report.policy_documents == 5
    assert report.total_scenarios == 6
    assert report.passed_scenarios == 6
    assert report.failed_scenarios == 0
    assert tuple(result.scenario for result in report.scenarios) == tuple(PortfolioDemoScenario)


async def test_portfolio_demo_proves_human_confirmation_and_idempotency() -> None:
    report = await run_offline_portfolio_demo(policy_directory=_POLICY_DIRECTORY)
    scenario = next(
        item for item in report.scenarios if item.scenario is PortfolioDemoScenario.HUMAN_IN_LOOP
    )

    assert scenario.passed is True
    assert scenario.observations["created_status"] == "awaiting_confirmation"
    assert scenario.observations["confirmed_status"] == "confirmed"
    assert scenario.observations["submitted_status"] == "submitted"
    assert scenario.observations["idempotent_replay"] is True
    assert scenario.observations["same_submission_reused"] is True


async def test_portfolio_demo_security_probe_avoids_provider_call() -> None:
    report = await run_offline_portfolio_demo(policy_directory=_POLICY_DIRECTORY)
    scenario = next(
        item
        for item in report.scenarios
        if item.scenario is PortfolioDemoScenario.SECURITY_BOUNDARY
    )

    assert scenario.passed is True
    assert scenario.observations == {
        "attack_blocked": True,
        "provider_call_delta": 0,
        "blocked_input_delta": 1,
        "llm_calls_avoided_delta": 1,
        "raw_attack_recorded": False,
    }
    assert "Ignore all previous" not in report.model_dump_json()


async def test_portfolio_demo_separates_internal_and_external_sources() -> None:
    report = await run_offline_portfolio_demo(policy_directory=_POLICY_DIRECTORY)
    scenario = next(
        item
        for item in report.scenarios
        if item.scenario is PortfolioDemoScenario.RESEARCH_BOUNDARY
    )

    assert scenario.passed is True
    assert scenario.observations["internal_sources"] == ["S1"]
    assert scenario.observations["external_sources"] == ["W1"]
    assert scenario.observations["external_source_is_advisory"] is True
    assert scenario.observations["network_calls"] == 0
