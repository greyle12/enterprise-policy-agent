from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from app.evaluation.models import GOLDEN_CASE_ADAPTER, GoldenCase


class GoldenDatasetError(ValueError):
    """黄金集文件无法安全解析或不满足契约。"""


@dataclass(frozen=True, slots=True)
class GoldenDataset:
    """已校验的黄金用例及原始文件摘要。"""

    path: Path
    sha256: str
    cases: tuple[GoldenCase, ...]


def load_golden_dataset(path: str | Path) -> GoldenDataset:
    """逐行加载 JSONL，用例异常时给出精确行号。"""

    dataset_path = Path(path)
    try:
        raw_bytes = dataset_path.read_bytes()
    except OSError as exc:
        raise GoldenDatasetError(
            f"cannot read golden dataset: {dataset_path}"
        ) from exc

    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise GoldenDatasetError(
            "golden dataset must be UTF-8 encoded"
        ) from exc

    cases: list[GoldenCase] = []
    seen_case_ids: set[str] = set()

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GoldenDatasetError(
                "invalid JSON in golden dataset at "
                f"line {line_number}: {exc.msg}"
            ) from exc

        try:
            case = GOLDEN_CASE_ADAPTER.validate_python(payload)
        except ValidationError as exc:
            raise GoldenDatasetError(
                "invalid golden case at "
                f"line {line_number}: {exc}"
            ) from exc

        if case.case_id in seen_case_ids:
            raise GoldenDatasetError(
                "duplicate golden case_id at "
                f"line {line_number}: {case.case_id}"
            )

        seen_case_ids.add(case.case_id)
        cases.append(case)

    if not cases:
        raise GoldenDatasetError("golden dataset must contain at least one case")

    return GoldenDataset(
        path=dataset_path,
        sha256=sha256(raw_bytes).hexdigest(),
        cases=tuple(cases),
    )
