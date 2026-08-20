from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.config import Settings, get_settings
from app.rag.embeddings import (
    BGEEmbeddingProvider,
    DEFAULT_BGE_MODEL_NAME,
    EmbeddingProvider,
)
from app.rag.indexing import DocumentIndexingReport, PolicyDocumentIndexer
from app.rag.vector_index import VectorIndex
from app.rag.vector_store import build_policy_vector_index

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"


def index_policy_documents(
    policy_directory: Path,
    *,
    settings: Settings | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    vector_index: VectorIndex | None = None,
) -> DocumentIndexingReport:
    """Run one index synchronization and close only resources created here."""

    active_settings = settings or get_settings()
    provider = embedding_provider or BGEEmbeddingProvider(
        model_name=DEFAULT_BGE_MODEL_NAME,
    )
    index = vector_index or build_policy_vector_index(
        active_settings,
        dimension=provider.dimension,
    )
    owns_index = vector_index is None
    try:
        return (
            PolicyDocumentIndexer(
                embedding_provider=provider,
                vector_index=index,
                embedding_identity=DEFAULT_BGE_MODEL_NAME,
                pipeline_version=active_settings.rag_index_pipeline_version,
            )
            .synchronize_directory(policy_directory)
            .report
        )
    finally:
        if owns_index:
            index.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Incrementally synchronize enterprise policy chunks into Vector Store."
    )
    parser.add_argument(
        "--policy-directory",
        type=Path,
        default=_DEFAULT_POLICY_DIRECTORY,
        help="Directory containing supported policy documents.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    settings = get_settings()
    report = index_policy_documents(
        args.policy_directory,
        settings=settings,
    )
    payload = {
        "schema_version": "1.0",
        "phase": 30,
        "passed": True,
        "vector_store_provider": settings.rag_vector_store_provider.value,
        **report.to_dict(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
