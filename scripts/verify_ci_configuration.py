from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW_RELATIVE_PATH = Path(".github/workflows/ci.yml")
_DEPENDABOT_RELATIVE_PATH = Path(".github/dependabot.yml")
_ACTION_REFERENCE_PATTERN = re.compile(
    r"^(?P<action>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@"
    r"(?P<sha>[0-9a-f]{40})$"
)
_REQUIRED_ACTIONS = {
    "actions/checkout",
    "actions/dependency-review-action",
    "actions/setup-python",
    "actions/upload-artifact",
}
_REQUIRED_QUALITY_COMMANDS = (
    'python -m pip install -e ".[dev]"',
    "python -m pip check",
    "python -m scripts.verify_ci_configuration",
    "python -m ruff check .",
    "python -m pytest --junitxml=artifacts/test-results/pytest.xml",
    "python -X utf8 -m scripts.run_golden_evaluation --mode offline",
    "python -X utf8 -m scripts.run_performance_benchmark --warmups 1 --iterations 5",
    "python -m pip wheel . --no-deps --wheel-dir dist",
)
_REQUIRED_CONTAINER_COMMANDS = (
    "cp .env.example .env",
    "docker compose config --quiet",
    "docker build --pull --tag enterprise-policy-agent:ci .",
    "docker run --rm --entrypoint python enterprise-policy-agent:ci",
)


class CIConfigurationError(ValueError):
    """Raised when the checked-in CI configuration violates its contract."""


@dataclass(frozen=True)
class CIConfigurationReport:
    workflow_sha256: str
    dependabot_sha256: str
    jobs: tuple[str, ...]
    action_pins: dict[str, str]
    dependency_ecosystems: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise CIConfigurationError(f"cannot read {path}: {exc}") from exc


def _load_mapping(path: Path, text: str) -> Mapping[str, Any]:
    try:
        value = yaml.load(text, Loader=yaml.BaseLoader)
    except yaml.YAMLError as exc:
        raise CIConfigurationError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise CIConfigurationError(f"{path} must contain a YAML object")
    return value


def _mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CIConfigurationError(f"{label} must be a YAML object")
    return value


