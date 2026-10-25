"""Optional publication of sanitized local experiment outcomes (no dataset text)."""

import hashlib


def publish_workflow_experiment(report, client):
    """Caller explicitly opts in; exporter failures cannot change evaluation results."""
    data = [
        {
            "input": {"case_digest": hashlib.sha256(r["case_id"].encode()).hexdigest()},
            "expected_output": True,
            "metadata": {"passed": bool(r["passed"])},
        }
        for r in report["results"]
    ]

    def task(*, item, **kwargs):
        return item["metadata"]["passed"]

    def evaluator(*, output, **kwargs):
        from langfuse.experiment import Evaluation

        return Evaluation(name="deterministic_task_success", value=float(output))

    metadata = {
        k: hashlib.sha256(str(v).encode()).hexdigest() for k, v in report["manifest"].items()
    }
    try:
        client.run_experiment(
            name="workflow-evaluation-v1",
            data=data,
            task=task,
            evaluators=[evaluator],
            max_concurrency=1,
            metadata=metadata,
        )
        client.flush()
        return {"status": "export_requested", "server_receipt_verified": False}
    except Exception:
        return {"status": "export_failed", "server_receipt_verified": False}
