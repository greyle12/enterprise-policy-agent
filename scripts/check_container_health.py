from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class HealthProbeError(RuntimeError):
    """Raised when a health endpoint cannot prove the expected state."""


def check_health(
    url: str,
    *,
    timeout_seconds: float = 4.0,
    expected_status: str = "ready",
) -> dict[str, Any]:
    """Fetch and validate one JSON health response using the standard library."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")

    request = Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status_code = response.status
            raw_body = response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise HealthProbeError(f"health endpoint is unavailable: {type(error).__name__}") from error

    if status_code != 200:
        raise HealthProbeError(f"health endpoint returned HTTP {status_code}")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HealthProbeError("health endpoint did not return valid UTF-8 JSON") from error

    if not isinstance(payload, dict):
        raise HealthProbeError("health response must be a JSON object")
    if payload.get("status") != expected_status:
        raise HealthProbeError(
            f"health endpoint did not report the expected status: {expected_status}"
        )
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the Enterprise Policy Agent health endpoint.",
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8000/health/ready",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=4.0,
    )
    parser.add_argument(
        "--expected-status",
        default="ready",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        payload = check_health(
            args.url,
            timeout_seconds=args.timeout_seconds,
            expected_status=args.expected_status,
        )
    except (HealthProbeError, ValueError) as error:
        print(
            json.dumps(
                {
                    "healthy": False,
                    "error": str(error),
                },
                ensure_ascii=False,
            )
        )
        return 1

    print(
        json.dumps(
            {
                "healthy": True,
                "response": payload,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
