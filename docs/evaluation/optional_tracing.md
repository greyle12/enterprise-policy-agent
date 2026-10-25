# Optional tracing — architecture and operation

FastAPI middleware -> AgentRouter -> LangGraph nodes -> resilient tool executor -> authorized retrieval / context / LLM. Only executed boundaries create spans. Graph topology, checkpoint schema, authorization filters and tool behavior are unchanged. RRF and reranker functions have dedicated spans. Nested access-control wrappers can share retrieval span names; they represent wrapper/delegate boundaries.

The adapter lazily imports the SDK only when `AGENT_TRACING_ENABLED=true`. A private OTel provider prevents unrelated auto-instrumented spans from being exported. Explicit trace/parent IDs use ContextVar, preserving concurrent task association without changing ambient global OTel context. HTTP request IDs and Prometheus labels are unchanged.

The provider uses only a fixed service name. After the SDK checks project ownership, the exporter copies spans and removes scope attributes before OTLP serialization, keeping project public keys out of stored metadata while preserving the SDK's isolation check. Scope name/version and the fixed service name remain visible and are checked exactly during receipt verification.

Exported fields: fixed developer-defined names, generated trace/span IDs, latency, status (`ok` or `error_or_interrupt`) and SHA-256 digests of configured version identifiers. Return status means the boundary returned, not business success; interrupts are not approval failures. No function arguments/results, exception messages, identity, title, policy text, credentials or tool parameters are supplied to the SDK. Session/case IDs, scenario, token usage, cost and business final state are currently omitted rather than guessed. Configure version fields for reproducible experiments and retain human-readable versions in a local manifest.

## Offline demonstration

PowerShell from repository root:

```powershell
.venv/Scripts/python.exe -m pip install -e '.[dev,observability]'
.venv/Scripts/python.exe -m scripts.demo_optional_tracing
```

The default demo uses the real SDK with an in-memory exporter, runs a synthetic materials query through real LangGraph, performs no submission and sends no telemetry. Saved sample: `artifacts/evaluation-e0-e1-20261023/demo-trace.json`.

## Explicit export and local application

Use a separately provisioned Langfuse server >=3.63.0 or compatible Cloud project. This change does not start or alter Phase 38 PostgreSQL/Redis services.

```powershell
$env:AGENT_TRACING_ENABLED='true'
$env:LANGFUSE_BASE_URL='http://localhost:3000'
$env:LANGFUSE_PUBLIC_KEY='<project public key>'
$env:LANGFUSE_SECRET_KEY='<project secret key>'
$env:AGENT_TRACE_GIT_COMMIT=(git rev-parse HEAD)
$env:AGENT_TRACE_DATASET_VERSION='golden-development-v1'
$env:AGENT_TRACE_MODEL_VERSION='offline-keyword-v1'
$env:AGENT_TRACE_PROMPT_VERSION='not-used'
# Set AGENT_TRACE_CORPUS_FINGERPRINT from the local evaluation report.
.venv/Scripts/python.exe -m scripts.demo_optional_tracing --export
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

These are process environment settings; copying them into `.env` alone does not enable tracing. Do not paste keys into reports. In Langfuse Traces, locate `agent.request` and expand `workflow.run`, classification and executed tools. HTTP calls add `http.request`. Check actual UI receipt after exporting: a successful flush is not proof of server ingestion.

SDK transport is batched; requests never flush. The CLI flushes explicitly and normal interpreter exit shuts down the exporter. Only telemetry errors are swallowed; business exceptions/cancellation propagate. Failed initialization stays disabled until restart. To disable, set `$env:AGENT_TRACING_ENABLED='false'` and restart to discard any queued spans. Cold disabled operation constructs no SDK client or tracing connection.

## Receipt probe

For a machine-readable synthetic probe, run `python -m scripts.verify_optional_tracing --output-dir <new-directory>`. The default uses only in-memory exporters. The explicit `--export` mode requires process-level Langfuse configuration, exports only the new synthetic probe, and reads its observations back through Observations v2 with a bounded retry budget. It verifies IDs, recorded parent relationships, metadata and privacy fields; flush alone never verifies receipt. See [E10 delivery and limits](observability_receipt_delivery.md). A disposable local Langfuse 4.69.0 service has verified all 8 observations of a new synthetic probe; see [the local acceptance evidence](local_langfuse_acceptance.md). Cloud and production deployment remain unverified.

## Verification

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/test_optional_tracing.py -q
```

Tests cover sensitive sentinels, real SDK payload inspection, concurrent context, cancellation, unavailable configuration, start/end errors and existing confirmation/idempotency contracts with tracing enabled/failing. Install the observability extra to run the optional SDK test; base CI skips that test when absent. No real approval service is used. External Langfuse UI receipt and production network behavior remain deployment checks, not completed production evaluation.
