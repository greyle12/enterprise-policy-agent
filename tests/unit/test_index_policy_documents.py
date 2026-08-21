from pathlib import Path
from types import SimpleNamespace

from app.rag.vector_index import InMemoryVectorIndex, VectorStoreProviderName
from scripts import index_policy_documents as indexing_script


class _EmbeddingProvider:
    @property
    def dimension(self) -> int:
        return 2

    def embed_documents(self, texts) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]


def test_index_policy_documents_supports_injected_offline_boundaries() -> None:
    index = InMemoryVectorIndex(dimension=2)
    settings = SimpleNamespace(rag_index_pipeline_version="test-index-v1")

    report = indexing_script.index_policy_documents(
        Path("data/policies"),
        settings=settings,
        embedding_provider=_EmbeddingProvider(),
        vector_index=index,
    )

    assert report.changed is True
    assert report.total_chunk_count == index.size
    assert report.upserted_chunk_count == index.size


def test_main_prints_machine_readable_phase_report(monkeypatch, capsys) -> None:
    settings = SimpleNamespace(
        rag_vector_store_provider=VectorStoreProviderName.MEMORY,
    )
    report = SimpleNamespace(
        to_dict=lambda: {
            "changed": False,
            "upserted_chunk_count": 0,
        }
    )
    monkeypatch.setattr(
        indexing_script,
        "_parse_args",
        lambda: SimpleNamespace(policy_directory=Path("data/policies")),
    )
    monkeypatch.setattr(indexing_script, "get_settings", lambda: settings)
    monkeypatch.setattr(
        indexing_script,
        "index_policy_documents",
        lambda policy_directory, *, settings: report,
    )

    indexing_script.main()

    output = capsys.readouterr().out
    assert '"phase": 30' in output
    assert '"passed": true' in output
    assert '"vector_store_provider": "memory"' in output
