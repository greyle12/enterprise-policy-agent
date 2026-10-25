"""Correlate offline workflow captures with real SDK spans; never exports externally."""

import argparse
import asyncio
import hashlib
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4

from app.evaluation.failure_pipeline import failure_cards
from app.observability import tracing
from scripts.run_agent_workflow_capture import run as capture_run
from scripts.verify_optional_tracing import span_manifest


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TurnTraceIndex:
    """Sequential capture callback; local execution IDs are never sent to the SDK."""

    def __init__(self, exporter):
        self.exporter = exporter
        self.position = 0
        self.seen = set()
        self.rows = []
        self.spans = []

    def __call__(self, captures, evaluations):
        capture = captures[-1]
        turn = capture["turns"][-1]
        key = (capture["case_id"], capture["execution_id"], turn["turn"])
        if key in self.seen:
            return
        self.seen.add(key)
        row = {
            "case_id": key[0],
            "execution_id": key[1],
            "turn": key[2],
            "trace_id": None,
            "root_span_id": None,
            "span_ids": [],
            "status": "missing",
        }
        try:
            finished = self.exporter.get_finished_spans()
            current = finished[self.position :]
            self.position = len(finished)
            spans = span_manifest(current)
            roots = [s for s in spans if s["name"] == "agent.request"]
            ids = {s["span_id"] for s in spans}
            existing_traces = {r["trace_id"] for r in self.rows if r["trace_id"]}
            reachable = {roots[0]["span_id"]} if len(roots) == 1 else set()
            while (
                children := {s["span_id"] for s in spans if s["parent_span_id"] in reachable}
                - reachable
            ):
                reachable.update(children)
            valid = (
                len(roots) == 1
                and len(ids) == len(spans)
                and len({s["trace_id"] for s in spans}) == 1
                and roots[0]["trace_id"] not in existing_traces
                and roots[0]["parent_span_id"] not in ids
                and reachable == ids
                and all(s["parent_span_id"] in ids for s in spans if s not in roots)
                and roots[0]["metadata"].get("final_status")
                == ("error_or_interrupt" if turn["error_type"] else "ok")
            )
            row.update(
                trace_id=roots[0]["trace_id"] if len(roots) == 1 else None,
                root_span_id=roots[0]["span_id"] if len(roots) == 1 else None,
                span_ids=sorted(ids),
                status="complete" if valid else "invalid",
            )
            self.spans.extend(spans)
        except Exception:
            # A broken recorder must not change workflow execution or persist error bodies.
            row["status"] = "missing_or_invalid"
        self.rows.append(row)


def linked_cards(report, index, capture_hash, index_hash):
    cards = failure_cards(report["workflow_results"])
    for card in cards:
        matches = [
            row
            for row in index
            if row["case_id"] == card["case_id"] and row["execution_id"] == card["execution_id"]
        ]
        expected_turns = {
            turn["turn"]
            for capture in report["results"]
            if capture["case_id"] == card["case_id"]
            and capture["execution_id"] == card["execution_id"]
            for turn in capture["turns"]
        }
        card.update(
            trace_ids=sorted({r["trace_id"] for r in matches if r["trace_id"]}),
            trace_link_status="complete"
            if expected_turns
            and len(matches) == len(expected_turns)
            and {r["turn"] for r in matches} == expected_turns
            and all(r["status"] == "complete" for r in matches)
            else "incomplete",
            source_capture_sha256=capture_hash,
            trace_index_sha256=index_hash,
        )
    return cards


