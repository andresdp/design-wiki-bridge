<p align="center">
  <img src="docs/assets/icon.svg" alt="design-wiki-bridge icon" width="128">
</p>

# Design Wiki Bridge

> **Status (2026-11-23):** the three conditions, the shared stages, the batch runner and offline evaluation are built; the test sets are frozen, and the paired test runs and their evaluation are complete. Results, interpretation and limitations: [`docs/results/2026-11-23-test-set-results.md`](docs/results/2026-11-23-test-set-results.md).

Cross-source question answering over software projects with verifiable citations. The system uses a generated repository wiki ([LangChain OpenWiki](https://github.com/langchain-ai/openwiki)) only as a map for finding the original code and test evidence. Answers then combine that evidence with external specifications (PDFs, Web pages) and cite each claim to an original source span.

This is the research code for the journal extension (Electronic Journal of SADIO) of a 55JAIIO conference paper. The study design is in [`docs/journal-extension-plan.md`](docs/journal-extension-plan.md), and the implementation plan is in [`docs/plans/`](docs/plans/).

## Research questions

1. **Evidence retrieval:** does OpenWiki guidance improve recall of original evidence for repository and cross-source questions?
2. **Answer quality:** does it improve required-claim correctness and the support and coverage of citations?
3. **Efficiency:** what does it cost per query (context tokens, latency, model usage), and what does generating the wiki cost up front?

## Approach

A LangGraph `StateGraph` with deterministic routing normalizes the question and fans out to the sources the item allows. It merges and deduplicates candidates, optionally adds Web evidence, reranks, assembles a token-budgeted context with `E1…En` aliases, and generates structured claims that cite those aliases. Validation maps the aliases back to stable evidence IDs and logs any that don't resolve. The three conditions differ only in the repository branch:

| Condition | Repository retrieval |
|---|---|
| **A. Langflow-style baseline** | Top-k AST code chunks from a Chroma index of the pinned repository. |
| **B. OpenWiki** | `openwiki_search` and `openwiki_read` over MCP, then resolving the selected sections' Claim evidence (`repo://path#Lx-Ly`) and symbol references to original spans at the same commit. |
| **C. Both** | A and B in parallel; the shared reranker picks from both candidate pools under the same pool and context budget (added after the dev pilot). |

All conditions share the PDF retrieval, the item-declared Tavily Web policy (record/replay store), the LLM listwise reranker, the context budget, the generator, and the citation format. `dwb experiment check` enforces this for every manifest pair (A/B, A/C, B/C).

## Benchmark

- **Repositories:** Flask 3.1.2 and Requests v2.32.5, pinned in [`configs/repos.yaml`](configs/repos.yaml). Flask's PDF sources are three Flask books; Requests uses web pages (RFCs, MDN) as external sources.
- **Frozen test sets (2026-11-22):** Flask 30 questions (10 [SWE-QA](https://huggingface.co/datasets/swe-qa/SWE-QA-Benchmark) public items, 17 authored, 3 unanswerable controls) and Requests 20 (10 public, 8 authored, 2 controls). Authored items carry required claims and annotated evidence spans; some need code plus a book or a web specification. A separate development set (10 + 7) was used for the pilot. Details: [`docs/benchmark-and-evaluation.md`](docs/benchmark-and-evaluation.md) and [`data/questions/README.md`](data/questions/README.md).
- **Scoring:** offline, over saved traces: span recall, required-claim correctness, citation support and coverage, per-stage cost, all reported as paired differences between conditions (B − A primary; C − A and C − B secondary). DeepEval runs as optional diagnostics.

## Results

On the frozen test sets, replacing vector search with the wiki route (B − A) lowered required-claim correctness by 0.20 in both repositories, mainly because the wiki route never reached the needed code for 20–39% of the questions. Combining both routes (C) recovered those losses but added little over vector search alone (+0.02 Flask, +0.06 Requests). The wiki route was 12–14% cheaper in tokens and about 10% faster per question, but the most expensive per correct answer. Citation support stayed between 0.79 and 0.90 in every condition. Full results, interpretation, limitations and practitioner guidance: [`docs/results/2026-11-23-test-set-results.md`](docs/results/2026-11-23-test-set-results.md). Statistical tests (Wilcoxon, permutation, bootstrap intervals, rank-biserial effect sizes) and their interpretation: [`docs/results/2026-11-23-statistical-tests.md`](docs/results/2026-11-23-statistical-tests.md), reproducible with `pip install -e '.[analysis]'` and `python scripts/paired_stats.py` (reads `reports/`, writes `docs/results/stats/`).

## Setup

```bash
conda activate taxonomy
pip install --no-deps -e .          # installs the `dwb` command
```

For the research harness, put `OPENAI_API_KEY` (and `TAVILY_API_KEY` for web search) in a `.env` file in this folder and run `dwb` from here. As a tool in another repository, `dwb init` stores the keys in that repository's `.dwb/.env`. The wiki source needs [OpenWiki](https://github.com/langchain-ai/openwiki) 0.9.0 (`npm install -g openwiki@0.9.0`).

## Usage

`dwb` works in two modes with the same commands. The full guide is **[docs/usage.md](docs/usage.md)**. The architecture and design decisions are in **[docs/architecture.md](docs/architecture.md)**.

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
dwb experiment run configs/manifests/flask-A.yaml --web-only           # record the web results once
dwb experiment run configs/manifests/flask-{A,B,C}.yaml --web-mode replay
dwb experiment run configs/manifests/flask-C.yaml --web-mode replay --resume   # after a stop
dwb experiment evaluate runs/flask-A-test-001 runs/flask-B-test-001 --out reports/flask-test-AB --deepeval
```

The repository is always a source; the wiki, PDFs, and the web are optional. Answers cite original code, PDF pages, and web pages, never wiki text. In `ask` and `chat`, `--wiki` currently replaces vector search (condition B), which was the weaker configuration in the test runs; vector search is the default.

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
docs/          usage.md (guide), architecture.md, benchmark-and-evaluation.md (protocol),
               results/ (findings), journal-extension-plan.md, plans/, spikes/
artifacts/     gitignored: repo checkouts, SWE-QA files, indexes, wiki snapshots, Tavily store
scripts/       paired_stats.py: statistical tests over saved reports
runs/, reports/  gitignored: run traces and evaluation reports
```
