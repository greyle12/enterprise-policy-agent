"""Read-only preflight; never evaluates the pending challenge set."""

import hashlib
import json
from pathlib import Path


def main():
    root = Path("artifacts/coverage-gate-challenge-v1")
    lock = json.loads((root / "challenge-lock.json").read_text(encoding="utf-8"))
    freeze = json.loads((root / "gate-freeze.json").read_text(encoding="utf-8"))
    expected = dict(freeze["files"])
    expected[str(root / "review-draft.json")] = lock["draft_sha256"]
    expected[str(root / "gate-freeze.json")] = lock["gate_freeze_sha256"]
    mismatches = [
        p
        for p, sha in expected.items()
        if not Path(p).exists() or hashlib.sha256(Path(p).read_bytes()).hexdigest() != sha
    ]
    draft = json.loads((root / "review-draft.json").read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "passed": not mismatches,
                "mismatches": mismatches,
                "case_count": len(draft["cases"]),
                "status": draft["status"],
                "evaluation_authorized": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if mismatches:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
