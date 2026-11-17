---
title: Organic telemetry for the dwb query graph - Plan
type: refactor
date: 2026-11-17
status: parked
---

# Organic telemetry for the dwb query graph (parked)

**Status: parked.** Recorded so it can be picked up later; nothing here is scheduled. The user will define the telemetry mechanism (backend, export format) when this is resumed.

## Why

Telemetry today is hand-built inside each graph node:

- Each node times itself with `time.perf_counter()` and returns a `StageRecord` (`src/dwb/graph.py`).
- The reranker and the generator each attach their own `UsageMetadataCallbackHandler` (`src/dwb/rerank.py`, `src/dwb/generate.py`).
- Embedding calls are counted approximately in the retrieval nodes (`src/dwb/usage.py`).
- `trace_from_state` turns the final graph state into a `TraceRecord`, and the runner writes it as JSONL.

This works and is what the study's RQ3 tables use, but it has some drawbacks:

- **Duplicated mechanics:** each new node must remember to time itself and to wire its own callback handler, so instrumentation is easy to miss.
- **Mixed concerns:** measurement (latency, tokens, model IDs) and the research record (candidates, evidence, claims, citations) live in the same `StageRecord` and `TraceRecord`.
- **No partial telemetry on failure:** with `ainvoke`, a question that fails mid-graph keeps only its error, not the stages that ran. This is accepted for now; streaming is out of scope.
- **No export path:** nothing can feed an external telemetry system without parsing the traces.

## Target design (directional)

1. **One callback handler per question, at the graph boundary.** Pass a `dwb` telemetry handler (a LangChain `BaseCallbackHandler`, managed by the `CallbackManager` that LangChain builds from `config["callbacks"]`) to `graph.ainvoke(..., config={"callbacks": [handler]})`. LangGraph propagates it to every node and to every LLM or chain call inside a node.
2. **Attribute events to graph nodes through LangGraph run metadata** (`langgraph_node`, run and parent run IDs). The handler then records, per node:
   - latency, from chain start and end;
   - LLM token usage, from `usage_metadata` (falling back to `count_tokens_approximately`);
   - model names;
   - errors.
   
   Nodes stop timing themselves.
3. **Embeddings.** LangChain embeddings emit no callbacks, so wrap the retrievers' embedding calls in a small LangChain runnable (or emit a custom event with `adispatch_custom_event`), so that embedding time and approximate tokens flow through the same handler.
4. **Keep the research record separate from telemetry.**
   - `TraceRecord` keeps the evidence, candidates, claims, citations, and validation, which the evaluator needs.
   - Telemetry becomes its own per-question record (`telemetry.jsonl`), joined to the trace by question ID and run ID.
   - The RQ3 cost tables read the telemetry records.
