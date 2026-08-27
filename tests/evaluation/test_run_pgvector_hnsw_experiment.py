from __future__ import annotations

import pytest

from scripts import run_pgvector_hnsw_experiment


def test_parses_hnsw_configuration_identity() -> None:
    configuration = run_pgvector_hnsw_experiment.parse_hnsw_configuration("16:64:40")

    assert configuration.identity == "m16-efc64-efs40"


@pytest.mark.parametrize("value", ["16:64", "16:x:40", "16:16:40"])
def test_rejects_invalid_hnsw_configuration(value: str) -> None:
    with pytest.raises(ValueError):
        run_pgvector_hnsw_experiment.parse_hnsw_configuration(value)


def test_cli_rejects_missing_default_before_opening_database(monkeypatch, capsys) -> None:
    database_opened = False

    def unexpected_database_open(*args, **kwargs):
        nonlocal database_opened
        database_opened = True
        raise AssertionError("database must not open")

    monkeypatch.setattr(
        run_pgvector_hnsw_experiment.PgVectorIndex,
        "from_dsn",
        unexpected_database_open,
    )

    exit_code = run_pgvector_hnsw_experiment.main(
        ["--hnsw-config", "8:32:20", "--default-config", "16:64:40"]
    )

    assert exit_code == 2
    assert database_opened is False
    assert "must be included" in capsys.readouterr().err


def test_cli_defaults_to_dedicated_non_production_collection() -> None:
    args = run_pgvector_hnsw_experiment._parse_args([])

    assert args.collection == "enterprise-policy-ann-experiment"
    assert args.mode == "offline"


def test_experiment_collection_isolates_mode_and_dimension() -> None:
    assert (
        run_pgvector_hnsw_experiment._experiment_collection(
            "enterprise-policy-ann-experiment",
            run_pgvector_hnsw_experiment.RetrievalEvaluationMode.BGE,
            512,
        )
        == "enterprise-policy-ann-experiment-bge-512d"
    )
