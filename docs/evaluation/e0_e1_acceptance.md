# E0/E1 acceptance record — 2026-10-23

E0 baseline/audit and E1 optional tracing implemented. E2–E5 remain plans. Local engineering acceptance passed for the focused scope; whole-repository green CI and remote Langfuse ingestion are not claimed.

## Commands and results

All commands used `.venv/Scripts/python.exe` (Python 3.12.10). Evidence: `artifacts/evaluation-e0-e1-20261023/`.

| Command after interpreter | Result |
|---|---|
| `-m scripts.run_golden_evaluation --mode offline --output-dir artifacts/evaluation-e0-e1-20261023/baseline-golden` | Before: 30/30; gate passed |
| `-m scripts.run_retrieval_evaluation --mode offline --output-dir artifacts/evaluation-e0-e1-20261023/baseline-retrieval` | Before: 20 cases, 199 chunks; gate passed |
| `-m pytest tests/unit/test_agent_router.py tests/unit/test_policy_retriever.py tests/unit/test_policy_hybrid_retriever.py tests/unit/test_policy_reranked_retriever.py -q --basetemp=.pytest_tmp_e0_target --junitxml=artifacts/evaluation-e0-e1-20261023/baseline-target.xml` | Before: 36 passed |
| `-m pytest tests/unit/test_optional_tracing.py -q --basetemp=.pytest_tmp_e1_final --junitxml=artifacts/evaluation-e0-e1-20261023/tracing-tests.xml` | Final tracing suite: 11 passed |
| Focused regression command below | 184 passed, 7 skipped; one existing Starlette/httpx deprecation warning |
| Golden/retrieval commands above with `after-golden` / `after-retrieval` output paths | Both gates passed; golden metric summaries identical. All retrieval quality metrics identical; elapsed durations differed naturally, no speedup claim. |
| `-m scripts.demo_optional_tracing` | Real SDK in-memory spans saved in `demo-trace.json`; no external export |
| `-m pip check` | Passed |
| `-m scripts.verify_ci_configuration` | Passed; existing four CI jobs retained |
| `-m ruff check .` | Failed: 4 existing errors (two historical artifact files, `scripts/finalize_workflow_browser_step298.py`, `tests/evaluation/test_generation_parser_failure_baseline.py`) |
| `-m ruff format --check .` | Failed: 315 files would reformat, including archived tmp copies |
| Ruff check and format on all 12 changed/new Python files | Passed; 12 already formatted |

Regression command (run before the final three parameterized confirmation tests were added; those three are covered by the final 11-test tracing run):

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/test_optional_tracing.py tests/unit/test_agent_router.py tests/unit/test_agent_conversation.py tests/unit/test_agent_submission.py tests/unit/test_agent_context_memory.py tests/unit/test_agent_resilience.py tests/unit/test_policy_retriever.py tests/unit/test_policy_hybrid_retriever.py tests/unit/test_policy_reranked_retriever.py tests/unit/test_policy_context.py tests/unit/test_policy_access_control.py tests/unit/test_prompt_injection.py tests/evaluation/test_evaluation_runner.py tests/evaluation/test_golden_dataset.py tests/evaluation/test_run_golden_evaluation.py tests/evaluation/test_retrieval_runner.py tests/evaluation/test_retrieval_dataset.py tests/evaluation/test_run_retrieval_evaluation.py tests/integration tests/api -q --basetemp=.pytest_tmp_e1_regression --junitxml=artifacts/evaluation-e0-e1-20261023/regression.xml
```

The seven PostgreSQL tests skipped because `AGENT_POSTGRES_TEST_DSN` is not configured. A broad baseline run was interrupted after historical failures; the targeted reproduction `tests/evaluation/test_answer_pair_live_step268.py -x` fails with `default target configuration drift`. It is recorded, not fixed by changing frozen expectations. Full-suite pass remains unverified.

## File changes

- `app/observability/tracing.py`: optional metadata-only SDK adapter, isolated OTel provider, ContextVar parent correlation, sync/async decorators, fail-open telemetry and shutdown.
- `app/observability/middleware.py`: HTTP root boundary.
- `app/agent/router.py`, `app/agent/workflow.py`: request, executed nodes and persistence boundaries; no topology/state logic changes.
- `app/rag/policy_retriever.py`: vector/BM25/hybrid/reranked and authorized scope boundaries.
- `app/rag/fusion.py`, `app/rag/reranking.py`, `app/rag/policy_context.py`: RRF/rerank/context boundaries.
- `app/llm/openai_compatible_client.py`, `app/resilience/tool_execution.py`: generation/tool execution boundaries.
- `pyproject.toml`: optional `observability` extra pinned to Langfuse 4.18.0.
- `.env.example`: disabled-default configuration reference; tracing requires process environment.
- `tests/unit/test_optional_tracing.py`: privacy, disabled mode, SDK failure, async/cancellation, real SDK and LangGraph, confirmation/idempotency regression.
- `scripts/demo_optional_tracing.py`: synthetic offline trace demonstration; explicit `--export` option.
- `README.md`: links to audit, architecture/operation, acceptance and plan.
- `docs/evaluation/{evaluation_baseline_audit,optional_tracing,evaluation_upgrade_plan,e0_e1_acceptance}.md`: reviewable documentation.
- Evidence directory above: baseline manifest/status, code inventory, reports, JUnit/logs and sample trace.

Baseline hash comparison confirms only the nine intended runtime Python files changed among pre-existing source/dataset files. Previously dirty generation, intent, BM25, policy-answer, frontend and historical review files retained their starting hashes. No approval infrastructure or Phase 38 persistence module was changed; tests use existing simulated submissions only.

## Remaining limitations and accurate portfolio claim

No live LLM/embedding/Judge call, external Langfuse receipt, production trace, human-label review or PostgreSQL integration was completed in this run. Telemetry omits session IDs, token/cost and business final state; execution success is not task success. Normal interpreter shutdown flushes; abrupt termination can lose queued telemetry. These limitations do not block disabled/default application operation.

Defensible wording: “在既有 LangGraph/RAG 服务中加入默认关闭、仅元数据的可选 Langfuse 追踪，覆盖异步节点和工具执行，验证故障隔离、敏感数据保护及确认/幂等回归；保留可复现离线基线与验收产物。” Do not describe this as a calibrated Judge, independent benchmark improvement or production success rate.
