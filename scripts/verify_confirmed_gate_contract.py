"""Validate the user-confirmed decision contract without evaluating any gate."""

import hashlib
import json
from pathlib import Path


def validate(root=Path("artifacts/gate-decision-contract-v1")):
    def read(name):
        return json.loads((root / name).read_text(encoding="utf-8"))

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    c = read("user-confirmation.json")
    lock = read("confirmed-lock.json")
    checks = {
        "confirmation": digest(root / "user-confirmation.json") == lock["confirmation_sha256"],
        "contract": digest(root / "CONTRACT.md") == c["contract_sha256"] == lock["contract_sha256"],
        "review": digest(root / "review.json") == c["review_sha256"] == lock["review_sha256"],
        "manifest": digest(root / "manifest.json") == c["manifest_sha256"],
        "confirmed": c["status"] == "user_confirmed",
    }
    for name, sha in read("manifest.json")["files"].items():
        checks[name] = digest(Path(name)) == sha
    rows = read("review.json")["rows"]
    counts = {
        v: sum(r["proposed_decision"] == v for r in rows)
        for v in ["ALLOW", "DENY", "NOT_APPLICABLE", "UNRESOLVED"]
    }
    checks["counts"] = counts == {"ALLOW": 11, "DENY": 8, "NOT_APPLICABLE": 1, "UNRESOLVED": 0}
    checks["unique_ids"] = len({r["case_id"] for r in rows}) == 20
    return {
        "passed": all(checks.values()),
        "status": "user_confirmed" if all(checks.values()) else "blocked",
        "counts": counts,
        "failed_checks": [k for k, v in checks.items() if not v],
        "new_gate_implemented": False,
        "new_evaluation_executed": False,
    }


def main():
    try:
        result = validate()
    except (OSError, KeyError, ValueError) as exc:
        result = {"passed": False, "status": "blocked", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
