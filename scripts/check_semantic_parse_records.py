"""Offline JSONL checker for semantic parse contract records.

The checker only decodes and validates serialized records. It never invokes a
model, contacts an external service, or changes application/runtime state.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Sequence
from pathlib import Path

from scripts.semantic_parse_contract import (
    ContractDecodeError,
    SemanticParseRecord,
    ValidationIssue,
    validate_record,
)


def _issue_dict(
    *,
    line_number: int | None,
    code: str,
    path: str,
    message: str,
) -> dict[str, object]:
    return {
        "line": line_number,
        "code": code,
        "path": path,
        "message": message,
    }


def _validation_issue_dict(line_number: int, issue: ValidationIssue) -> dict[str, object]:
    return {
        "line": line_number,
        **issue.as_dict(),
    }


def check_records(lines: Iterable[str]) -> dict[str, object]:
    """Return a machine-readable validation summary for JSONL input."""

    errors: list[dict[str, object]] = []
    record_count = 0
    valid_record_count = 0
    invalid_record_count = 0
    records_with_missing_outputs = 0

    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            errors.append(
                _issue_dict(
                    line_number=line_number,
                    code="INVALID_JSON",
                    path=f"line[{line_number}]",
                    message=exc.msg,
                )
            )
            continue

        try:
            record = SemanticParseRecord.from_dict(payload, path=f"line[{line_number}]")
        except ContractDecodeError as exc:
            errors.append(
                _issue_dict(
                    line_number=line_number,
                    code="CONTRACT_DECODE_ERROR",
                    path=f"line[{line_number}]",
                    message=str(exc),
                )
            )
            continue

        record_count += 1
        if record.missing_outputs:
            records_with_missing_outputs += 1
        issues = validate_record(record)
        if issues:
            invalid_record_count += 1
            errors.extend(_validation_issue_dict(line_number, issue) for issue in issues)
        else:
            valid_record_count += 1

    if record_count == 0:
        errors.append(
            _issue_dict(
                line_number=None,
                code="NO_RECORDS",
                path="input",
                message="input did not contain a decodable semantic record",
            )
        )

    return {
        "status": "passed" if not errors else "failed",
        "schema_version": "1.0",
        "record_count": record_count,
        "valid_record_count": valid_record_count,
        "invalid_record_count": invalid_record_count,
        "records_with_missing_outputs": records_with_missing_outputs,
        "error_count": len(errors),
        "errors": errors,
    }


def check_file(path: Path) -> dict[str, object]:
    """Read one UTF-8 JSONL file and return its validation summary."""

    with path.open("r", encoding="utf-8-sig") as handle:
        return check_records(handle)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate semantic parse contract records in JSONL format."
    )
    parser.add_argument(
        "input",
        type=Path,
        help="UTF-8 JSONL file containing one serialized record per line",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        summary = check_file(args.input)
    except OSError as exc:
        summary = {
            "status": "failed",
            "schema_version": "1.0",
            "record_count": 0,
            "valid_record_count": 0,
            "invalid_record_count": 0,
            "records_with_missing_outputs": 0,
            "error_count": 1,
            "errors": [
                _issue_dict(
                    line_number=None,
                    code="INPUT_READ_ERROR",
                    path=str(args.input),
                    message=str(exc),
                )
            ],
        }

    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
