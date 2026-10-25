"""Start an isolated local Langfuse, verify one synthetic trace, then remove its resources."""

import argparse
import base64
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "deploy/langfuse-evaluation/compose.yml"


def scrub(value, credentials):
    private_values = list(credentials.values())
    if "LF_PUBLIC_KEY" in credentials and "LF_SECRET_KEY" in credentials:
        private_values.append(
            base64.b64encode(
                f"{credentials['LF_PUBLIC_KEY']}:{credentials['LF_SECRET_KEY']}".encode()
            ).decode()
        )
    for secret in sorted(private_values, key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    return value


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    project = "codex-lf-eval-" + uuid4().hex[:12]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    credentials = {
        "LF_" + name: secrets.token_hex(24)
        for name in (
            "DATABASE_PASSWORD",
            "CLICKHOUSE_PASSWORD",
            "MINIO_PASSWORD",
            "REDIS_PASSWORD",
            "SALT",
            "NEXTAUTH_SECRET",
        )
    }
    credentials.update(
        LF_ENCRYPTION_KEY=secrets.token_hex(32),
        LF_PUBLIC_KEY="pk-lf-" + uuid4().hex,
        LF_SECRET_KEY="sk-lf-" + uuid4().hex,
    )
    private = Path(tempfile.mkdtemp(prefix=project + "-"))
    env_file = private / "acceptance.env"
    env_file.write_text(
        "\n".join(
            f"{k}={v}"
            for k, v in {
                **credentials,
                "LF_HOST_PORT": str(port),
            }.items()
        )
        + "\n",
        "utf-8",
    )
    compose_path = output / "compose.yml"
    compose = yaml.safe_load(TEMPLATE.read_text("utf-8"))
    compose_path.write_text(yaml.safe_dump(compose, sort_keys=False), "utf-8")
    command = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--env-file",
        str(env_file),
        "-f",
        str(compose_path),
    ]
    result = {
        "project": project,
        "base_url": f"http://127.0.0.1:{port}",
        "live_model_calls": 0,
        "historical_capture_exported": False,
        "server_receipt_verified": False,
        "cleanup_verified": False,
    }

    def execute(args, timeout=60):
        completed = subprocess.run(
            args,
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
        if completed.returncode:
            raise RuntimeError(scrub(completed.stdout + completed.stderr, credentials)[-4000:])
        return completed.stdout

    def save(name, value):
        (output / name).write_text(json.dumps(value, indent=2) + "\n", "utf-8")

    try:
        execute(command + ["config", "--quiet"])
        print("Inspecting/pulling pinned Langfuse evaluation images", flush=True)
        inventory = {}
        for service in compose["services"].values():
            image = service["image"]
            inspected = subprocess.run(["docker", "image", "inspect", image], capture_output=True)
            if inspected.returncode:
                execute(["docker", "pull", image], timeout=900)
            info = json.loads(execute(["docker", "image", "inspect", image]))[0]
            digest = info["RepoDigests"][0]
            inventory[image] = {"id": info["Id"], "digest": digest}
            service["image"] = digest
        save("image-inventory.json", inventory)
        compose_path.write_text(yaml.safe_dump(compose, sort_keys=False), "utf-8")
        print("Starting isolated local services", flush=True)
        execute(command + ["up", "-d", "--wait", "--wait-timeout", "180"], timeout=240)
        deadline = time.monotonic() + 240
        with httpx.Client(timeout=2, trust_env=False) as client:
            while time.monotonic() < deadline:
                try:
                    health = client.get(result["base_url"] + "/api/public/health")
                    if health.status_code == 200:
                        save("health.json", health.json())
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(3)
            else:
                raise RuntimeError("local health deadline exceeded")
        print("Local service ready; sending one new synthetic metadata probe", flush=True)
        environment = {
            **os.environ,
            "NO_PROXY": "127.0.0.1,localhost,::1",
            "no_proxy": "127.0.0.1,localhost,::1",
            "LANGFUSE_BASE_URL": result["base_url"],
            "LANGFUSE_PUBLIC_KEY": credentials["LF_PUBLIC_KEY"],
            "LANGFUSE_SECRET_KEY": credentials["LF_SECRET_KEY"],
        }
        probe = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.verify_optional_tracing",
                "--export",
                "--output-dir",
                str(output / "probe"),
            ],
            env=environment,
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=100,
        )
        (output / "probe.log").write_text(scrub(probe.stdout + probe.stderr, credentials), "utf-8")
        receipt = json.loads((output / "probe/receipt-report.json").read_text("utf-8"))
        result.update(
            server_receipt_verified=receipt["server_receipt_verified"],
            probe_status=receipt["status"],
            trace_id=receipt.get("trace_id"),
        )
        result["status"] = (
            "verified"
            if probe.returncode == 0 and result["server_receipt_verified"]
            else "unverified"
        )
    except Exception as exc:
        result.update(status="failed", error_type=type(exc).__name__)
        (output / "diagnostic.log").write_text(scrub(str(exc), credentials), "utf-8")
        try:
            logs = execute(command + ["logs", "--no-color", "--tail", "80"])
            (output / "services.log").write_text(scrub(logs, credentials), "utf-8")
        except Exception:
            pass
    finally:
        try:
            ids = execute(
                ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"]
            ).split()
            for identifier in ids:
                labels = json.loads(
                    execute(
                        ["docker", "inspect", "--format", "{{json .Config.Labels}}", identifier]
                    )
                )
                if labels.get("codex.evaluation") != "langfuse-local-receipt":
                    raise RuntimeError("cleanup ownership mismatch")
            execute(command + ["down", "--volumes", "--remove-orphans"], timeout=120)
            remaining = execute(
                ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"]
            ).strip()
            volumes = execute(
                [
                    "docker",
                    "volume",
                    "ls",
                    "-q",
                    "--filter",
                    f"label=com.docker.compose.project={project}",
                ]
            ).strip()
            networks = execute(
                [
                    "docker",
                    "network",
                    "ls",
                    "-q",
                    "--filter",
                    f"label=com.docker.compose.project={project}",
                ]
            ).strip()
            result["cleanup_verified"] = not (remaining or volumes or networks)
        except Exception as exc:
            result["cleanup_error_type"] = type(exc).__name__
        env_file.unlink(missing_ok=True)
        private.rmdir()
        save("summary.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.output_dir.resolve())
    print(json.dumps(result, indent=2))
    return 0 if result["server_receipt_verified"] and result["cleanup_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
