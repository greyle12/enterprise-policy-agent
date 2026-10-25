# Evaluation baseline audit — 2026-10-23

## Scope and evidence

Workspace `D:/Ai_agent_program/demo1`, branch `main`, HEAD `3e2f6b501c8516e2c0eacb4720d023b71df8bf4a`. Existing dirty files were preserved. Evidence directory: `artifacts/evaluation-e0-e1-20261023`. `baseline-status.txt` records tracked changes; `baseline-manifest.json` freezes actual working-file SHA-256 values, not just HEAD. This is an engineering baseline, not label freezing or human certification. `code-inventory.json` lists Python symbols across the requested directories; inventory does not mean every historical experiment was rerun.

## Capabilities checked

| Capability | Implementation | Boundary / gap |
|---|---|---|
| Golden routing/rules | `app/evaluation/{dataset,models,runner,runtime,reporting}.py`; `tests/evaluation/golden_test_cases.jsonl` | 30 cases: 10 routing, 10 materials, 10 approval. Real LangGraph routing; placeholder policy answerer. No database task-success assertion in this runner. |
| Retrieval | `retrieval_{dataset,models,runner,runtime,reporting}.py`; `retrieval_test_cases.jsonl` | 20 cases, Recall/nDCG at 1/3/5, MRR at 5, Vector/BM25/Hybrid/Reranked. Offline lexical embedding/reranking are not BGE quality. |
| Experiments | `retrieval_experiments.py`, `pgvector_ann_experiments.py`, matching models/reports | Candidate-window/Pareto/performance infrastructure exists. Real BGE mode and PostgreSQL ANN were not rerun here. |
| Answer evaluation | `generation_{dataset,models,evaluation_models,runner,runtime,reporting}.py` | Live/offline/replay capture, citations, surface facts, clarification and prohibited-claim diagnostics, hashes. Surface matching is not semantic correctness/faithfulness. |
| Paired comparison | `scripts/prepare_answer_candidate_step267.py`, `finalize_answer_pair_step268.py` | Saved-context paired responses and anchored assistant scoring already exist. Not a calibrated general blind Judge with order-swap consistency and bootstrap intervals. |
| Human calibration | `scripts/prepare_human_calibration_step292.py`, historical calibration/review scripts | Review kits exist; Step292 explicitly marks development exposure and no independent-test certification. No fresh human labels obtained. |
| Trajectory | `app/agent/workflow.py`, `workflow_models.py`, `app/resilience/tool_execution.py` | Real node steps, interrupts, persistence and retry/outcome records. Unified dataset-based multi-turn E2E aggregation remains E2. |
| Side effects | `tests/unit/test_agent_submission.py`, conversation/resilience/persistence tests | Deterministic confirmation/idempotency assertions exist. Reuse them; do not equate successful text with DB success. |
| Authorization/safety | `app/security`, scoped retriever, context guard, unit/API tests | Filtering before context; no unauthorized-content probes added. Semantic refusal-reason assessment remains partial. |
| PostgreSQL | `tests/integration/test_postgres_{repositories,checkpointer}.py`, existing CI job | Requires isolated `_test` database. No local DSN means skipped, not passed. Phase 38 state logic unchanged. |
| Observability before E1 | `app/observability/{logging,middleware,metrics,prometheus}.py` | HTTP request IDs/logs/metrics existed; no Langfuse adapter found in inspected runtime modules. |
| Langfuse after E1 | `app/observability/tracing.py`, boundary decorators | Optional SDK, private provider, explicit async parent context, metadata only. Server receipt unverified. |
| Judge / attribution | Generation diagnostics and script-specific review reports | No reusable calibrated Judge established by inspected runtime modules. Unified failure taxonomy/review promotion remains E3/E5. |

## Baseline and gates

- Python 3.12.10 in `.venv`; optional SDK 4.18.0 installed; `pip check` passed.
- Golden offline: 30/30. Intent threshold 0.90; tool/material/approval/citation thresholds 1.00.
- Retrieval offline: 20 cases, 199 chunks. Hybrid Recall@5/MRR@5/nDCG@5: 0.85/0.8791667/0.8498324. Reranked: 0.875/0.935/0.8831729. Gates 0.80; Vector/BM25 diagnostic only.
- Focused pre-change tests: 36 passed, recorded in `baseline-target.xml`. Golden/retrieval before and after reports are separate.
- Existing CI runs pytest, Ruff, golden/retrieval/evidence commands and PostgreSQL integration. E1 introduces no new quality thresholds or CI redesign.
- An initial wrong test path `tests/agent` ran zero tests; corrected to `tests/unit`. Broad baseline pytest was interrupted after failures appeared; no complete-suite pass claimed.
- Historical Step268 test reproduces `default target configuration drift` via `prepare_independent_test_step246.py`; see `existing-failure.log`. Its locks/configuration were not repaired or re-frozen.
- Full Ruff found four existing errors; full format check reports 315 files, including archived tmp copies. Scoped E1 files pass; logs preserve exact diagnostics.

## Data and technical debt

The original 30/20 sets are development fixtures, not independent holdout evidence. Generation data span draft/development/review/frozen artifacts across historical directories. Preserve review status, source hashes and independence labels. Live responses, assistant scoring and human ground truth are separate layers. Step292's kit explicitly records development exposure. No new training data or human labels were generated.

The manifest enumerates scanned JSON/JSONL datasets and code. Existing loaders, metrics, report models, runtime factories and business assertions should be reused. Historical script chains pin target configuration and hashes; changes can invalidate them without indicating a tracing regression. Broad repository debt must not be cleaned up as part of E1.

## Research checked

- [Langfuse SDK overview](https://langfuse.com/docs/observability/sdk/overview), [compatibility](https://langfuse.com/docs/compatibility), [PyPI](https://pypi.org/project/langfuse/): PyPI observed 5.0.4; v5 requires server >=4.59.0. Supported v4 works with server >=3.63.0. Pin 4.18.0 without assuming a server upgrade. Installed signatures verified for `start_observation`, `trace_context`, `tracer_provider`, `span_exporter`, `update`, `end`; no legacy v2 trace/span API.
- [Meituan overview](https://tech.meituan.com/2026/09/10/Agent-Evaluation-White-Paper-01.html), [Agent evaluation](https://tech.meituan.com/2026/08/07/Agent-Evaluation.html): map process/outcome assessment to actual nodes, submission state, human review and regression intake. Fluent text is not task success.
- [MT-Bench/Arena](https://arxiv.org/abs/2306.05685), [position bias](https://arxiv.org/abs/2406.07791): motivate blinded, swapped comparisons and calibration. Search did not establish an evaluation product called “opening ranking”; no such attribution is made.
- Langfuse datasets, experiments, scores and prompt management are future integration surfaces, not implemented E1 features. No dataset or score upload occurs here.

Recommended sequence: E2 deterministic workflow/state coverage, E3 calibrated semantic Judge, E4 safety-constrained pairwise comparison, E5 review-led failure intake and CI. See `evaluation_upgrade_plan.md`.
