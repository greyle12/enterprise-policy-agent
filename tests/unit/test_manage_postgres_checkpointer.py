from argparse import Namespace

from scripts import manage_postgres_checkpointer


def test_cli_prints_step4_status_without_exposing_dsn(monkeypatch, capsys) -> None:
    dsn = "postgresql://agent:super-secret@localhost/agent_test"
    monkeypatch.setenv("AGENT_POSTGRES_DSN", dsn)
    monkeypatch.setattr(
        manage_postgres_checkpointer,
        "_run_with_compatible_loop",
        lambda args: {
            "schema_version": "1.0",
            "phase": 38,
            "step": 4,
            "action": args.action,
            "passed": True,
        },
    )

    exit_code = manage_postgres_checkpointer.main(["status"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert '"step": 4' in output
    assert dsn not in output
    assert "super-secret" not in output


def test_cli_redacts_dsn_from_errors(monkeypatch, capsys) -> None:
    dsn = "postgresql://agent:super-secret@localhost/agent_test"
    monkeypatch.setenv("AGENT_POSTGRES_DSN", dsn)

    def fail(args: Namespace):
        raise RuntimeError(f"could not connect to {dsn}")

    monkeypatch.setattr(manage_postgres_checkpointer, "_run_with_compatible_loop", fail)

    exit_code = manage_postgres_checkpointer.main(["setup"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "<redacted-dsn>" in output
    assert dsn not in output
    assert "super-secret" not in output
