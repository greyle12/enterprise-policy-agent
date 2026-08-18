from __future__ import annotations

import importlib
import sys
from types import ModuleType
from types import SimpleNamespace

import pytest


def test_entrypoint_uses_json_logging_and_disables_raw_access_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    fake_uvicorn = ModuleType("uvicorn")
    fake_uvicorn.run = lambda *args, **kwargs: observed.update(  # type: ignore[attr-defined]
        {"args": args, "kwargs": kwargs}
    )
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    entrypoint = importlib.import_module("app.__main__")
    monkeypatch.setattr(
        entrypoint,
        "get_settings",
        lambda: SimpleNamespace(
            app_host="127.0.0.1",
            app_port=8000,
            log_level="INFO",
        ),
    )
    entrypoint.main()

    assert observed["args"] == ("app.main:app",)
    kwargs = observed["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["access_log"] is False
    assert kwargs["log_level"] == "info"
    assert kwargs["server_header"] is False
    assert kwargs["log_config"]["formatters"]["json"]["()"] == (
        "app.observability.logging.JsonLogFormatter"
    )
