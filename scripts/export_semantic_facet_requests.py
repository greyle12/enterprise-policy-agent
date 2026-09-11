"""Export label-free model requests from the verified development bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from scripts.semantic_request_facets_adapter import load_semantic_request_facet_bundle


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rooted_path(root: Path, path: Path | None, default: Path) -> Path:
    candidate = default if path is None else path
    return (root / candidate if not candidate.is_absolute() else candidate).resolve()


def export_requests(
    root: Path,
    output: Path,
    *,
    records_path: Path | None = None,
    accepted_path: Path | None = None,
    confirmation_path: Path | None = None,
    manifest_path: Path | None = None,
    prompt_path: Path | None = None,
) -> dict:
    root = root.resolve()
    source = _rooted_path(
        root, records_path, Path("docs/gate_v3/semantic-dev-v1-confirmed/records.jsonl")
    )
    confirmed = _rooted_path(
        root, accepted_path, Path("docs/gate_v3/semantic-request-facets-v1-confirmed/accepted.json")
    )
    confirmation = _rooted_path(
        root,
        confirmation_path,
        Path("docs/gate_v3/semantic-request-facets-v1-confirmed/confirmation.json"),
    )
    source_manifest = _rooted_path(
        root,
        manifest_path,
        Path("docs/gate_v3/semantic-request-facets-v1-confirmed/manifest.json"),
    )
    prompt = _rooted_path(root, prompt_path, Path("docs/gate_v3/semantic-facet-prompt-v1.txt"))
    bundle = load_semantic_request_facet_bundle(
        source,
        confirmed,
        confirmation,
        manifest_path=source_manifest,
        project_root=root,
    )
    # Explicit allowlist: never serialize the annotated case or semantic record.
    rows = [{"case_id": case.case_id, "query": case.query} for case in bundle.cases]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = bool(
        subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=root,
            text=True,
        ).strip()
    )
    output.mkdir(parents=True, exist_ok=False)
    requests = output / "requests.jsonl"
    requests.write_bytes(
        (
            "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n"
        ).encode("utf-8")
    )
    (output / "prompt.txt").write_bytes(prompt.read_bytes())
    manifest = {
        "schema_version": "1.0",
        "prompt_version": "semantic-facet-prompt-v1",
        "dataset_version": bundle.dataset_version,
        "source_dataset_version": bundle.source_dataset_version,
        "sample_count": len(rows),
        "case_ids": [row["case_id"] for row in rows],
        "source_records_sha256": digest(source),
        "source_records_path": source.relative_to(root).as_posix(),
        "accepted_sha256": digest(confirmed),
        "accepted_path": confirmed.relative_to(root).as_posix(),
        "confirmation_sha256": digest(confirmation),
        "confirmation_path": confirmation.relative_to(root).as_posix(),
        "source_manifest_path": source_manifest.relative_to(root).as_posix(),
        "requests_sha256": digest(requests),
        "prompt_sha256": digest(prompt),
        "exporter_sha256": digest(Path(__file__)),
        "code_head": head,
        "working_tree_dirty": dirty,
        "manifest_hashes_verified": bundle.manifest_hashes_verified,
        "model_visible_files": ["prompt.txt", "requests.jsonl"],
        "model_inference": False,
        "model_id": None,
        "model_revision": None,
        "independent_test_set": False,
        "numeric_resume_ready": False,
    }
    (output / "manifest.json").write_bytes(
        (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--records", type=Path)
    parser.add_argument("--accepted", type=Path)
    parser.add_argument("--confirmation", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--prompt", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            export_requests(
                args.project_root,
                args.output,
                records_path=args.records,
                accepted_path=args.accepted,
                confirmation_path=args.confirmation,
                manifest_path=args.manifest,
                prompt_path=args.prompt,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
