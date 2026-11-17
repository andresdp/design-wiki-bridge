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

## Open questions (for when it resumes)

- Which telemetry mechanism or backend the user wants, and whether it needs OpenTelemetry semantics (spans) or flat records.
- Whether per-question telemetry should also be summarized per run, replacing part of `run_summary.json`.
- Whether chat sessions should emit telemetry too (they currently save no traces).