def _sequence(value: Any, *, label: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise CIConfigurationError(f"{label} must be a YAML list")
    return value


def _job_steps(job: Mapping[str, Any], *, job_name: str) -> tuple[Mapping[str, Any], ...]:
    steps = _sequence(job.get("steps"), label=f"jobs.{job_name}.steps")
    return tuple(_mapping(step, label=f"jobs.{job_name}.steps item") for step in steps)


def _run_commands(steps: Sequence[Mapping[str, Any]]) -> str:
    return "\n".join(str(step["run"]) for step in steps if "run" in step)


def _validate_triggers(workflow: Mapping[str, Any]) -> None:
    triggers = _mapping(workflow.get("on"), label="on")
    required = {"push", "pull_request", "workflow_dispatch"}
    missing = required.difference(triggers)
    if missing:
        raise CIConfigurationError(f"workflow is missing triggers: {', '.join(sorted(missing))}")
    if "pull_request_target" in triggers:
        raise CIConfigurationError("pull_request_target is forbidden")


def _validate_permissions(workflow: Mapping[str, Any]) -> None:
    permissions = _mapping(workflow.get("permissions"), label="permissions")
    if dict(permissions) != {"contents": "read"}:
        raise CIConfigurationError("workflow permissions must be exactly contents: read")


def _validate_concurrency(workflow: Mapping[str, Any]) -> None:
    concurrency = _mapping(workflow.get("concurrency"), label="concurrency")
    if concurrency.get("cancel-in-progress") != "true":
        raise CIConfigurationError("concurrency must cancel in-progress runs")
    if not str(concurrency.get("group", "")).startswith("ci-"):
        raise CIConfigurationError("concurrency group must start with ci-")


def _collect_action_pins(
    jobs: Mapping[str, Any],
) -> dict[str, str]:
    pins: dict[str, str] = {}
    for job_name, job_value in jobs.items():
        job = _mapping(job_value, label=f"jobs.{job_name}")
        for step in _job_steps(job, job_name=str(job_name)):
            reference = step.get("uses")
            if reference is None:
                continue
            match = _ACTION_REFERENCE_PATTERN.fullmatch(str(reference))
            if match is None:
                raise CIConfigurationError(
                    f"action reference must use a full 40-character SHA: {reference}"
                )
            action = match.group("action")
            sha = match.group("sha")
            previous = pins.setdefault(action, sha)
            if previous != sha:
                raise CIConfigurationError(f"action {action} uses inconsistent SHAs")

            if action == "actions/checkout":
                with_values = _mapping(step.get("with"), label="checkout.with")
                if with_values.get("persist-credentials") != "false":
                    raise CIConfigurationError("checkout must set persist-credentials: false")

    missing = _REQUIRED_ACTIONS.difference(pins)
    if missing:
        raise CIConfigurationError(
            f"workflow is missing required actions: {', '.join(sorted(missing))}"
        )
    return dict(sorted(pins.items()))


def _validate_jobs(jobs: Mapping[str, Any]) -> None:
    required_jobs = {"quality", "dependency-review", "container-build"}
    missing_jobs = required_jobs.difference(jobs)
    if missing_jobs:
        raise CIConfigurationError(f"workflow is missing jobs: {', '.join(sorted(missing_jobs))}")

    for job_name in required_jobs:
        job = _mapping(jobs[job_name], label=f"jobs.{job_name}")
        timeout = job.get("timeout-minutes")
        if timeout is None or int(str(timeout)) <= 0:
            raise CIConfigurationError(f"jobs.{job_name} must define a positive timeout-minutes")

    quality = _mapping(jobs["quality"], label="jobs.quality")
    quality_steps = _job_steps(quality, job_name="quality")
    quality_commands = _run_commands(quality_steps)
    for command in _REQUIRED_QUALITY_COMMANDS:
        if command not in quality_commands:
            raise CIConfigurationError(f"quality job is missing command: {command}")

    setup_steps = [
        step
        for step in quality_steps
        if str(step.get("uses", "")).startswith("actions/setup-python@")
    ]
    if len(setup_steps) != 1:
        raise CIConfigurationError("quality job must use setup-python once")
    setup_values = _mapping(setup_steps[0].get("with"), label="setup-python.with")
    if setup_values.get("python-version") != "3.12.10":
        raise CIConfigurationError("CI Python must be pinned to 3.12.10")
    if setup_values.get("cache") != "pip":
        raise CIConfigurationError("setup-python must enable the pip cache")

    artifact_steps = [
        step
        for step in quality_steps
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    if len(artifact_steps) < 2:
        raise CIConfigurationError("quality job must upload evidence and wheel artifacts")
    for step in artifact_steps:
        values = _mapping(step.get("with"), label="upload-artifact.with")
        if values.get("retention-days") != "14":
            raise CIConfigurationError("artifacts must use a 14-day retention period")
        if values.get("if-no-files-found") not in {"warn", "error"}:
            raise CIConfigurationError("artifact upload must define if-no-files-found behavior")

    dependency_review = _mapping(jobs["dependency-review"], label="jobs.dependency-review")
    if dependency_review.get("if") != "${{ github.event_name == 'pull_request' }}":
        raise CIConfigurationError("dependency-review job must run only for pull requests")
    dependency_steps = _job_steps(dependency_review, job_name="dependency-review")
    review_steps = [
        step
        for step in dependency_steps
        if str(step.get("uses", "")).startswith("actions/dependency-review-action@")
    ]
    if len(review_steps) != 1:
        raise CIConfigurationError("dependency-review job must use dependency-review-action once")
    review_values = _mapping(review_steps[0].get("with"), label="dependency-review-action.with")
    if review_values.get("fail-on-severity") != "high":
        raise CIConfigurationError("dependency review must fail on high severity or above")

    container = _mapping(jobs["container-build"], label="jobs.container-build")
    if container.get("if") != "${{ github.event_name != 'pull_request' }}":
        raise CIConfigurationError("container-build job must not run for pull requests")
    container_needs = _sequence(container.get("needs"), label="jobs.container-build.needs")
    if tuple(container_needs) != ("quality",):
        raise CIConfigurationError("container-build job must depend only on quality")
    container_commands = _run_commands(_job_steps(container, job_name="container-build"))
    for command in _REQUIRED_CONTAINER_COMMANDS:
        if command not in container_commands:
            raise CIConfigurationError(f"container-build job is missing command: {command}")


def _validate_dependabot(config: Mapping[str, Any]) -> tuple[str, ...]:
    if config.get("version") != "2":
        raise CIConfigurationError("Dependabot config version must be 2")
    updates = _sequence(config.get("updates"), label="dependabot updates")
    ecosystems: set[str] = set()
    for index, update_value in enumerate(updates):
        update = _mapping(update_value, label=f"dependabot updates[{index}]")
        ecosystem = str(update.get("package-ecosystem", ""))
        schedule = _mapping(update.get("schedule"), label=f"dependabot updates[{index}].schedule")
        if schedule.get("interval") != "weekly":
            raise CIConfigurationError(f"Dependabot ecosystem {ecosystem} must run weekly")
        ecosystems.add(ecosystem)

    required = {"pip", "github-actions"}
    missing = required.difference(ecosystems)
    if missing:
        raise CIConfigurationError(
            f"Dependabot is missing ecosystems: {', '.join(sorted(missing))}"
        )
    return tuple(sorted(ecosystems))


def validate_ci_configuration(
    project_root: Path = _PROJECT_ROOT,
) -> CIConfigurationReport:
    root = project_root.resolve()
    workflow_path = root / _WORKFLOW_RELATIVE_PATH
    dependabot_path = root / _DEPENDABOT_RELATIVE_PATH
    workflow_text = _read_text(workflow_path)
    dependabot_text = _read_text(dependabot_path)

    if "${{ secrets." in workflow_text:
        raise CIConfigurationError("CI workflow must not read repository secrets")
    if "pull_request_target" in workflow_text:
        raise CIConfigurationError("pull_request_target is forbidden")

    workflow = _load_mapping(workflow_path, workflow_text)
    dependabot = _load_mapping(dependabot_path, dependabot_text)
    _validate_triggers(workflow)
    _validate_permissions(workflow)
    _validate_concurrency(workflow)
    jobs = _mapping(workflow.get("jobs"), label="jobs")
    _validate_jobs(jobs)
    pins = _collect_action_pins(jobs)
    ecosystems = _validate_dependabot(dependabot)

    return CIConfigurationReport(
        workflow_sha256=hashlib.sha256(workflow_text.encode("utf-8")).hexdigest(),
        dependabot_sha256=hashlib.sha256(dependabot_text.encode("utf-8")).hexdigest(),
        jobs=tuple(sorted(str(job) for job in jobs)),
        action_pins=pins,
        dependency_ecosystems=ecosystems,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the Day 18 GitHub Actions and Dependabot contract."
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=_PROJECT_ROOT,
        help="Project root containing .github (defaults to this repository).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        report = validate_ci_configuration(args.project_root)
    except CIConfigurationError as exc:
        print(
            json.dumps(
                {
                    "passed": False,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    print(
        json.dumps(
            {"passed": True, **report.to_dict()},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
