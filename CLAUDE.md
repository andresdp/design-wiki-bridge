# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This repo has no code yet. The only spec is `docs/journal-extension-plan.md`, which is the authoritative design. It covers the journal extension of a CIBSE conference paper on cross-source (repository + PDF + Web) question answering with citations. There are no build, lint, or test commands yet. Add them here once the project is scaffolded.

## Environment

Use the existing conda env **`taxonomy`** (Python 3.11) for everything, e.g. `conda run -n taxonomy python ...` or `conda run -n taxonomy pytest ...`. It already has langgraph, langchain, deepeval, tavily-python, and pytest. **Ask the user before installing any package** (pip/conda), even one the plan clearly needs. As of 2026-11-15, `langchain-mcp-adapters` is missing. Whether OpenWiki is reached through MCP is still undecided, so discuss that with the user before adding it.

## Git / PR workflow

Whenever you commit a branch locally, also give the user a PR description they can paste when merging. Write it in raw markdown inside a code block, keep it concise, and use a title in semantic-versioning / conventional-commit style (e.g. `feat: add OpenWiki retrieval branch`, `fix: ...`, `chore: ...`).

## Stack

Planned stack: **LangGraph** (query workflow), **LangChain OpenWiki** (generated repository wiki, accessed through its native MCP search/read), **Tavily** (Web evidence), **DeepEval** (offline scoring), and the **SWE-QA** benchmark (Flask and Requests, with pytest in reserve). Jev is deferred.

## Intended architecture (from the plan)

One LangGraph query graph with **two interchangeable repository-retrieval branches**. The experiment config picks one per run:
- **Condition A (baseline):** top-k code spans from a vector index of the pinned repository. This is a port of the conference's Langflow workflow.
- **Condition B (OpenWiki):** OpenWiki search → read selected sections → resolve their file and symbol references to original code or test spans at the same commit. Wiki text is never cited. It only guides discovery and is kept in the trace.

Everything downstream is shared and must stay identical across conditions: PDF retriever, the Tavily Web-fallback policy (evaluated only after the local branches finish), the reranker, the context budget, the generator, and the citation format. Any change to a shared stage affects both conditions.

The reranker implementation is **undecided**. The plan says cross-encoder, but the user is leaning toward an LLM-based reranker from LangChain, so don't hard-wire either one. Whichever is chosen, it must keep evidence IDs through reranking, behave as deterministically as possible (pinned model, temperature 0), and log its tokens and latency per query, because the efficiency RQ reports per-stage costs.

Invariants to preserve:
- **Evidence item = ID + source type + text + locator.** Locators: code is commit/path/line range, PDF is document version/page, and Web is URL/capture ID/passage. Evidence IDs must survive reranking and generation. The generator cites IDs, and validation logs unresolved IDs.
- **Reproducibility:** a manifest-driven batch runner persists the repo SHA, document and wiki snapshots, models and prompts, retrieval limits, candidate lists, final context, answer, citations, timing, failures, and usage. Pin one OpenWiki snapshot per checkout and record its generator version, config, cost, and time.
- **Evaluation is a separate command** that runs over saved traces (DeepEval plus a custom claim/cited-span evaluator). It never runs inline with generation.
- **Web:** the quality comparison uses frozen/replayed Tavily passages (exact requests, URLs, capture dates). Web stays disabled for public SWE-QA items so the system cannot retrieve published benchmark answers.
- If wiki retrieval returns nothing, the other enabled branches must still contribute evidence.

## Project skills and where they conflict with the plan

Installed as user-level plugins: `langchain-skills` (with the `langchain-docs` / `langchain-reference` MCP servers) and `deepeval`. Their defaults are generic, and this plan wins wherever they disagree:
- **langchain-build-agent:** this skill defaults to Deep Agents. Don't use it here. The query graph is an explicit LangGraph `StateGraph` with deterministic routing (see the mermaid diagram in the plan), because the two conditions have to be controlled and comparable. Check APIs against the versions installed in `taxonomy` and the LangChain docs/reference MCP servers. Don't call `submit_feedback` without the user's agreement. `langchain-agent-tracing`, `langchain-agent-monitoring` (LangSmith), and `langchain-eval-engineering` (Harbor) aren't part of the plan.
- **deepeval:** this skill defaults to `deepeval generate` for goldens, live tracing, and iterating on the app until evals pass. Don't use any of those here. The test set is hand-curated and frozen (SWE-QA items plus authored cross-source items), and it must never be synthesized or tuned against. Evaluation runs offline over saved traces. Iterating on the app uses only the separate development questions. Don't use `deepeval-tracing`, `deepeval-otel`, or Confident AI / `deepeval login`.
- **Tavily:** use only the Python SDK (`search` / `extract`). Persist every request and response so runs can be replayed. Don't use `research()`, because its output is AI-synthesized and its evidence can't be traced back to sources.
