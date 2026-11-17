# Using `dwb`

`dwb` answers questions about a software repository. Every claim in an answer cites the original evidence it rests on: code at a commit, PDF pages, or web pages. It works in two modes, with the same commands:

| Mode | Where you run it | Configured by | For |
|---|---|---|---|
| **User tool** | inside any git repository | `<repo>/.dwb/config.yaml`, written by `dwb init` | asking and chatting about that repository |
| **Research harness** | in the `design-wiki-bridge` folder | `configs/` (repos, manifests, eval) | the A/B experiments; `ask`/`chat` take `--repo <name>` |

`dwb` picks the mode from the folder: a repository with `.dwb/config.yaml` is a project, otherwise `--repo` selects a research repository.

## Contents

- [Install](#install)
- [User tool](#user-tool)
  - [1. dwb init](#1-dwb-init): configure the repository
  - [2. dwb index](#2-dwb-index): build every source
  - [3. dwb chat / dwb ask](#3-dwb-chat--dwb-ask)
  - [Sources](#sources)
  - [Reading an answer](#reading-an-answer)
  - [The project file](#the-project-file-dwbconfigyaml)
- [Research harness](#research-harness)
- [Logging](#logging-and-output)
- [Troubleshooting](#troubleshooting)

## Install

```sh
conda activate taxonomy                  # Python 3.11 environment with the dependencies
pip install --no-deps -e <path-to>/design-wiki-bridge     # installs the `dwb` command
```

API keys: `OPENAI_API_KEY` is required, and `TAVILY_API_KEY` is needed only for web search. `dwb init` offers to save them in `~/.config/dwb/.env` (readable only by you), which `dwb` reads from any folder. A `.env` file in the current folder also works, and keys exported in your shell always take precedence.

For the wiki source you also need [OpenWiki](https://github.com/langchain-ai/openwiki) **0.9.0** (`npm install -g openwiki@0.9.0`, Node ≥ 22.22) and `git` on the `PATH`.

Every command has help: `dwb --help`, `dwb init --help`, `dwb ask --help`, and so on.

## User tool

```sh
cd ~/code/some-repo
dwb init        # 1. answer a few questions → writes .dwb/config.yaml
dwb index       # 2. index everything the config lists (code at HEAD, PDFs, wiki)
dwb chat        # 3. ask away
```

### 1. `dwb init`

Run it anywhere inside the repository. It shows the commit that will be indexed (HEAD) and asks about:

- **Models:** the chat model (answering and reranking) and the embedding model (indexes). Both are OpenAI models.
- **PDF documents:** specifications, papers, or manuals that explain what the code implements. Give local paths (relative to the repository) or URLs. URLs are downloaded during `dwb index`.
- **Web search:** whether to use it by default, and which domains to exclude. `init` suggests the project's own hosts (its git host, the docs URLs in `pyproject.toml`/`package.json`, PyPI/npm), so web evidence stays external to the code. Edit the list as you like.
- **OpenWiki:** whether to use the wiki source. If `openwiki/` is missing or older than HEAD, `init` asks whether `dwb index` should generate or update it. That makes model calls (about 15 minutes for a mid-size repository) and writes `openwiki/`, `AGENTS.md`, and a GitHub workflow into the repository.
- **API keys:** if a key is missing, paste it (hidden input) to save it in `~/.config/dwb/.env`.

The result is **`.dwb/config.yaml`**, a plain YAML file you can edit at any time (see [below](#the-project-file-dwbconfigyaml)). Everything `dwb` writes stays in `.dwb/`, which is hidden from git through `.git/info/exclude`; your `.gitignore` is not touched. `dwb init --yes` accepts every default without asking, and `--force` overwrites an existing config.

### 2. `dwb index`

Builds every source the config lists, for the **HEAD commit**:

- **Code (always):** the committed code, read through git. Functions, methods, and classes become one chunk each; other files are split into line windows; the chunks are embedded. Uncommitted changes are **not** indexed, and `dwb` warns about them. Commit first if they matter.
- **PDFs:** one entry per page, with each file's hash as its version.
- **Wiki:** registered when `openwiki/` matches HEAD. It is generated or updated first if `wiki.generate` is on (or with `--generate-wiki`).

Rerunning is cheap: an index that already exists for the same commit, settings, and model is reused, not rebuilt. After new commits, run `dwb index` again. Until you do, chat warns that the index is stale and keeps citing the indexed commit.

`dwb status` shows what is ready:

```
 Source  State  Detail
 commit  ok     indexed 3c7baaa05ab4, HEAD 3c7baaa05ab4
 code    ok     code-requests-ab97677c8e84
 pdf     ok     2 documents, pdf-requests-pdfs-…
 wiki    off    disabled in config
 web     off    excluding github.com, requests.readthedocs.io, pypi.org
```

### 3. `dwb chat` / `dwb ask`

`dwb chat` and `dwb ask` refuse to run until `dwb index` has finished.

```sh
dwb chat                                   # interactive
dwb ask "How are redirects followed?"      # one question
dwb ask "How is a streamed body sent?" --pdf --web --show-context
```

Options (both commands): `--wiki/--no-wiki`, `--pdf/--no-pdf`, `--web/--no-web`, and `--model`. `ask` also has `--show-context` (print every passage the model saw) and `--json` (the full trace, for scripts).

Inside `dwb chat`:

| Command | Effect |
|---|---|
| `/wiki on` · `/wiki off` | Repository evidence through the wiki, or through vector search |
| `/pdf on` · `/pdf off` | Use the PDF documents |
| `/web on` · `/web off` | Use web search |
| `/sources` | Show the active sources |
| `/context` | Show every passage behind the last answer |
| `/help` · `/quit` (or Ctrl-D) | Help · leave |

Each question is answered on its own, from the evidence retrieved for it. There is no conversation memory, so make follow-up questions self-contained. The prompt keeps a history (arrow keys) in `.dwb/chat_history`. Web results are cached in `.dwb/tavily/`, so repeating a question doesn't repeat the search.

### Sources

The **repository is always a source**. The others are optional:

| Source | What it adds | Needs |
|---|---|---|
| Repository: vector search (default) | Code, tests, and in-repo docs at the indexed commit, found by similarity to the question | `dwb index` |
| Repository: **wiki** (`--wiki`) | The same code, found by following the generated OpenWiki documentation to the code it describes. Wiki text itself is never cited. It replaces vector search while on. | `wiki.enabled` and a wiki matching HEAD |
| **PDFs** (`--pdf`) | Pages from your PDF documents | `pdfs:` in the config |
| **Web** (`--web`) | Live Tavily web search, minus the excluded domains | `TAVILY_API_KEY` |

The defaults come from `sources:` in the config. **Vector search or wiki?** Vector search finds code that looks similar to the question. The wiki helps with "how do these parts fit together" questions that span files, but it can miss topics the wiki doesn't cover. Try both on the same question.

### Reading an answer

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
- **Location** is `path:lines` at the indexed commit, `doc p.N` for a PDF page, or a URL.
- **Found via** says how the passage was retrieved:
  - `vector search`: the code index.
  - `wiki (inline_link)`: a line link in a wiki section.
  - `wiki (claim_section)` / `wiki (claim_page)`: OpenWiki evidence attached to the section or page.
  - `wiki (ast)` / `wiki (path)`: a code name or path mentioned in the wiki text.
- A red `[E7?]` marks a citation that doesn't match any passage.
- *No evidence* means none of the enabled sources returned anything. Enable more sources, or switch between `/wiki on` and `/wiki off`.

### The project file (`.dwb/config.yaml`)

```yaml
version: 1
name: requests
models:
  chat: gpt-5.6-luna              # answering and reranking
  embedding: text-embedding-3-large
  temperature: 0.0
sources:                          # defaults for ask/chat; the repository is always on
  wiki: false
  pdf: true
  web: false
code:
  extensions: [.py, .md, .rst, …] # which files to index
  exclude: [openwiki/, .dwb/]     # path prefixes to skip
  max_span_tokens: 1500
pdfs:
  - source: https://www.rfc-editor.org/rfc/rfc9110.pdf
    title: "RFC 9110: HTTP Semantics"
  - source: docs/spec.pdf         # relative to the repository root
    title: Internal spec
web:
  exclude_domains: [github.com, requests.readthedocs.io, pypi.org]
  max_results: 5
  search_depth: basic
wiki:
  enabled: false
  generate: false                 # let `dwb index` run OpenWiki when openwiki/ is missing or stale
  provider: openai
  model: gpt-5.6-luna
```

After editing it, run `dwb index` again. Changing the embedding model or the code settings builds a new index; the old one stays in `.dwb/index/`. To remove dwb from a repository, delete `.dwb/`.

## Research harness

Run from the `design-wiki-bridge` folder. Repositories are pinned in `configs/repos.yaml`, and their checkouts live in `artifacts/checkouts/`.

```sh
dwb status                                             # every research repository and its sources
dwb ask "How does Flask answer OPTIONS?" --repo flask --wiki --pdf
dwb chat --repo requests

dwb index code flask                                   # code index for a research repository
dwb index pdf configs/pdfs/flask.yaml                  # PDF corpus
dwb wiki snapshot flask --provider openai --model gpt-5.6-luna --effort medium --elapsed-s 1054.9

dwb experiment check configs/manifests/flask-A.yaml configs/manifests/flask-B.yaml
dwb experiment run configs/manifests/flask-A.yaml --web-mode record
dwb experiment run configs/manifests/flask-B.yaml --web-mode record
dwb experiment evaluate runs/flask-A-dev-001 runs/flask-B-dev-001 --out reports/flask-dev
```

- **Conditions:** condition **A** uses vector search and **B** uses OpenWiki-guided search. Everything else is identical: PDFs, the web policy, the reranker, the context budget, the generator, and the citation format. `experiment check` verifies this for each manifest pair.
- **Manifests:** manifests in `configs/manifests/` pin every setting, including index and snapshot IDs, models, and budgets.
- **Run:** `experiment run` writes `runs/<run_id>/`, containing the frozen manifest, `traces.jsonl` (one record per question), and `run_summary.json`. A test set must be frozen; its hash is in the manifest. `--overwrite` replaces a previous run.
- **Web modes:**
  - `record` calls Tavily for searches that aren't stored yet and saves them.
  - `replay` uses stored results only.
  - `live` stores nothing and is for latency measurements.
  
  Run A with `record` first, so B replays identical web results.
- **Evaluate:** `experiment evaluate` scores two runs offline:
  - evidence recall for the candidates and the final context;
  - required-claim correctness and citation support and coverage, from the judge in `configs/eval.yaml`;
  - per-stage cost.

  Results are paired A/B differences with improved/tied/regressed counts. Judge verdicts are cached. `--no-judge` gives recall and cost only.
- **Wiki snapshots:** the research harness copies a pinned snapshot into each checkout before a condition B run. In user mode the wiki is used in place.
- **Question records** live in `data/questions/{test,dev}/`; see `data/questions/README.md`.

Research configuration:

| File | Contents |
|---|---|
| `configs/repos.yaml` | Repositories: URL, pinned SHA, checkout path |
| `configs/chat.yaml` | `ask`/`chat --repo` defaults: models, sources, per-repo PDF corpus and excluded web domains |
| `configs/pdfs/*.yaml` | PDF corpora |
| `configs/manifests/*.yaml` | Experiment run manifests (one per repository and condition) |
| `configs/eval.yaml` | Evaluation judge model and span-matching rules |
| `configs/wikis/*.json` | Registered wiki snapshot manifests |
| `src/dwb/prompt_files/*.md` | Versioned prompts, shipped with the package; their hashes are recorded in every run |

## Logging and output

Progress messages go to stderr, and results (answers, JSON) go to stdout. `dwb -v <command>` adds per-stage timings and token counts, and `dwb -q <command>` shows only warnings and errors. Put these options **before** the command.

## Troubleshooting

| Message | Fix |
|---|---|
| `this folder is not a dwb project` | `dwb init` inside the repository (or `--repo <name>` in design-wiki-bridge). |
| `… has not been indexed yet` | `dwb index`. |
| `The index is for commit … but HEAD is …` | `dwb index` to update; until then answers cite the indexed commit. |
| `not inside a git repository` | dwb indexes committed code: `git init` and commit, or cd into a repository. |
| `OPENAI_API_KEY is not set` | Rerun `dwb init` to save it, export it, or put it in `~/.config/dwb/.env`. |
| `Wiki not available: openwiki/ missing…` | Set `wiki.generate: true` and `dwb index`, or run `openwiki code --init` yourself, then `dwb index`. |
| `OpenWiki MCP server lacks … (needs 0.9.0)` | `npm install -g openwiki@0.9.0`, then `hash -r`. |
| `did not return a PDF` | Download the file in a browser and list the local path under `pdfs:`. |
| `no code index for <repo> …` (research mode) | `dwb index code <repo>`. |
| `replay miss` during an experiment | Run that manifest with `--web-mode record` first. |
