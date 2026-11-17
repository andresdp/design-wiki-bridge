<p align="center">
  <img src="docs/assets/icon.svg" alt="design-wiki-bridge icon" width="128">
</p>

# Design Wiki Bridge

> **Status: in progress.** Both conditions, the shared stages, the batch runner, and offline evaluation are built. Condition B runs end to end on the Flask wiki snapshot. Still to do: the Requests wiki, the benchmark content, and the real-model pilot (see [`docs/spikes/2026-11-openwiki-swe-qa-spike.md`](docs/spikes/2026-11-openwiki-swe-qa-spike.md)).

Cross-source question answering over software projects with verifiable citations. The system uses a generated repository wiki ([LangChain OpenWiki](https://github.com/langchain-ai/openwiki)) only as a map for finding the original code and test evidence. Answers then combine that evidence with external specifications (PDFs, Web pages) and cite each claim to an original source span.

This is the research code for the journal extension of a CIBSE conference paper. The study design is in [`docs/journal-extension-plan.md`](docs/journal-extension-plan.md), and the implementation plan is in [`docs/plans/`](docs/plans/).

## Research questions

1. **Evidence retrieval:** does OpenWiki guidance improve recall of original evidence for repository and cross-source questions?
2. **Answer quality:** does it improve required-claim correctness and the support and coverage of citations?
3. **Efficiency:** what does it cost per query (context tokens, latency, model usage), and what does generating the wiki cost up front?

## Approach

A LangGraph `StateGraph` with deterministic routing normalizes the question and fans out to the sources the item allows. It merges and deduplicates candidates, optionally adds Web evidence, reranks, assembles a token-budgeted context with `E1…En` aliases, and generates structured claims that cite those aliases. Validation maps the aliases back to stable evidence IDs and logs any that don't resolve. The two conditions differ only in the repository branch:

| Condition | Repository retrieval |
|---|---|
| **A. Langflow-style baseline** | Top-k AST code chunks from a Chroma index of the pinned repository. |
| **B. OpenWiki** | `openwiki_search` and `openwiki_read` over MCP, then resolving the selected sections' Claim evidence (`repo://path#Lx-Ly`) and symbol references to original spans at the same commit. |

Both conditions share the PDF retrieval, the item-declared Tavily Web policy (record/replay store), the LLM listwise reranker, the context budget, the generator, and the citation format. `dwb experiment check` enforces this for every A/B manifest pair.

## Benchmark

- **Repositories:** Flask 3.1.2 and Requests v2.32.5 (pytest 8.4.2 in reserve), pinned in [`configs/repos.yaml`](configs/repos.yaml).
- **Public items:** 12–15 [SWE-QA](https://huggingface.co/datasets/swe-qa/SWE-QA-Benchmark) questions per repository (dataset revision pinned; row ID = `<split>:<line index>`).
- **Custom items:** 6–8 cross-source questions per repository, authored with claims that can be checked independently against code or tests *and* an external specification (WSGI, HTTP semantics).
- **Scoring:** offline, over saved traces: span recall, required-claim correctness, citation support and coverage, per-stage cost, all reported as paired A/B differences. DeepEval runs as optional diagnostics.

## Setup

```bash
conda activate taxonomy
pip install --no-deps -e .          # installs the `dwb` command
```

Put `OPENAI_API_KEY` (and `TAVILY_API_KEY` for web search) in a `.env` file in this folder. Run `dwb` from here.

## Usage

`dwb` works in two modes with the same commands. The full guide is **[docs/usage.md](docs/usage.md)**.

**As a tool, inside any git repository:**

```bash
cd ~/code/some-repo
dwb init                      # guided setup → writes .dwb/config.yaml (models, PDFs, web, wiki, keys)
dwb index                     # indexes everything the config lists, for the HEAD commit
dwb chat                      # interactive; /wiki /pdf /web on|off
dwb ask "How are redirects followed?" --pdf --web
dwb status                    # what is indexed, and whether HEAD moved since
```

**As the research harness, from this folder:**

```bash
dwb ask "How does Flask answer OPTIONS?" --repo flask --wiki
dwb index code flask   ·   dwb index pdf configs/pdfs/flask.yaml   ·   dwb wiki snapshot flask …
dwb experiment check configs/manifests/flask-A.yaml configs/manifests/flask-B.yaml
dwb experiment run configs/manifests/flask-A.yaml --web-mode record
dwb experiment evaluate runs/flask-A-dev-001 runs/flask-B-dev-001 --out reports/flask-dev
```

The repository is always a source; the wiki, PDFs, and the web are optional. Answers cite original code, PDF pages, and web pages, never wiki text.

## Development

```bash
pytest                              # offline suite (tests marked `live` are skipped)
pytest -m live                      # needs API keys and the local wiki snapshots
ruff check src tests
```

## Layout

```
configs/       repos.yaml, chat.yaml, pdfs/, manifests/, wikis/, eval.yaml
src/dwb/prompt_files/  versioned generator and judge prompts (hashed into manifests)
src/dwb/       evidence, manifest, trace, budget, rerank, generate, graph, runner, chat, workspace, cli
  benchmark/   question schema, SWE-QA loader, freeze
  retrieval/   chunking, Chroma code/PDF indexes, wiki types
  web/         Tavily record/replay store
  eval/        matching, recall, claim judges, DeepEval diagnostics, report
data/questions/  test/ (frozen), dev/, selections/
docs/          usage.md (guide), journal-extension-plan.md, plans/, spikes/
artifacts/     gitignored: repo checkouts, SWE-QA files, indexes, wiki snapshots, Tavily store
runs/          gitignored: run traces
```
