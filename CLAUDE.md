# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

`docs/journal-extension-plan.md` owns the study design. The implementation plan is `docs/plans/2026-11-15-1948-feat-cross-source-qa-research-system-plan.md`, and the U1 findings (pinned SHAs, SWE-QA row IDs, the OpenWiki 0.9.0 interface) are in `docs/spikes/2026-11-openwiki-swe-qa-spike.md`. The code lives in the `dwb` package (`src/dwb/`). Both conditions are built. The resolver follows the revised KTD4 order in the spike doc §6. The Flask wiki snapshot is `wiki-flask-52e1119f2d9c`; its manifest is in `configs/wikis/`.

## Commands

All of these run in the `taxonomy` env, from the repo root (paths and `.env` are resolved relative to the current folder). The package is installed editable (`pip install --no-deps -e .`). Every `dwb` command logs progress to stderr (rich); `dwb -v ...` adds per-stage timings, and `dwb -q ...` shows warnings only. JSON results go to stdout.

- Tests (offline; `live` tests are skipped): `conda run -n taxonomy pytest`
- Live checks (need `OPENAI_API_KEY`): `conda run -n taxonomy pytest -m live`
- Lint: `conda run -n taxonomy ruff check src tests`
- Code index (condition A): `conda run -n taxonomy dwb index-code --repo flask --embedding-model <model>`
- PDF index: `conda run -n taxonomy dwb index-pdf --corpus configs/pdfs/<corpus>.yaml --embedding-model <model>`
- Register a generated wiki: `conda run -n taxonomy dwb wiki-snapshot --repo flask --provider openai --model <model> --effort <effort> --elapsed-s <s> [--cost-usd <usd>]`
- Paired gate: `conda run -n taxonomy dwb check-paired configs/manifests/flask-A.yaml configs/manifests/flask-B.yaml`
- Run one condition: `conda run -n taxonomy dwb run --manifest configs/manifests/flask-A.yaml` (`--web-mode live` is the latency flag)
- Evaluate: `conda run -n taxonomy dwb evaluate --run-a runs/<A> --run-b runs/<B> --out reports/<name>` (`--no-judge` gives recall and cost only)

`conda run` doesn't forward stdin, so heredoc scripts piped into it silently do nothing. Write the script to a file first.

## Environment

Use the existing conda env **`taxonomy`** (Python 3.11) for everything, e.g. `conda run -n taxonomy python ...` or `conda run -n taxonomy pytest ...`. **Ask the user before installing any package** (pip/conda/npm), even one the plan clearly needs. OpenWiki is reached through its stdio MCP server (`openwiki mcp`) via `langchain-mcp-adapters`. It needs `openwiki@0.9.0`; the global 0.3.0 install has no retrieval tools.

Large artifacts are gitignored under `artifacts/`: repository clones and pinned checkouts, the SWE-QA files, indexes, wiki snapshots, and the Tavily store. Run traces go under `runs/`.

## Git / PR workflow

Whenever you commit a branch locally, also give the user a PR description they can paste when merging. Write it in raw markdown inside a code block, keep it concise, and use a title in semantic-versioning / conventional-commit style (e.g. `feat: add OpenWiki retrieval branch`, `fix: ...`, `chore: ...`).

## Stack

Stack: **LangGraph** (query workflow), **Chroma** with OpenAI embeddings (condition A code index and the shared PDF index), **LangChain OpenWiki** (generated repository wiki, accessed through its native MCP search/read), **Tavily** (Web evidence), **DeepEval** (offline scoring), and the **SWE-QA** benchmark (Flask and Requests, with pytest in reserve). Jev is deferred.

## Intended architecture (from the plan)

One LangGraph query graph with **two interchangeable repository-retrieval branches**. The experiment config picks one per run:
- **Condition A (Langflow-style baseline):** top-k code spans from a vector index of the pinned repository. The conference's Langflow export isn't available, so the code makes no claim of conference equivalence.
- **Condition B (OpenWiki):** OpenWiki search → read selected sections → resolve their file and symbol references to original code or test spans at the same commit. Wiki text is never cited. It only guides discovery and is kept in the trace.

Everything downstream is shared and must stay identical across conditions: PDF retriever, the Tavily Web-fallback policy (evaluated only after the local branches finish), the reranker, the context budget, the generator, and the citation format. Any change to a shared stage affects both conditions.

The reranker is LangChain's `LLMListwiseRerank` (from `langchain_classic`), wrapped behind the `Reranker` interface in `src/dwb/rerank.py`. The wrapper parses the full-pool ranking leniently, repairs dropped or duplicated documents, and only then cuts to `top_n`. It must keep evidence IDs through reranking, behave as deterministically as possible (pinned model, temperature 0), and log its tokens and latency per query, because the efficiency RQ reports per-stage costs. A cross-encoder backend is deferred.

Invariants to preserve:
- **Evidence item = ID + source type + text + locator.** Locators: code is commit/path/line range, PDF is document version/page, and Web is URL/capture ID/passage. Evidence IDs must survive reranking and generation. The generator cites IDs, and validation logs unresolved IDs.
- **Reproducibility:** a manifest-driven batch runner persists the repo SHA, document and wiki snapshots, models and prompts, retrieval limits, candidate lists, final context, answer, citations, timing, failures, and usage. Pin one OpenWiki snapshot per checkout and record its generator version, config, cost, and time.
- **Evaluation is a separate command** that runs over saved traces (DeepEval plus a custom claim/cited-span evaluator). It never runs inline with generation.
- **Web:** the quality comparison uses frozen/replayed Tavily passages (exact requests, URLs, capture dates). Web retrieval runs only when the run manifest enables it *and* the item's `allowed_sources` includes `web`. Public SWE-QA items never allow `web`, so the system cannot retrieve published benchmark answers.
- If wiki retrieval returns nothing, the other enabled branches must still contribute evidence.

## Project skills and where they conflict with the plan

Installed as user-level plugins: `langchain-skills` (with the `langchain-docs` / `langchain-reference` MCP servers) and `deepeval`. Their defaults are generic, and this plan wins wherever they disagree:
- **langchain-build-agent:** this skill defaults to Deep Agents. Don't use it here. The query graph is an explicit LangGraph `StateGraph` with deterministic routing (see the mermaid diagram in the plan), because the two conditions have to be controlled and comparable. Check APIs against the versions installed in `taxonomy` and the LangChain docs/reference MCP servers. Don't call `submit_feedback` without the user's agreement. `langchain-agent-tracing`, `langchain-agent-monitoring` (LangSmith), and `langchain-eval-engineering` (Harbor) aren't part of the plan.
- **deepeval:** this skill defaults to `deepeval generate` for goldens, live tracing, and iterating on the app until evals pass. Don't use any of those here. The test set is hand-curated and frozen (SWE-QA items plus authored cross-source items), and it must never be synthesized or tuned against. Evaluation runs offline over saved traces. Iterating on the app uses only the separate development questions. Don't use `deepeval-tracing`, `deepeval-otel`, or Confident AI / `deepeval login`.
- **Tavily:** use only the Python SDK (`search` / `extract`). Persist every request and response so runs can be replayed. Don't use `research()`, because its output is AI-synthesized and its evidence can't be traced back to sources.
