# Using `dwb`

`dwb` answers questions about a software repository. Every claim in an answer cites the original evidence it rests on: code at a commit, PDF pages, or web pages.

`dwb` has **two modes**. They share the query pipeline, the answer format, and most command names, but they differ in who uses them, where they run, and where their files live.

| | **User mode** | **Research mode** (experimenter) |
|---|---|---|
| For | Anyone asking questions about their own repository | The study: paired A/B experiments on pinned repositories |
| Run from | Inside any git repository (any subfolder) | The `design-wiki-bridge` folder |
| Set up with | `dwb init` | `configs/repos.yaml`, `configs/chat.yaml`, `configs/manifests/`, `configs/eval.yaml` |
| Repository | The one you are in, at its HEAD commit | Pinned checkouts in `artifacts/checkouts/`, selected with `--repo <name>` |
| Build sources | `dwb index` (everything the config lists) | `dwb index code <repo>`, `dwb index pdf <corpus>`, `dwb wiki snapshot <repo>` |
| Files written | `<repo>/.dwb/` (hidden from git) | `artifacts/` (indexes, wikis, Tavily store), `runs/`, `reports/` |
| Ask | `dwb ask "…"`, `dwb chat` | `dwb ask "…" --repo flask`, `dwb chat --repo flask` |
| Experiments | No | `dwb experiment check / run / evaluate` |
| API keys | `~/.config/dwb/.env`; a repository's `.env` gives only `OPENAI_API_KEY` and `TAVILY_API_KEY` | `design-wiki-bridge/.env`, read in full |

**Which mode runs?** `dwb` decides from the current folder and the options:

1. `--repo <name>` given: research mode. Run from `design-wiki-bridge`, where `configs/repos.yaml` is.
2. Otherwise, inside a git repository whose root has `.dwb/config.yaml`: user mode for that repository.
3. Otherwise `dwb` stops and says which of the two to set up (`dwb init`, or `--repo`).

