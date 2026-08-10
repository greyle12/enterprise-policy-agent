from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

from scripts.check_container_health import HealthProbeError, check_health

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_COMPOSE_FILE = _PROJECT_ROOT / "compose.yaml"
_DEFAULT_PROBE_ID = "DAY17-CONTAINER-PROBE"


def _run(
    command: list[str],
    *,
    capture_output: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=_PROJECT_ROOT,
        check=check,
        text=True,
        capture_output=capture_output,
    )


def _compose_command(compose_file: Path) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-directory",
        str(_PROJECT_ROOT),
        "--file",
        str(compose_file),
    ]


def _probe_command(
    compose: list[str],
    operation: str,
    probe_id: str,
) -> list[str]:
    return [
        *compose,
        "exec",
        "-T",
        "agent",
        "python",
        "-m",
        "scripts.container_persistence_probe",
        operation,
        "--probe-id",
        probe_id,
    ]


def verify_deployment(
    *,
    compose_file: Path,
    health_url: str,
    wait_timeout_seconds: int,
    probe_id: str,
    build: bool,
) -> dict[str, object]:
    compose = _compose_command(compose_file)
    _run([*compose, "config", "--quiet"])

    up_command = [*compose, "up"]
    if build:
        up_command.append("--build")
    up_command.extend(
        [
            "--detach",
            "--wait",
            "--wait-timeout",
            str(wait_timeout_seconds),
            "agent",
        ]
    )
    _run(up_command)
    check_health(
        health_url,
        timeout_seconds=5.0,
        expected_status="ready",
    )

    probe_written = False
    try:
        _run(
            _probe_command(compose, "write", probe_id),
            capture_output=True,
        )
        probe_written = True
        _run(
            [
                *compose,
                "up",
                "--detach",
                "--force-recreate",
                "--wait",
                "--wait-timeout",
                str(wait_timeout_seconds),
                "agent",
            ]
        )
        read_result = _run(
            _probe_command(compose, "read", probe_id),
            capture_output=True,
        )
        probe_payload = json.loads(read_result.stdout)
    finally:
        if probe_written:
            _run(
                _probe_command(compose, "delete", probe_id),
                capture_output=True,
                check=False,
            )

    return {
        "compose_config_valid": True,
        "container_ready": True,
        "health_url": health_url,
        "sqlite_volume_survived_recreation": bool(
            probe_payload.get("persisted")
        ),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build the Day 17 Compose service and verify health plus "
            "SQLite persistence across container recreation."
        ),
    )
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=_DEFAULT_COMPOSE_FILE,
    )
    parser.add_argument(
        "--health-url",
        default="http://127.0.0.1:8000/health/ready",
    )
    parser.add_argument(
        "--wait-timeout-seconds",
        type=int,
        default=900,
    )
    parser.add_argument(
        "--probe-id",
        default=_DEFAULT_PROBE_ID,
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.wait_timeout_seconds < 1:
        print("wait timeout must be greater than zero")
        return 2

    try:
        result = verify_deployment(
            compose_file=args.compose_file.resolve(),
            health_url=args.health_url,
            wait_timeout_seconds=args.wait_timeout_seconds,
            probe_id=args.probe_id,
            build=not args.skip_build,
        )
    except (
        FileNotFoundError,
        HealthProbeError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as error:
        print(
            json.dumps(
                {
                    "passed": False,
                    "error_type": type(error).__name__,
                    "message": str(error),
                },
                ensure_ascii=False,
            )
        )
        return 1

    print(
        json.dumps(
            {
                "passed": True,
                **result,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