5. **Pluggable sinks:** a `TelemetrySink` protocol, with a JSONL sink as the default (today's behavior). The user's future mechanism plugs in as another sink, without touching nodes or the evaluator.
6. **Compatibility:** an adapter keeps producing today's `StageRecord` fields, so the evaluator and the reports keep working during the migration, and A/B traces from before and after can still be compared.

## Units (sketch)

- T1. The telemetry handler: node attribution via `langgraph_node`, per-node latency, tokens, and models, plus tests with fake models.
- T2. Embedding instrumentation (a runnable wrapper or a custom event).
- T3. The `TelemetrySink` protocol with a JSONL sink; the runner and chat attach the handler at `ainvoke`.
- T4. Remove the per-node timing and handlers, with a `StageRecord` compatibility adapter; update `report.cost_rows`.
- T5. Docs: what is recorded, where it goes, and how to add a sink.

## Constraints

- **No change to retrieval, reranking, generation, or the context budget.** This is a measurement refactor only; A/B pairing (`dwb experiment check`) must be unaffected.
- **Deterministic and offline by default.** Nothing is sent anywhere unless a sink is configured.
- **Out of scope:** LangSmith or other hosted tracing (the user will choose the mechanism), streaming (`astream`), and LangGraph checkpointers.

## Backend options (assessed 2026-11-17)

Constraint from the user: if a backend is adopted, it must be a **free option**. Two candidates were assessed from their current docs and PyPI metadata; nothing was installed. Both are built on OpenTelemetry, so the refinement below holds for either.

### Refinement: OpenTelemetry spans, with local files as the source of truth

- Each question runs inside one root span carrying `run_id`, `question_id`, and `condition`; OpenTelemetry context follows `ainvoke` across async tasks, so every call inside the question nests under it.
- `dwb` owns the `TracerProvider` and always attaches a **JSONL span exporter** that writes `runs/<run_id>/telemetry.jsonl`. The RQ3 cost tables read this file, never a backend, so results stay reproducible offline and survive any backend's retention limit.
- A backend (OpenLIT or Langfuse) is an **optional extra span processor or instrumentor**, off by default and enabled per run. This is the `TelemetrySink` of design step 5, expressed in OpenTelemetry terms.
- Prices are pinned in the repository (a versioned price file), and cost is computed by `dwb` from token counts, not by a backend's live price table, so costs cannot drift mid-study.
- Backends are used only as trace viewers. Their evaluation, dataset, scoring, and prompt-management features are out of scope (CLAUDE.md: evaluation runs offline over saved traces against a frozen, hand-curated set).
- Timing: switch before the paired test runs or after them, never between them; both conditions' RQ3 numbers must come from the same measurement path.

### OpenLIT

- **What it is:** an OpenTelemetry auto-instrumentation SDK (`openlit` 1.45.0, Aug 2026, Apache-2.0, Python ≥3.9). Free: the SDK is open source and emits standard OTLP, viewable in any OpenTelemetry backend or a self-hosted OpenLIT UI.
- **Coverage:** patches the OpenAI SDK (generator, reranker, judge, and, through the SDK, embeddings with real token counts, which closes design step 3), LangChain, Chroma, and MCP (the OpenWiki calls). LangGraph is listed as "graph execution"; per-node spans are not documented. No Tavily integration (raw HTTP spans only).
- **Fit:** `taxonomy` already has `opentelemetry-api`/`-sdk` 1.45.2 and `openai` 2.41, inside its pinned ranges (OpenTelemetry ≥1.38,<2; openai ≥1.92,<3).
- **Concerns:**
  - Heavy install: `anthropic`, `boto3`/`botocore`, the OTLP exporter, 13 HTTP/web-framework instrumentations, `schedule`, `xmltodict`. Unneeded instrumentors would be turned off with `disabled_instrumentors`.
  - Downloads its price file from GitHub at every `init()` unless `pricing_json` points to a local file.
  - Captures prompt and response content by default (`capture_message_content=False` turns it off).
  - Without `otlp_endpoint` it prints spans to the console. Using a custom tracer provider is shown only in the old README (`tracer` parameter), not in the current docs.
- **Telemetry:** the SDK does not phone home. The self-hosted platform sends anonymous usage data to PostHog unless `TELEMETRY_ENABLED=false`.

### Langfuse

- **What it is:** an LLM tracing platform with an OpenTelemetry-based Python SDK (`langfuse` 5.2.0, Nov 2026, MIT, Python ≥3.11; needs `langchain-core>=1` and Langfuse server v4).
- **Free options:**
  - **Self-hosted open-source edition:** all core features without a license key. Docker Compose for local use runs Web and Worker containers plus PostgreSQL, ClickHouse, Redis/Valkey, and MinIO (S3). Docs recommend 4 cores and 16 GiB for that VM.
  - **Cloud Hobby plan:** 50k units per month (a unit is a trace, observation, or score), 30 days of data access, 2 users, no blob export. Estimated study volume: about 150 question runs per full test pass (two repositories; A, B, and the A20 control), at roughly 20 observations each, about 3k units. That fits, but traces (code passages, prompts) leave the machine for Langfuse's EU, US, or Japan region.
- **Coverage:** the LangChain `CallbackHandler` passed at `ainvoke(config={"callbacks": [...]})` is exactly design step 1, with LLM token usage from the callbacks. Not documented: whether LangGraph nodes become observations named after the node, and Python `ainvoke` (examples use `invoke`/`stream`). Embeddings are not captured by any integration (an `embedding` observation type exists), so design step 3 is still needed. No Chroma, MCP, or Tavily integration.
- **Fit:** light install: `httpx`, `pydantic`, `backoff`, `wrapt`, `packaging`, `typing-extensions`, `opentelemetry-api`/`-sdk` ≥1.45.0 (already met), and `opentelemetry-exporter-otlp-proto-http` (new). No `openai` pin.
- **Cost:** computed server-side at ingestion from model price definitions, and not recomputed later. `gpt-5.6-luna` would need a custom price definition; alternatively, `dwb` sends its own `cost_details`, which take priority.
- **Local data:** `Langfuse(tracer_provider=...)` or a `LangfuseSpanProcessor` added to our own provider coexists with the JSONL exporter. `LANGFUSE_TRACING_ENABLED=false` turns it off. Data can be read back through the public API (observations v2, metrics v2).
- **Privacy and telemetry:** client-side masking (`mask_otel_spans`). The self-hosted server sends aggregate usage to PostHog every 12 hours unless `TELEMETRY_ENABLED=false`, and its release check cannot be disabled.

### Comparison

| | OpenLIT | Langfuse |
|---|---|---|
| Free option | Open-source SDK; any OTLP backend or self-hosted UI | Self-hosted OSS (5 services), or Cloud Hobby (50k units/month, 30 days, data leaves the machine) |
| Install footprint | Heavy (26 dependencies) | Light (9 dependencies) |
| How it attaches | Patches SDKs globally at `openlit.init()` | One callback handler at `ainvoke` (design step 1) |
| Embedding tokens | Yes, via the OpenAI SDK (to verify) | No; `dwb` must emit them |
| Chroma / MCP / Tavily | Yes / yes / no | No / no / no |
| Per-node spans | Not documented | Not documented |
| Server needed to view traces | No (console or any OTLP backend) | Yes (self-hosted or cloud) |
| Cost source | Price file (pin locally) | Server price definitions (or `dwb`-sent cost) |

### Recommendation and spike

Either backend can be used; neither is needed for the RQ3 numbers once the JSONL exporter exists. Langfuse matches the planned callback-handler design with fewer dependencies but needs a server; OpenLIT covers more of the pipeline automatically, including embeddings, and needs no server, at the cost of a heavy install. Choose after a spike (approval needed to install the candidate in `taxonomy`; throwaway script, no `src/` changes) that runs one dev question with `ainvoke` and checks:

1. Spans or observations per LangGraph node, named after the node.
2. Spans reach our own `TracerProvider` and the JSONL exporter, with no network export unless a backend is enabled.
3. Token attributes for the generator, reranker, and embedding calls (exact attribute names, matching today's `usage` numbers).
4. Async capture under `ainvoke`, with every call nested under the question's root span.

## Open questions (for when it resumes)

- Which backend to adopt, if any (see "Backend options"); the user requires a free option.
- Whether per-question telemetry should also be summarized per run, replacing part of `run_summary.json`.
- Whether chat sessions should emit telemetry too (they currently save no traces).
