from pathlib import Path
from types import SimpleNamespace

from app.core.config import Settings
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
        lambda argv=None: SimpleNamespace(policy_directory=Path("data/policies"), collection=None),
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


def test_collection_override_builds_physical_snapshot_without_release_alias(
    monkeypatch, capsys
) -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        rag_vector_store_provider=VectorStoreProviderName.MEMORY,
        rag_pgvector_release_alias="enterprise-policy",
    )
    captured: dict[str, object] = {}
    report = SimpleNamespace(
        to_dict=lambda: {
            "snapshot_sha256": "a" * 64,
            "changed": True,
        }
    )
    monkeypatch.setattr(indexing_script, "get_settings", lambda: settings)

    def fake_index(policy_directory, *, settings):
        captured["collection"] = settings.rag_pgvector_collection
        captured["release_alias"] = settings.rag_pgvector_release_alias
        return report

    monkeypatch.setattr(indexing_script, "index_policy_documents", fake_index)

    indexing_script.main(["--collection", "policy-green"])

    assert captured == {"collection": "policy-green", "release_alias": None}
    assert '"snapshot_sha256"' in capsys.readouterr().out


def test_pgvector_collection_build_reports_released_fencing_lease(monkeypatch, capsys) -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        rag_vector_store_provider=VectorStoreProviderName.PGVECTOR,
    )
    report = SimpleNamespace(to_dict=lambda: {"snapshot_sha256": "a" * 64})
    captured: dict[str, object] = {}
    monkeypatch.setattr(indexing_script, "get_settings", lambda: settings)

    def fake_leased_index(policy_directory, **kwargs):
        captured.update(kwargs)
        return report, {
            "collection_name": "policy-green",
            "owner_id": "builder-a",
            "fencing_token": 7,
            "released": True,
        }

    monkeypatch.setattr(
        indexing_script,
        "_index_pgvector_collection_with_lease",
        fake_leased_index,
    )

    exit_code = indexing_script.main(
        [
            "--collection",
            "policy-green",
            "--lease-owner",
            "builder-a",
            "--lease-ttl-seconds",
            "30",
            "--lease-renew-interval-seconds",
            "10",
        ]
    )

    payload = capsys.readouterr().out
    assert exit_code == 0
    assert captured["owner_id"] == "builder-a"
    assert '"phase": 36' in payload
    assert '"fencing_token": 7' in payload
    assert '"released": true' in payload


def test_pgvector_indexing_requires_explicit_green_collection(monkeypatch, capsys) -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        rag_vector_store_provider=VectorStoreProviderName.PGVECTOR,
    )
    monkeypatch.setattr(indexing_script, "get_settings", lambda: settings)

    assert indexing_script.main([]) == 2
    assert "explicit --collection Green target" in capsys.readouterr().err
