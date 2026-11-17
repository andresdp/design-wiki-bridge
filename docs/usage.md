# Using `dwb`

`dwb` answers questions about a software repository. Every claim in an answer cites the original evidence it rests on: code at a pinned commit, PDF pages, or web pages. You can use it two ways:

- **Ask and chat** (`dwb ask`, `dwb chat`): question answering over one repository, with sources you can switch on and off.
- **Experiments** (`dwb experiment …`): the research workflow that compares vector search (condition A) with OpenWiki-guided search (condition B) on a frozen question set.

Run every command from the `design-wiki-bridge` folder. Configuration (`configs/`), built artifacts (`artifacts/`), and API keys (`.env`) are all resolved relative to it.

## Contents

1. [Setup](#setup)
2. [Quick start](#quick-start)
3. [Sources](#sources)
4. [Asking questions](#asking-questions)
5. [Chatting](#chatting)
6. [Reading an answer](#reading-an-answer)
7. [Preparing a repository](#preparing-a-repository)
8. [Adding a repository](#adding-a-repository)
9. [Running the experiments](#running-the-experiments)
10. [Configuration files](#configuration-files)
11. [Troubleshooting](#troubleshooting)

## Setup

```sh
conda activate taxonomy                 # Python 3.11 environment with the dependencies
pip install --no-deps -e .              # installs the `dwb` command
```

Create a `.env` file in the `design-wiki-bridge` folder (it is gitignored):

```
OPENAI_API_KEY=sk-...        # required: embeddings, answering, reranking, evaluation
TAVILY_API_KEY=tvly-...      # only for web search
```

Keys already exported in your shell take precedence over `.env`.

The OpenWiki source also needs [OpenWiki](https://github.com/langchain-ai/openwiki) **0.9.0** (`npm install -g openwiki@0.9.0`, Node ≥ 22.22) and `git` on the `PATH`.

Every command has help: `dwb --help`, `dwb ask --help`, `dwb index code --help`, and so on.

## Quick start

```sh
dwb status                                   # which repositories are ready, and what is missing
dwb ask flask "How does Flask answer an OPTIONS request?"
dwb chat requests --pdf --web                # interactive session with PDFs and web search
```

## Sources

The **repository** is always a source. Three more can be switched on or off:

| Source | Option | What it adds | Needs |
|---|---|---|---|
| Repository | always on | Code, tests and in-repo docs at the pinned commit. Found by **vector search**, or by the **wiki** when it is on. | `dwb index code <repo>` (vector search) |
| Wiki | `--wiki` / `--no-wiki` | Repository evidence found through the OpenWiki snapshot. The wiki is searched and read, and the code it references is retrieved and cited. **Wiki text itself is never cited.** It replaces vector search for the repository. | a registered wiki snapshot |
| PDFs | `--pdf` / `--no-pdf` | Pages from the repository's PDF corpus (specifications, papers, manuals). | `dwb index pdf configs/pdfs/<repo>.yaml` |
| Web | `--web` / `--no-web` | Live Tavily web search. The project's own docs and GitHub are excluded, so web evidence stays external to the code. | `TAVILY_API_KEY` |

The defaults are in `configs/chat.yaml` (`sources:`). Out of the box: vector search, PDFs on, web off. If a PDF index is missing, `dwb` warns you and answers without PDFs. If the wiki is requested but no snapshot exists, it stops and prints the command that creates one.

**Vector search or wiki?** Vector search finds code that looks similar to the question. The wiki follows the generated documentation to the code it describes, which helps for questions about how parts fit together across files. It can miss topics the wiki doesn't cover. Try both on the same question.

## Asking questions

```sh
dwb ask REPO "QUESTION" [--wiki/--no-wiki] [--pdf/--no-pdf] [--web/--no-web] [--model MODEL]
                        [--show-context] [--json]
```

Examples:

```sh
dwb ask flask "Which status code does flask.redirect use by default?"
dwb ask flask "How are request contexts pushed and popped?" --wiki
dwb ask requests "How does Requests change the method when it follows a 303?" --pdf --web
dwb ask requests "How is a generator body sent?" --show-context     # also print the passages
dwb ask flask "..." --json > answer.json                             # the full trace, for scripts
```

- `--model` overrides the answering and reranking model from `configs/chat.yaml`.
- `--show-context` prints every passage the model was given, not just the cited ones.
- `--json` prints the full trace: every retrieval stage with its timing and tokens, the candidates, the final context, the claims, and the citation check.

Each question is answered independently. There is no conversation memory, so every answer rests only on the evidence retrieved for that question.

## Chatting

```sh
dwb chat REPO [--wiki/--no-wiki] [--pdf/--no-pdf] [--web/--no-web] [--model MODEL]
```

Type questions at the `repo>` prompt. Commands:

| Command | Effect |
|---|---|
| `/wiki on` · `/wiki off` | Repository evidence through the wiki, or through vector search |
| `/pdf on` · `/pdf off` | Use the PDF corpus |
| `/web on` · `/web off` | Use web search |
| `/sources` | Show the active sources |
| `/context` | Show every passage behind the last answer |
| `/help` | List the commands |
| `/quit` (or Ctrl-D) | Leave |

The prompt keeps a history (arrow up/down), stored in `artifacts/chat_history`. Web results are cached in `artifacts/tavily/chat-<repo>/`, so asking the same thing again doesn't repeat the search.

## Reading an answer

```
╭─ Answer ─────────────────────────────────────────────────────────────╮
│ • For each WSGI request, Flask creates a request context … [E1]      │
│ • RequestContext.push() first checks … [E2]                          │
╰──────────────────────────────────────────────────────────────────────╯
 ID  Type  Location                      Found via
 E1  code  src/flask/app.py:1479-1524    wiki (claim_section)
 E2  code  src/flask/ctx.py:367-449      wiki (claim_section)
12 passages in context, 7 claims, 10 wiki sections searched, 13.2 s
```

- **Claims** are short statements, each followed by the evidence IDs that support it.
- **Sources** lists every cited passage. *Location* is `path:lines` at the pinned commit, `doc p.N` for a PDF page, or a URL. *Found via* says how the passage was retrieved:
  - `vector search` means the repository vector index.
  - `wiki (inline_link)` means a line link in a wiki section.
  - `wiki (claim_section)` means OpenWiki evidence attached to the section.
  - `wiki (claim_page)` means evidence attached to the page.
  - `wiki (ast)` / `wiki (path)` means a code name or path mentioned in the wiki text.
- A red `[E7?]` marks a citation that doesn't match any passage. The status line counts these.
- *No evidence* means none of the enabled sources returned anything. Enable more sources, or switch between `/wiki on` and `/wiki off`.

## Preparing a repository

A repository needs a pinned checkout, plus an index for each source you want. `dwb status` shows what is missing:

```sh
dwb index code flask                                  # vector search over the code (needed without --wiki)
dwb index pdf configs/pdfs/flask.yaml                 # PDF corpus
dwb wiki snapshot flask --provider openai --model gpt-5.6-luna --effort medium --elapsed-s 1054.9
```

- **Code index**: chunks the code at the pinned commit (functions, methods, classes; other files in line windows) and embeds the chunks. Rerunning reuses an existing index; it is never silently rebuilt.
- **PDF index**: one entry per page. A corpus file lists the documents:
  ```yaml
  corpus_id: flask-v1
  documents:
    - doc_id: rfc9110
      version: rfc9110-2022-06          # change it when the file changes
      path: artifacts/pdfs/rfc9110.pdf  # relative to the design-wiki-bridge folder
      title: "RFC 9110: HTTP Semantics"
  ```
- **Wiki snapshot**: generate the wiki inside the checkout first, then register it. Registration pins the wiki to the commit and records the OpenWiki version and model.
  ```sh
  cd artifacts/checkouts/flask-3.1.2
  OPENWIKI_TELEMETRY_DISABLED=1 OPENWIKI_PROVIDER=openai OPENWIKI_MODEL_ID=gpt-5.6-luna \
    /usr/bin/time -p openwiki code --init
  cd -
  dwb wiki snapshot flask --provider openai --model gpt-5.6-luna --effort medium --elapsed-s <real>
  ```

All indexes use the embedding model in `configs/chat.yaml` unless you pass `--embedding-model`. Questions must be embedded with the same model the index was built with; `dwb status --embedding-model …` checks a different one.

## Adding a repository

1. Clone it and check out the commit you want to study:
   ```sh
   git clone https://github.com/<org>/<repo>.git artifacts/repos/<repo>
   git -C artifacts/repos/<repo> worktree add --detach ../../checkouts/<repo>-<tag> <tag-or-sha>
   ```
2. Add it to `configs/repos.yaml`:
   ```yaml
   repos:
     myrepo:
       url: https://github.com/<org>/<repo>.git
       tag: v1.2.3
       sha: <full commit sha>
       checkout: artifacts/checkouts/myrepo-v1.2.3
   ```
3. Optionally, add PDF and web settings to `configs/chat.yaml` under `repos:`:
   ```yaml
   myrepo:
     pdf_corpus: configs/pdfs/myrepo.yaml
     web_exclude_domains: [myrepo.readthedocs.io, github.com, pypi.org]
   ```
4. Build what you need (`dwb index code myrepo`, and so on), check `dwb status`, then `dwb ask myrepo "…"`.

## Running the experiments

The study compares condition **A** (vector search) with condition **B** (OpenWiki-guided search). Everything else is identical: PDFs, the web policy, the reranker, the context budget, the generator, and the citation format. Run manifests in `configs/manifests/` pin every setting, including index and snapshot IDs, models, and budgets.

```sh
dwb experiment check configs/manifests/flask-A.yaml configs/manifests/flask-B.yaml   # A/B differ only in the repository branch
dwb experiment run configs/manifests/flask-A.yaml --web-mode record    # records live web results
dwb experiment run configs/manifests/flask-B.yaml --web-mode record    # replays what A recorded
dwb experiment evaluate runs/flask-A-dev-001 runs/flask-B-dev-001 --out reports/flask-dev
```

- **`run`** writes `runs/<run_id>/` with a frozen copy of the manifest, `traces.jsonl` (one record per question: stages, candidates, context, claims, citations, timings, tokens), and `run_summary.json`. A test set must be frozen (its hash is in the manifest), and the run refuses to start if the hash differs. Add `--overwrite` to replace a previous run.
- **Web modes:**
  - `record` calls Tavily for searches it hasn't stored yet and saves them.
  - `replay` uses only stored results; a missing one is an error for that question.
  - `live` stores nothing and is for latency measurements.
  
  Run A with `record` first, so B replays identical web results.
- **`evaluate`** scores two runs offline (it never calls the answering pipeline). It reports:
  - evidence recall for the candidates and for the final context;
  - required-claim correctness and citation support and coverage, from an LLM judge configured in `configs/eval.yaml`;
  - per-stage cost.

  Results are paired by question as A/B differences, with improved/tied/regressed counts. Judge verdicts are cached, so re-evaluating unchanged traces is free. `--no-judge` gives recall and cost only.

Question records live in `data/questions/{test,dev}/`. See `data/questions/README.md` and the record schema in `src/dwb/benchmark/schemas.py`.

## Configuration files

| File | Contents |
|---|---|
| `configs/repos.yaml` | Repositories: URL, pinned SHA, checkout path |
| `configs/chat.yaml` | `ask`/`chat` defaults: models, default sources, web settings, per-repo PDF corpus and excluded web domains |
| `configs/pdfs/*.yaml` | PDF corpora |
| `configs/manifests/*.yaml` | Experiment run manifests (one per repository and condition) |
| `configs/eval.yaml` | Evaluation judge model and span-matching rules |
| `configs/wikis/*.json` | Registered wiki snapshot manifests |
| `src/dwb/prompt_files/*.md` | Versioned prompts, shipped with the package; their hashes are recorded in every run |

## Logging and output

Progress messages go to stderr, and results (answers, JSON) go to stdout. `dwb -v <command>` adds per-stage timings and token counts, and `dwb -q <command>` shows only warnings and errors. Options like `-v` go **before** the command.

## Troubleshooting

| Message | Fix |
|---|---|
| `configs/repos.yaml not found` | Run `dwb` from the `design-wiki-bridge` folder. |
| `Missing credentials … OPENAI_API_KEY` | Add the key to `.env` in that folder, or export it. |
| `no code index for <repo> …` | `dwb index code <repo>` (or use `--wiki`). |
| `no OpenWiki snapshot for <repo> …` | Generate and register the wiki (see [Preparing a repository](#preparing-a-repository)), or use `--no-wiki`. |
| `PDFs skipped: no PDF index …` | `dwb index pdf configs/pdfs/<repo>.yaml`. |
| `OpenWiki MCP server lacks … (needs 0.9.0)` | `npm install -g openwiki@0.9.0`, then `hash -r`. |
| `The OpenWiki root must be inside a Git repository` | The checkout path in `configs/repos.yaml` must be a git checkout. |
| `replay miss` during an experiment | Run that manifest with `--web-mode record` first. |
