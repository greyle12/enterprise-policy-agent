import json

from app.performance import (
    render_batch_optimization_markdown,
    run_offline_batch_optimization,
    write_batch_optimization_report,
)


def test_render_batch_report_explains_results_and_boundary() -> None:
    report = run_offline_batch_optimization(
        item_count=4,
        batch_size=2,
        call_overhead_ms=0.2,
        batch_latency_ms=0.05,
    )

    markdown = render_batch_optimization_markdown(report)

    assert "Embedding/Reranker 批处理报告" in markdown
    assert "`embedding_documents`" in markdown
    assert "`reranker_candidates`" in markdown
    assert "完全离线" in markdown
    assert "不能作为真实模型 SLA" in markdown


def test_write_batch_report_creates_json_and_markdown(tmp_path) -> None:
    report = run_offline_batch_optimization(
        item_count=4,
        batch_size=2,
        call_overhead_ms=0.2,
        batch_latency_ms=0.05,
    )

    paths = write_batch_optimization_report(report, tmp_path)

    payload = json.loads(paths.json_path.read_text(encoding="utf-8"))
    assert payload["suite_name"] == "enterprise_policy_agent_offline_batch_optimization"
    assert payload["quality_gate_passed"] is True
    assert paths.markdown_path.name == "agent-batch-optimization-report.md"
