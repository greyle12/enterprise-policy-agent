"""Verify v2 freeze and pending draft, without running any query."""

import hashlib
import json
from pathlib import Path


def main():
    root = Path("artifacts/gate-v2-unseen-v1")
    lock = json.loads((root / "draft-lock.json").read_text(encoding="utf-8"))
    freeze = json.loads((root / "freeze.json").read_text(encoding="utf-8"))
    files = {
        **freeze["files"],
        str(root / "freeze.json"): lock["freeze_sha256"],
        str(root / "review-draft.json"): lock["draft_sha256"],
    }
    errors = [
        p
        for p, h in files.items()
        if not Path(p).exists() or hashlib.sha256(Path(p).read_bytes()).hexdigest() != h
    ]
    draft = json.loads((root / "review-draft.json").read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "passed": not errors,
                "mismatches": errors,
                "status": draft["status"],
                "n": len(draft["cases"]),
                "inference_executed": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
