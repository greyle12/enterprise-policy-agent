"""Validate and join confirmed semantic request Facets offline."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from scripts.semantic_request_facets_adapter import (
    FacetAdapterError,
    load_semantic_request_facet_bundle,
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and join semantic records with the confirmed request Facet sidecar."
    )
    parser.add_argument("--records", required=True, type=Path, help="UTF-8 semantic records JSONL")
    parser.add_argument(
        "--accepted", required=True, type=Path, help="Confirmed accepted.json sidecar"
    )
    parser.add_argument("--confirmation", required=True, type=Path, help="User confirmation.json")
    parser.add_argument(
        "--manifest", required=True, type=Path, help="Facet provenance manifest.json"
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path("."),
        help="Project root used to resolve manifest-relative paths",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        bundle = load_semantic_request_facet_bundle(
            args.records,
            args.accepted,
            args.confirmation,
            manifest_path=args.manifest,
            project_root=args.project_root,
        )
        summary = bundle.summary()
        exit_code = 0
    except FacetAdapterError as exc:
        summary = {
            "status": "failed",
            "schema_version": "1.0",
            "record_count": 0,
            "facet_case_count": 0,
            "error_count": 1,
            "errors": [exc.as_dict()],
        }
        exit_code = 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
