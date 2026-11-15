<p align="center">
  <img src="docs/assets/icon.svg" alt="design-wiki-bridge icon" width="128">
</p>

# design-wiki-bridge

> **Status: provisional.** The repo has no code yet. This README describes the planned system and will change once implementation starts.

Cross-source question answering over software projects with verifiable citations. The system uses a generated repository wiki ([LangChain OpenWiki](https://github.com/langchain-ai/openwiki)) only as a map for finding the original code and test evidence. Answers then combine that evidence with external specifications (PDFs, Web pages) and cite each claim to an original source span.

This is the research code for the journal extension of a CIBSE conference paper. The full design is in [`docs/journal-extension-plan.md`](docs/journal-extension-plan.md).

## Research questions

1. **Evidence retrieval:** does OpenWiki guidance improve recall of original evidence for repository and cross-source questions?
2. **Answer quality:** does it improve required-claim correctness and the support and coverage of citations?
3. **Efficiency:** what does it cost per query (context tokens, latency, model usage), and what does generating the wiki cost up front?

## Approach

A LangGraph query graph normalizes the question and routes it to the enabled sources. It then merges and reranks candidates and generates an answer that cites evidence IDs. The two experimental conditions differ only in the repository branch:

| Condition | Repository retrieval |
|---|---|
| **A. Baseline** | Top-k code spans from a vector index of the pinned repository. |
| **B. OpenWiki** | OpenWiki search and read, with the selected sections' references resolved to original code and test spans at the same commit. |

Both conditions share the PDF retrieval, the Tavily Web fallback (frozen and replayed in the quality study), the reranker, the context budget, the generator, and the citation format.

## Benchmark

- **Repositories:** Flask and Requests (provisional; pytest in reserve), each pinned to a verified commit.
- **Public items:** 12–15 [SWE-QA](https://huggingface.co/datasets/swe-qa/SWE-QA-Benchmark) questions per repository.
- **Custom items:** 6–8 cross-source questions per repository, authored with claims that can be checked independently against code or tests *and* an external specification (WSGI, HTTP semantics).
- **Scoring:** offline, over saved traces, with [DeepEval](https://deepeval.com) and a custom evaluator that checks each claim against its cited spans.

## Stack

Python 3.11 · LangGraph / LangChain · OpenWiki · Tavily · DeepEval

## Development setup

All work runs in the `taxonomy` conda environment:

```bash
conda activate taxonomy
```

Build, run, and evaluation commands will be added as the code lands.

## Layout

```
docs/
  journal-extension-plan.md   # authoritative study and system design
  assets/icon.svg             # project icon
CLAUDE.md                     # guidance for Claude Code in this repo
```
