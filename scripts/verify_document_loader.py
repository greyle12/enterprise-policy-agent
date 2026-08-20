from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from app.rag.document_loader import DEFAULT_DOCUMENT_LOADER_REGISTRY
from app.rag.policy_chunker import chunk_policy_directory
from app.rag.policy_parser import parse_policy_directory

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
_EXPECTED_DOCUMENT_COUNT = 5
_EXPECTED_CHUNK_COUNT = 199


def run_verification(policy_directory: Path) -> dict[str, object]:
    """Verify the Phase 22 loader boundary without network or model calls."""

    discovered = DEFAULT_DOCUMENT_LOADER_REGISTRY.discover(policy_directory)
    loaded = [DEFAULT_DOCUMENT_LOADER_REGISTRY.load(path) for path in discovered]
    documents = parse_policy_directory(
        policy_directory,
        loader_registry=DEFAULT_DOCUMENT_LOADER_REGISTRY,
    )
    chunks = chunk_policy_directory(
        policy_directory,
        loader_registry=DEFAULT_DOCUMENT_LOADER_REGISTRY,
    )

    checks = {
        "markdown_loader_registered": (
            ".md" in DEFAULT_DOCUMENT_LOADER_REGISTRY.supported_extensions
        ),
        "pdf_loader_registered": (".pdf" in DEFAULT_DOCUMENT_LOADER_REGISTRY.supported_extensions),
        "all_policy_files_discovered": len(discovered) == _EXPECTED_DOCUMENT_COUNT,
        "all_files_loaded_as_markdown": all(
            item.loader_name == "markdown" and item.media_type == "text/markdown" for item in loaded
        ),
        "parser_contract_preserved": len(documents) == _EXPECTED_DOCUMENT_COUNT,
        "chunker_contract_preserved": len(chunks) == _EXPECTED_CHUNK_COUNT,
        "stable_source_paths": [item.source_path for item in loaded] == discovered,
        "unique_document_ids": (
            len({document.document_id for document in documents}) == len(documents)
        ),
        "unique_chunk_ids": len({chunk.chunk_id for chunk in chunks}) == len(chunks),
    }

    return {
        "schema_version": "1.0",
        "phase": 22,
        "passed": all(checks.values()),
        "supported_extensions": list(DEFAULT_DOCUMENT_LOADER_REGISTRY.supported_extensions),
        "document_count": len(documents),
        "chunk_count": len(chunks),
        "network_calls": False,
        "model_calls": False,
        "checks": checks,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify the Phase 22 document-loader abstraction.")
    parser.add_argument(
        "--policy-directory",
        type=Path,
        default=_DEFAULT_POLICY_DIRECTORY,
        help="Policy directory to load (defaults to data/policies).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        report = run_verification(args.policy_directory)
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "phase": 22,
                    "passed": False,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