A pinned checkout under `artifacts/checkouts/` is a normal git repository, so it can also be set up in user mode (see [Trying user mode on a pinned checkout](#trying-user-mode-on-a-pinned-checkout)). The two modes never share indexes: user mode builds its own in `.dwb/`.

## Contents

- [Install](#install)
- [User mode](#user-mode)
  - [1. dwb init](#1-dwb-init-configure-the-repository)
  - [2. dwb index](#2-dwb-index-build-every-source)
  - [3. dwb chat / dwb ask](#3-dwb-chat--dwb-ask-ask-questions)
  - [Sources](#sources-both-modes)
  - [Reading an answer](#reading-an-answer-both-modes)
  - [The project file](#the-project-file-dwbconfigyaml)
- [Research mode](#research-mode)
  - [Asking questions about a research repository](#asking-questions-about-a-research-repository)
  - [Building the sources](#building-the-sources)
  - [Running and evaluating experiments](#running-and-evaluating-experiments)
  - [Adding a research repository](#adding-a-research-repository)
  - [Research configuration](#research-configuration)
  - [Trying user mode on a pinned checkout](#trying-user-mode-on-a-pinned-checkout)
- [Logging](#logging-and-output)
- [Troubleshooting](#troubleshooting)

## Install

Both modes use the same installation:

```sh
conda activate taxonomy                  # Python 3.11 environment with the dependencies
pip install --no-deps -e <path-to>/design-wiki-bridge     # installs the `dwb` command
```

Run `dwb` in your own terminal with the environment activated. `conda run` does not pass keyboard input through, so `dwb init` and `dwb chat` cannot read your answers under it.

API keys: `OPENAI_API_KEY` is required, and `TAVILY_API_KEY` is needed only for web search. Keys exported in your shell always take precedence. Beyond that, the modes read keys from different places:

- **User mode:** `dwb init` offers to save the keys in `~/.config/dwb/.env` (readable only by you), which `dwb` reads from any folder. From a repository's own `.env`, `dwb` reads only `OPENAI_API_KEY` and `TAVILY_API_KEY`; other settings there (for example `OPENAI_BASE_URL`) are ignored, so a cloned repository cannot redirect your key. Put such settings in your shell or `~/.config/dwb/.env`.
- **Research mode:** `design-wiki-bridge/.env` is read in full, then `~/.config/dwb/.env`.

For the wiki source you also need [OpenWiki](https://github.com/langchain-ai/openwiki) **0.9.0** (`npm install -g openwiki@0.9.0`, Node ≥ 22.22) and `git` on the `PATH`.

Every command has help: `dwb --help`, `dwb init --help`, `dwb ask --help`, and so on.

## User mode

For asking questions about your own repository. Everything happens inside that repository:

```sh
cd ~/code/some-repo
dwb init        # 1. answer a few questions → writes .dwb/config.yaml
dwb index       # 2. index everything the config lists (code at HEAD, PDFs, wiki)
dwb chat        # 3. ask away
```

### 1. `dwb init`: configure the repository

Run it anywhere inside the repository. It shows the commit that will be indexed (HEAD) and asks about:

- **Models:** the chat model (answering and reranking) and the embedding model (indexes). Both are OpenAI models.
- **PDF documents:** specifications, papers, or manuals that explain what the code implements. Give local files or folders (relative to the repository) or URLs. A folder adds every PDF inside it, subfolders included, each titled by its file name; new PDFs in it are picked up on the next `dwb index`. URLs are downloaded during `dwb index`. An entry given twice is rejected.
- **Web search:** whether to use it by default, and which domains to exclude. `init` suggests the project's own hosts (its git host, the docs URLs in `pyproject.toml`/`package.json`, PyPI/npm), so web evidence stays external to the code. Edit the list as you like.
- **OpenWiki:** whether to use the wiki source. If `openwiki/` is missing or older than HEAD, `init` asks whether `dwb index` should generate or update it. That makes model calls (about 15 minutes for a mid-size repository) and writes `openwiki/`, `AGENTS.md`, and a GitHub workflow into the repository.
- **API keys:** if a key is missing, paste it to save it in `~/.config/dwb/.env`. Typing shows `*` per character, and `init` confirms the key as `sk-*****xyz` (first and last three characters only).

The result is **`.dwb/config.yaml`**, a plain YAML file you can edit at any time (see [below](#the-project-file-dwbconfigyaml)). Everything `dwb` writes stays in `.dwb/`, which is hidden from git through `.git/info/exclude`; your `.gitignore` is not touched. `dwb init --yes` accepts every default without asking, and `--force` overwrites an existing config.

### 2. `dwb index`: build every source

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

### 3. `dwb chat` / `dwb ask`: ask questions

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

### Sources (both modes)

The **repository is always a source**. The others are optional:

| Source | What it adds | Needs |
|---|---|---|
| Repository: vector search (default) | Code, tests, and in-repo docs at the indexed commit, found by similarity to the question | `dwb index` |
| Repository: **wiki** (`--wiki`) | The same code, found by following the generated OpenWiki documentation to the code it describes. Wiki text itself is never cited. It replaces vector search while on. | `wiki.enabled` and a wiki matching HEAD |
| **PDFs** (`--pdf`) | Pages from your PDF documents | `pdfs:` in the config |
| **Web** (`--web`) | Live Tavily web search, minus the excluded domains | `TAVILY_API_KEY` |

The defaults come from `sources:` in the config. **Vector search or wiki?** Vector search finds code that looks similar to the question. The wiki helps with "how do these parts fit together" questions that span files, but it can miss topics the wiki doesn't cover. Try both on the same question.

### Reading an answer (both modes)

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
- ⚠ *Not established by the evidence* marks a part of the question the retrieved evidence doesn't answer. `dwb` says so instead of guessing; try other sources.
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
  - source: docs/standards/       # a folder: every PDF inside it, titled by file name
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

## Research mode

For the experimenter: the paired A/B study on pinned repositories. Run every research command from the `design-wiki-bridge` folder: configuration (`configs/`), artifacts (`artifacts/`), and `.env` are resolved relative to it. Repositories are pinned in `configs/repos.yaml`, and their checkouts live in `artifacts/checkouts/`.

```sh
cd ~/Documents/GitHub/design-wiki-bridge
conda activate taxonomy
dwb status                  # every research repository and which sources are ready
```

### Asking questions about a research repository

`ask` and `chat` work as in user mode, but take `--repo <name>` and use the research indexes and wiki snapshots. Their defaults (models, sources, each repository's PDF corpus and excluded web domains) come from `configs/chat.yaml`.

```sh
dwb ask "How does Flask answer OPTIONS?" --repo flask --wiki --pdf
dwb chat --repo requests
```

The chat commands, sources, and answer format are the ones described under [User mode](#3-dwb-chat--dwb-ask-ask-questions). Web results from research-mode chat are cached under `artifacts/tavily/chat-<repo>/`.

### Building the sources

Research mode builds each source separately, so a manifest can pin exactly which index or snapshot a run uses:

```sh
dwb index code flask                                   # code index for the pinned commit
dwb index pdf configs/pdfs/flask.yaml                  # PDF corpus
dwb wiki snapshot flask --provider openai --model gpt-5.6-luna --effort medium --elapsed-s 1054.9
```

`dwb index code` and `dwb index pdf` print the new index ID; put it in the manifests (`indexes.code_index_id`, `indexes.pdf_index_id`). `dwb wiki snapshot` registers the `openwiki/` folder generated in the checkout as a pinned snapshot (`configs/wikis/`). The research harness copies that snapshot into the checkout before each condition B run; in user mode the wiki is used in place.

### Running and evaluating experiments

```sh
dwb experiment check configs/manifests/flask-A.yaml configs/manifests/flask-B.yaml
dwb experiment run configs/manifests/flask-A.yaml --web-mode record
dwb experiment run configs/manifests/flask-B.yaml --web-mode record
dwb experiment evaluate runs/flask-A-dev-001 runs/flask-B-dev-001 --out reports/flask-dev
```

- **Conditions:** condition **A** uses vector search and **B** uses OpenWiki-guided search. Everything else is identical: PDFs, the web policy, the reranker, the context budget, the generator, and the citation format. `experiment check` verifies this for each manifest pair.
- **Manifests:** manifests in `configs/manifests/` pin every setting, including index and snapshot IDs, models, and budgets.
- **Run:** `experiment run` writes `runs/<run_id>/`, containing the frozen manifest, `traces.jsonl` (one record per question), and `run_summary.json`. Code passages are stored by reference (commit, path, lines); `experiment evaluate` reads their text back from the checkout named in the manifest, so keep that checkout. A test set must be frozen; its hash is in the manifest, and it is checked before anything else runs. `--overwrite` replaces a previous run. If one source fails for a question (for example the OpenWiki server, or the reranker call), the question is still answered from the other sources and marked degraded; `run_summary.json` counts `failed` and `degraded` questions separately. Condition A refuses a code index built with a different `max_span_tokens` or an older chunker version: rebuild it with `dwb index code <repo>` and put the new ID in the manifest.
- **Web modes:**
  - `record` calls Tavily for searches that aren't stored yet and saves them.
  - `replay` uses stored results only.
  - `live` stores nothing and is for latency measurements.
  
  Run A with `record` first, so B replays identical web results.
- **Evaluate:** `experiment evaluate` scores two runs offline:
  - evidence recall for the candidates and the final context;
  - required-claim correctness and citation support and coverage, from the judge in `configs/eval.yaml`;
  - per-stage cost.

  Results are paired A/B differences with improved/tied/regressed counts. Means and differences use only the questions scored in both runs, and `n_excluded` says how many were left out. A failed question scores correctness 0 and counts in the `failed` metric; its cost is left empty. Judge verdicts are cached per judge model and temperature. `--no-judge` gives recall and cost only.

  Pass the A run first. `evaluate` stops if the two saved manifests are not condition A then B or fail the `experiment check` rules; `--allow-unpaired` scores them anyway and records that in `evaluator_config.json`.
- **Question records** live in `data/questions/{test,dev}/`; see `data/questions/README.md`.


### Adding a research repository

Pin a commit and fetch only that commit:

```sh
git init -q artifacts/checkouts/<repo>-<tag>
git -C artifacts/checkouts/<repo>-<tag> fetch --depth 1 https://github.com/<org>/<repo>.git "refs/tags/<tag>:refs/tags/<tag>"
git -C artifacts/checkouts/<repo>-<tag> checkout --detach "<tag>^{commit}"
```

Then add it to `configs/repos.yaml` (`url`, `tag`, `sha`, `checkout`), and optionally add a PDF corpus and excluded web domains to `configs/chat.yaml`. Build the indexes with `dwb index code <repo>` and the commands above. Choosing the commit to pin (as the U1 spike did by scoring tags against SWE-QA) needs the full history, so do that in a separate, temporary clone.


### Research configuration

| File | Contents |
|---|---|
| `configs/repos.yaml` | Repositories: URL, pinned SHA, checkout path |
| `configs/chat.yaml` | `ask`/`chat --repo` defaults: models, sources, per-repo PDF corpus and excluded web domains. Validated when loaded: a misspelled key or invalid value stops with an error that names it |
| `configs/pdfs/*.yaml` | PDF corpora |
| `configs/manifests/*.yaml` | Experiment run manifests (one per repository and condition) |
| `configs/eval.yaml` | Evaluation judge model and span-matching rules |
| `configs/wikis/*.json` | Registered wiki snapshot manifests |
| `src/dwb/prompt_files/*.md` | Versioned prompts, shipped with the package; their hashes are recorded in every run |

### Trying user mode on a pinned checkout

The checkouts in `artifacts/checkouts/` are git repositories at the pinned commits, so you can try user mode on one without cloning anything:

```sh
cd artifacts/checkouts/requests-v2.32.5
dwb init        # PDFs can point at ../../pdfs/rfc9110.pdf; the existing openwiki/ is detected
dwb index       # builds new indexes in .dwb/; the research indexes are not touched
dwb chat
```

The research harness reads committed files through git, so `.dwb/` (hidden from git) does not affect it. Do not let `dwb index` regenerate the wiki there (keep `wiki.generate: false`): the checkout's `openwiki/` is the pinned snapshot. To undo, delete `.dwb/`.

## Logging and output

Progress messages go to stderr, and results (answers, JSON) go to stdout. `dwb -v <command>` adds per-stage timings and token counts, and `dwb -q <command>` shows only warnings and errors. Put these options **before** the command.

## Troubleshooting

| Mode | Message | Fix |
|---|---|---|
| Both | `this folder is not a dwb project` | User mode: `dwb init` inside the repository. Research mode: run from design-wiki-bridge with `--repo <name>`. |
| User | `… has not been indexed yet` | `dwb index`. |
| User | `The index is for commit … but HEAD is …` | `dwb index` to update; until then answers cite the indexed commit. |
| User | `not inside a git repository` | dwb indexes committed code: `git init` and commit, or cd into a repository. |
| User | `OPENAI_API_KEY is not set` | Rerun `dwb init` to save it, export it, or put it in `~/.config/dwb/.env`. |
| User | `Wiki not available: openwiki/ missing…` | Set `wiki.generate: true` and `dwb index`, or run `openwiki code --init` yourself, then `dwb index`. |
| Both | `OpenWiki MCP server lacks … (needs 0.9.0)` | `npm install -g openwiki@0.9.0`, then `hash -r`. |
| User | `did not return a PDF` | Download the file in a browser and list the local path under `pdfs:`. |
| User | `PDF … is listed more than once` / `listed twice` | Remove the duplicate entry from `pdfs:` (a folder may already contain the file). |
| User | `no PDF files in <folder>` | Point the entry at a folder with `.pdf` files, or remove it. |
| Research | `no code index for <repo> …` | `dwb index code <repo>`. |
| Research | `replay miss` during an experiment | Run that manifest with `--web-mode record` first. |
| Both | `code index … was built by chunker version 1` / `chunked with max_span_tokens=…` | Rebuild: `dwb index code <repo>` (research; then update the manifest's `code_index_id`) or `dwb index` (project). |
| Research | `runs are not a valid A/B pair` | Pass the A run first, or fix the manifests so `dwb experiment check` passes; `--allow-unpaired` only for exploration. |
| User | `openwiki … changed since dwb index` | `dwb index` again, or ask with `--no-wiki`. |
| Research | `corrupt Tavily record` | Delete the named file and rerun with `--web-mode record`. |
