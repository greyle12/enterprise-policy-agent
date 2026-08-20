from __future__ import annotations

import json
from urllib.error import URLError

import pytest

from scripts import check_container_health as health_module


class FakeResponse:
    def __init__(self, payload: object, *, status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_check_health_accepts_expected_ready_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        health_module,
        "urlopen",
        lambda request, timeout: FakeResponse(
            {
                "status": "ready",
                "checks": {
                    "application": "ok",
                    "database": "ok",
                },
            }
        ),
    )

    payload = health_module.check_health("http://127.0.0.1:8000/health/ready")

    assert payload["status"] == "ready"


def test_check_health_rejects_unexpected_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        health_module,
        "urlopen",
        lambda request, timeout: FakeResponse({"status": "not_ready"}),
    )

    with pytest.raises(
        health_module.HealthProbeError,
        match="expected status",
    ):
        health_module.check_health("http://127.0.0.1:8000/health/ready")


def test_check_health_normalizes_connection_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(request, timeout):
        raise URLError("connection refused at a sensitive host")

    monkeypatch.setattr(health_module, "urlopen", unavailable)

    with pytest.raises(
        health_module.HealthProbeError,
        match="URLError",
    ) as error:
        health_module.check_health("http://127.0.0.1:8000/health/ready")

    assert "sensitive host" not in str(error.value)


def test_health_cli_returns_nonzero_for_failed_probe(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_probe(*args, **kwargs):
        raise health_module.HealthProbeError("not ready")

    monkeypatch.setattr(health_module, "check_health", fail_probe)

    exit_code = health_module.main([])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output == {
        "healthy": False,
        "error": "not ready",
    }