async def run(output):
    if output.exists():
        raise FileExistsError(output)
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    output.mkdir(parents=True)
    exporter = InMemorySpanExporter()
    provider = tracing.metadata_only_provider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    backend = tracing.LangfuseBackend.__new__(tracing.LangfuseBackend)
    backend.client = tracing.make_langfuse_client(
        public_key="pk-workflow-local-" + uuid4().hex,
        secret_key="sk-synthetic-local-only",
        base_url="http://127.0.0.1:1",
        tracer_provider=provider,
        span_exporter=InMemorySpanExporter(),
        debug=False,
    )
    saved = tracing._backend, tracing._initialized, os.environ.get("AGENT_TRACING_ENABLED")
    index = TurnTraceIndex(exporter)
    recorder_ok = True
    try:
        tracing._backend, tracing._initialized = backend, True
        os.environ["AGENT_TRACING_ENABLED"] = "true"
        workflow = await capture_run(output / "workflow", on_snapshot=index)
        try:
            backend.client.flush()
        except Exception:
            recorder_ok = False
    finally:
        try:
            backend.client.shutdown()
        except Exception:
            recorder_ok = False
        finally:
            tracing._backend, tracing._initialized, enabled = saved
            if enabled is None:
                os.environ.pop("AGENT_TRACING_ENABLED", None)
            else:
                os.environ["AGENT_TRACING_ENABLED"] = enabled
    report_path = output / "workflow/capture-report.json"
    report = json.loads(report_path.read_text("utf-8"))
    expected = {
        (case["case_id"], case["execution_id"], turn["turn"])
        for case in report["results"]
        for turn in case["turns"]
    }
    actual = {(r["case_id"], r["execution_id"], r["turn"]) for r in index.rows}
    index_data = {
        "source_capture_sha256": sha(report_path),
        "scope": "local_only",
        "turns": index.rows,
    }
    write_json(output / "trace-index.json", index_data)
    write_json(output / "trace-spans.json", {"scope": "in_memory_only", "spans": index.spans})
    cards = linked_cards(report, index.rows, sha(report_path), sha(output / "trace-index.json"))
    (output / "trace-linked-pending-review.jsonl").write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cards), "utf-8"
    )
    complete = sum(r["status"] == "complete" for r in index.rows)
    trace_ok = recorder_ok and actual == expected and complete == len(expected)
    summary = {
        **workflow,
        "trace_turns": len(index.rows),
        "complete_trace_turns": complete,
        "spans": len(index.spans),
        "trace_gate_passed": trace_ok,
        "recorder_status": "ok" if recorder_ok else "failed",
        "engineering_gate_passed": workflow["workflow_gate_passed"] and trace_ok,
        "external_publication": False,
        "server_receipt_verified": False,
        "semantic_judge_calls": 0,
        "quality_conclusion": None,
        "human_review_decisions_added": 0,
        "pending_failure_cards": len(cards),
    }
    write_json(output / "summary.json", summary)
    suite = ET.Element("testsuite", name="offline_workflow_trace_correlation")
    for result in report["workflow_results"]:
        rows = [r for r in index.rows if r["case_id"] == result["case_id"]]
        case_expected = {key for key in expected if key[0] == result["case_id"]}
        case_actual = {(r["case_id"], r["execution_id"], r["turn"]) for r in rows}
        case = ET.SubElement(
            suite, "testcase", name=result["case_id"], classname="offline_integration"
        )
        if (
            not recorder_ok
            or not result["passed"]
            or case_actual != case_expected
            or any(r["status"] != "complete" for r in rows)
        ):
            ET.SubElement(case, "failure", message="workflow or trace correlation failed")
    suite.set("tests", str(len(suite)))
    suite.set("failures", str(len(suite.findall("testcase/failure"))))
    suite.set("errors", "0")
    suite.set("skipped", "0")
    ET.ElementTree(suite).write(output / "trace-junit.xml", encoding="utf-8", xml_declaration=True)
    write_json(
        output / "delivery-manifest.json",
        {
            p.relative_to(output).as_posix(): sha(p)
            for p in sorted(output.rglob("*"))
            if p.is_file()
        },
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    summary = asyncio.run(run(args.output_dir))
    print(json.dumps(summary, indent=2))
    return 0 if summary["engineering_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
