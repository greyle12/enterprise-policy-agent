from pathlib import Path

from scripts.verify_runtime_provider_preparation import run_verification

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_runtime_provider_preparation_gate_passes() -> None:
    result = run_verification(_PROJECT_ROOT)

    assert result["passed"] is True
    assert result["provider_factory_wired"] is False
    assert result["postgres_activation_ready"] is False
    assert result["runtime_backend_switched"] is False
    assert result["sqlite_data_migrated"] is False
    assert result["database_calls"] is False
    assert result["external_calls"] is False
