"""Bounded real-model experiment; preserve every response and failed case."""

import hashlib
import json
import os
import platform
import subprocess
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from dotenv import dotenv_values
from openai import OpenAI

from scripts.evaluate_semantic_request_facets import load_prediction_dataset


def main():
    config = {**dotenv_values(".env"), **os.environ}
    max_tokens = int(config.get("FACET_MAX_TOKENS", "1024"))
    if max_tokens <= 0:
        raise ValueError("FACET_MAX_TOKENS must be greater than zero")
    client = OpenAI(
        api_key=config["LLM_API_KEY"],
        base_url="https://api.deepseek.com",
        timeout=30,
        max_retries=0,
    )
    source = Path(config.get("FACET_RUN_INPUT", "artifacts/semantic-facet-requests-v1"))
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    for filename, key in (("requests.jsonl", "requests_sha256"), ("prompt.txt", "prompt_sha256")):
        if hashlib.sha256((source / filename).read_bytes()).hexdigest() != manifest[key]:
            raise ValueError("Frozen input hash mismatch")
    output = Path(os.environ.get("FACET_RUN_OUTPUT", "artifacts/semantic-facet-real-v1"))
    output.mkdir(exist_ok=False)
    provenance = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "code_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "working_tree_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
        ),
        "input_path": str(source),
        "input_manifest": manifest,
        "python": platform.python_version(),
        "packages": {name: version(name) for name in ("openai", "httpx", "python-dotenv")},
        "model_id": config.get("LLM_MODEL", "deepseek-v4-flash"),
        "model_revision": "unavailable-provider-alias",
        "endpoint": "https://api.deepseek.com",
        "temperature": 0,
        "max_tokens": max_tokens,
        "timeout": 30,
        "max_retries": 0,
        "independent_test_set": False,
        "numeric_resume_ready": False,
    }
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    prompt = (source / "prompt.txt").read_text(encoding="utf-8")
    results, predictions = [], []
    for i, line in enumerate((source / "requests.jsonl").read_text(encoding="utf-8").splitlines()):
        row = json.loads(line)
        entry = {"case_id": row["case_id"], "status": "failed"}
        start = time.monotonic()
        try:
            response = client.chat.completions.create(
                model=config.get("LLM_MODEL", "deepseek-v4-flash"),
                temperature=0,
                max_tokens=max_tokens,
                messages=[
                    {
                        "role": "system",
                        "content": prompt,
                    },
                    {"role": "user", "content": line},
                ],
            )
            (output / f"response-{i}.json").write_text(
                response.model_dump_json(indent=2), encoding="utf-8"
            )
            content = response.choices[0].message.content or ""
            candidate = output / f"candidate-{i}.jsonl"
            candidate.write_text(content, encoding="utf-8")
            parsed = load_prediction_dataset(candidate)
            if len(parsed.records) != 1 or parsed.records[0].case_id != row["case_id"]:
                raise ValueError("alignment")
            predictions.append(content.strip())
            entry.update(status="passed", returned_model=response.model)
        except Exception as exc:
            entry["error_type"] = type(exc).__name__
        entry["seconds"] = time.monotonic() - start
        results.append(entry)
        (output / "run.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(json.dumps(entry), flush=True)
    (output / "predictions.jsonl").write_text("\n".join(predictions) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
