from __future__ import annotations

import json
from pathlib import Path

from app.performance import (
    render_concurrency_load_markdown,
    run_offline_concurrency_load,
    write_concurrency_load_report,
)


async def test_concurrency_report_writes_json_and_markdown(tmp_path: Path) -> None:
    report = await run_offline_concurrency_load(
        request_count=6,
        concurrency=3,
        provider_latency_ms=1.0,
    )

    paths = write_concurrency_load_report(report, tmp_path)
    payload = json.loads(paths.json_path.read_text(encoding="utf-8"))
    markdown = paths.markdown_path.read_text(encoding="utf-8")

    assert payload["quality_gate_passed"] is True
    assert len(payload["scenario_results"]) == 3
    assert "完全离线" in markdown
    assert "不能当作真实 Provider SLA" in markdown
    assert "上游调用" in markdown
    assert "放大率" in markdown
    assert render_concurrency_load_markdown(report) == markdown
