from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from app.evaluation.retrieval_models import RetrievalCase


class RetrievalDatasetError(ValueError):
    """A retrieval judgment dataset cannot be read or violates its contract."""


@dataclass(frozen=True, slots=True)
class RetrievalDataset:
    """Validated JSONL retrieval cases plus the exact input digest."""

    path: Path
    sha256: str
    cases: tuple[RetrievalCase, ...]


def load_retrieval_dataset(path: str | Path) -> RetrievalDataset:
    """Load UTF-8 JSONL while preserving line-specific validation failures."""

    dataset_path = Path(path)
    try:
        raw_bytes = dataset_path.read_bytes()
    except OSError as exc:
        raise RetrievalDatasetError(f"cannot read retrieval dataset: {dataset_path}") from exc

    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RetrievalDatasetError("retrieval dataset must be UTF-8 encoded") from exc

    cases: list[RetrievalCase] = []
    seen_case_ids: set[str] = set()
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RetrievalDatasetError(
                f"invalid JSON in retrieval dataset at line {line_number}: {exc.msg}"
            ) from exc
        try:
            case = RetrievalCase.model_validate(payload)
        except ValidationError as exc:
            raise RetrievalDatasetError(
                f"invalid retrieval case at line {line_number}: {exc}"
            ) from exc
        if case.case_id in seen_case_ids:
            raise RetrievalDatasetError(
                f"duplicate retrieval case_id at line {line_number}: {case.case_id}"
            )
        seen_case_ids.add(case.case_id)
        cases.append(case)

    if not cases:
        raise RetrievalDatasetError("retrieval dataset must contain at least one case")
    return RetrievalDataset(
        path=dataset_path,
        sha256=sha256(raw_bytes).hexdigest(),
        cases=tuple(cases),
    )
