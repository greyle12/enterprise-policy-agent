"""Safety contracts for the disposable local acceptance fixture; Docker calls mocked."""

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts import run_local_langfuse_acceptance as local


def test_local_stack_binds_only_loopback_and_uses_internal_network_and_named_volumes():
    compose = yaml.safe_load(local.TEMPLATE.read_text("utf-8"))
    assert compose["networks"]["default"]["internal"] is True
    assert compose["services"]["langfuse-web"]["networks"] == ["default", "frontend"]
    assert "frontend" in compose["networks"]
    assert all(
        "frontend" not in service.get("networks", [])
        for name, service in compose["services"].items()
        if name != "langfuse-web"
    )
    published = [
        (name, port)
        for name, service in compose["services"].items()
        for port in service.get("ports", [])
    ]
    assert published == [("langfuse-web", "127.0.0.1:${LF_HOST_PORT:?required}:3000")]
    for service in compose["services"].values():
        assert service["labels"]["codex.evaluation"] == "langfuse-local-receipt"
        assert "mem_limit" in service
        for volume in service.get("volumes", []):
            assert volume.split(":", 1)[0] in compose["volumes"]


def test_stack_has_no_model_credentials_and_requires_generated_project_credentials():
    compose = yaml.safe_load(local.TEMPLATE.read_text("utf-8"))
    for name in ("langfuse-worker", "langfuse-web"):
        env = compose["services"][name]["environment"]
        assert env["TELEMETRY_ENABLED"] == "false"
        assert env["LANGFUSE_IN_APP_AGENT_ENABLED"] == "false"
        assert not {"OPENAI_API_KEY", "EVAL_API_KEY", "LANGFUSE_AI_API_KEY"} & env.keys()
        assert env["ENCRYPTION_KEY"] == "${LF_ENCRYPTION_KEY:?required}"
    env = compose["services"]["langfuse-web"]["environment"]
    assert env["LANGFUSE_INIT_PROJECT_SECRET_KEY"] == "${LF_SECRET_KEY:?required}"


def test_ci_only_runs_mocked_local_acceptance_contracts():
    workflow = yaml.safe_load((local.ROOT / ".github/workflows/ci.yml").read_text("utf-8"))
    commands = "\n".join(step.get("run", "") for step in workflow["jobs"]["quality"]["steps"])
    assert "tests/deployment/test_local_langfuse_acceptance.py" in commands
    assert "-m scripts.run_local_langfuse_acceptance" not in commands


def test_existing_output_blocks_all_docker_operations(tmp_path, monkeypatch):
    monkeypatch.setattr(local.subprocess, "run", lambda *a, **kw: pytest.fail("Docker called"))
    with pytest.raises(FileExistsError):
        local.run(tmp_path)


def test_diagnostics_redact_tokens_and_basic_authorization():
    credentials = {"LF_PUBLIC_KEY": "pk-synthetic", "LF_SECRET_KEY": "sk-private"}
    basic = base64.b64encode(b"pk-synthetic:sk-private").decode()
    assert local.scrub(f"pk-synthetic sk-private Basic {basic}", credentials) == (
        "[REDACTED] [REDACTED] Basic [REDACTED]"
    )


def test_successful_probe_bypasses_proxy_and_cleans_local_stack(tmp_path, monkeypatch):
    import httpx

    calls = []
    real_http = httpx.Client
    monkeypatch.setenv("HTTP_PROXY", "http://unused.invalid:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://unused.invalid:1")

    def docker_or_probe(args, **kwargs):
        calls.append(args)
        stdout = ""
        if args[:3] == ["docker", "image", "inspect"]:
            stdout = json.dumps(
                [{"RepoDigests": ["example@sha256:" + "a" * 64], "Id": "sha256:synthetic"}]
            )
        elif "scripts.verify_optional_tracing" in args:
            env = kwargs["env"]
            assert env["NO_PROXY"] == env["no_proxy"] == "127.0.0.1,localhost,::1"
            assert env["HTTP_PROXY"] == "http://unused.invalid:1"
            assert env["LANGFUSE_BASE_URL"].startswith("http://127.0.0.1:")
            probe = Path(args[-1])
            probe.mkdir()
            (probe / "receipt-report.json").write_text(
                json.dumps(
                    {
                        "server_receipt_verified": True,
                        "status": "receipt_verified",
                        "trace_id": "a" * 32,
                    }
                ),
                "utf-8",
            )
        if not kwargs.get("text"):
            stdout = stdout.encode()
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(local.subprocess, "run", docker_or_probe)
    monkeypatch.setattr(
        local.httpx,
        "Client",
        lambda **kw: real_http(
            **kw,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"status": "OK", "version": "synthetic"})
            ),
        ),
    )
    report = local.run(tmp_path / "new")
    assert report["server_receipt_verified"] and report["cleanup_verified"]
    assert report["status"] == "verified"
    assert any("down" in args for args in calls)
    assert report["live_model_calls"] == 0 and not report["historical_capture_exported"]


@pytest.mark.parametrize("owned", [True, False])
def test_startup_failure_redacts_credentials_and_guards_resource_cleanup(
    tmp_path, monkeypatch, owned
):
    calls, private_files = [], []
    removed = False
    secret = None

    def docker(args, **kwargs):
        nonlocal removed, secret
        calls.append(args)
        stdout, code = "", 0
        if args[:3] == ["docker", "image", "inspect"]:
            stdout = json.dumps(
                [{"RepoDigests": ["example@sha256:" + "a" * 64], "Id": "sha256:synthetic"}]
            )
        elif args[:2] == ["docker", "compose"]:
            env_file = Path(args[args.index("--env-file") + 1])
            private_files.append(env_file)
            credentials = dict(
                line.split("=", 1) for line in env_file.read_text("utf-8").splitlines()
            )
            secret = credentials["LF_SECRET_KEY"]
            if "up" in args:
                stdout, code = f"private startup failure {secret}", 1
            elif "down" in args:
                removed = True
        elif args[:3] == ["docker", "ps", "-aq"]:
            stdout = "synthetic-container\n" if not removed else ""
        elif args[:3] == ["docker", "inspect", "--format"]:
            stdout = json.dumps(
                {"codex.evaluation": "langfuse-local-receipt" if owned else "other"}
            )
        if not kwargs.get("text"):
            stdout = stdout.encode()
        return SimpleNamespace(returncode=code, stdout=stdout, stderr="")

    monkeypatch.setattr(local.subprocess, "run", docker)
    output = tmp_path / "new"
    report = local.run(output)
    assert report["status"] == "failed" and not report["server_receipt_verified"]
    assert report["cleanup_verified"] is owned
    assert any("down" in args for args in calls) is owned
    assert secret not in (output / "diagnostic.log").read_text("utf-8")
    assert all(not path.exists() and not path.parent.exists() for path in private_files)
