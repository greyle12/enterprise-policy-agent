from __future__ import annotations

from scripts import run_retrieval_candidate_sweep


def test_offline_candidate_sweep_writes_reports(tmp_path, capsys) -> None:
    exit_code = run_retrieval_candidate_sweep.main(
        [
            "--mode",
            "offline",
            "--candidate-k",
            "5",
            "20",
            "--default-candidate-k",
            "20",
            "--warmups",
            "0",
            "--repetitions",
            "1",
            "--output-dir",
            str(tmp_path),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"quality_gate_passed": true' in output
    assert '"ndcg_at_5"' in output
    assert (tmp_path / "retrieval-candidate-sweep-report.json").is_file()
    assert (tmp_path / "retrieval-candidate-sweep-report.md").is_file()


def test_invalid_window_is_rejected_before_model_runtime_build(monkeypatch, capsys) -> None:
    runtime_built = False

    def unexpected_runtime_build(**kwargs):
        nonlocal runtime_built
        runtime_built = True
        raise AssertionError("runtime must not be built")

    monkeypatch.setattr(
        run_retrieval_candidate_sweep,
        "build_retrieval_evaluation_runtime",
        unexpected_runtime_build,
    )

    exit_code = run_retrieval_candidate_sweep.main(
        ["--candidate-k", "5", "--default-candidate-k", "20"]
    )

    assert exit_code == 2
    assert runtime_built is False
    assert "included" in capsys.readouterr().err


def test_bge_cli_exposes_device_and_independent_batch_sizes() -> None:
    args = run_retrieval_candidate_sweep._parse_args(
        [
            "--mode",
            "bge",
            "--device",
            "cuda",
            "--embedding-batch-size",
            "16",
            "--reranker-batch-size",
            "8",
        ]
    )

    assert args.mode == "bge"
    assert args.device == "cuda"
    assert args.embedding_batch_size == 16
    assert args.reranker_batch_size == 8
