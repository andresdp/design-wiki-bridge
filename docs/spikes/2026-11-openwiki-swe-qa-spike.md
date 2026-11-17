# U1 spike: revisions, SWE-QA items, OpenWiki interface

Status: **partial.** Revisions, the dataset pin, and the OpenWiki interface are settled. Wiki generation and the MCP fixture capture are waiting on the user's `openwiki` runs (see "Remaining steps").

## 1. SWE-QA dataset

- Source: `swe-qa/SWE-QA-Benchmark` at revision `f048f07da234b39e32937f115d027c5b71acd54e` (last modified 2026-08-10). Raw files are in `artifacts/swe-qa/` (gitignored), with their sha256 values pinned in `configs/repos.yaml`.
- **There are no upstream row IDs, no question splits, and no source commit.** Every record is just `{question, answer}`, and each repository is a 48-row split (file). The plan assumed stable row IDs (Assumptions §3), and that assumption does not hold. Decision: row ID = `<split>:<0-based line index>` in the pinned file. Since the revision is pinned, the ID is stable.
- Answers cite files, often with line numbers (`` `src/flask/json/provider.py` (lines 144-148) ``). Some use short paths (`app.py`) that are ambiguous between `src/flask/app.py` and `sansio/app.py`.

## 2. Revision selection

SWE-QA doesn't record the commit it was built from. I scored every release tag between 2024-06 and 2026-06 (plus about 40 sampled first-parent commits) on one test: does a backticked identifier from the same answer appear within ±2 lines of each `path` + line citation? Script: session scratchpad `sha_score.py`, not committed.

| Repo | Pinned tag | SHA | Date | Citation score at tag |
|---|---|---|---|---|
| Flask | 3.1.2 | `2c1b30d0503cfb064f1cb252e6614a06915a362a` | 2025-08-19 | 35/47 (ties 3.1.0–3.1.2; 3.1.3 drops) |
| Requests | v2.32.5 | `b25c87d7cb8d6a18a37fa12442b5f883f9e41741` | 2025-08-18 | 34/40 (best; v2.33+ drops) |
| pytest (reserve) | 8.4.2 | `bfae4224fd554d3d7f2c277a4cc092b6ec6af3ae` | 2025-09-03 | 31/42 (best; 9.x drops) |

The rationale: in every repository the best-scoring range ends at the last release before roughly September 2025, which is consistent with when SWE-QA was published. Where tags tied, I took the newest one in the range. Clean detached checkouts are in `artifacts/checkouts/<repo>-<tag>/` (git worktrees of full clones in `artifacts/repos/`).

## 3. Item check (five items per repository, plus aggregate)

The first five rows with line citations, checked at the pinned SHA:

- **Flask** rows 0, 1, 2, 4, 5: every named symbol exists (36/36). Every cited file exists once short paths are resolved, though row 4 uses bare `app.py` / `ctx.py`. Line-window mismatches: `sansio/scaffold.py:42` (row 1) and `templating.py:138-150` (row 5). Both are small line offsets, and the symbols exist nearby.
- **Requests** rows 1, 3, 4, 5, 7: every named symbol exists (46/46), and every cited range matches. Rows 4 and 7 use bare `models.py` / `adapters.py`.
- **pytest** rows 0, 1, 3, 5, 6: every named symbol exists. Mismatches: `pytester.py:1440-1443` (row 0) and `testing/io/test_saferepr.py:61-64` (row 5).

Aggregate over all 48 rows at the pinned SHA:

| Repo | Rows with line citations | Paths exact / suffix-resolved / ambiguous | Line windows matching | Named symbols present |
|---|---|---|---|---|
| Flask | 30/48 | 47 / 4 / 3 | 37/51 | 311/311 |
| Requests | 29/48 | 40 / 6 / 3 | 40/46 | 280/281 |
| pytest | 28/48 | 42 / 4 / 1 | 34/46 | 289/290 |

Consequence for U4: line numbers in reference answers are approximate. Span annotation for public items has to be re-done by a person against the checkout, and the cited lines can't just be copied. Short paths need resolving by suffix, and ambiguous ones by a person.

## 4. OpenWiki interface

**Version finding: the globally installed `openwiki` is 0.3.0. It has no retrieval MCP tools and no Claims sidecars.** In 0.3.0, `grep` finds no `openwiki_search`, `openwiki_read`, or `.claims`. Those features are in the current release. npm `latest` is **0.9.0** (published 2026-11-05), and I inspected that tarball without installing it. Decision: pin **`openwiki@0.9.0`** (KTD6). The global 0.3.0 can't be used for this study.

Facts from the 0.9.0 package (README and `dist/`):

- **MCP server launch:** `openwiki mcp [--host <id>]`. This is a stdio server (`dist/cli/integrations.js`, "Starts the local stdio MCP server"). The `--host` value is 1–64 lowercase letters, digits, or hyphens. Use `--host dwb`. Requires Node ≥ 22.22.0 (installed: 22.22.3).
- **The server is rootless.** Every call passes `root`, which must be an **absolute path to a Git repository top level that contains `openwiki/`** (`resolveRepositoryRoot` → `git rev-parse` top level + safety check). Consequence for U7: a snapshot can't be served from `artifacts/wikis/<id>/`. At run time it has to be restored into a git checkout at the pinned SHA (the worktrees above), and `root` points at that checkout.
- **`openwiki_search`** input: `root`, `query`, optional `paths` (source paths that boost ranking but don't filter), optional `limit`, optional `workspace`. It returns "compact ranked results". Each `ref` splits at `#` into the page and the exact heading anchor. Empty results are valid (R5). The tool is local and model-free.
- **`openwiki_read`** input: `root`, `page` (for example `openwiki/architecture/jobs.md`), `sections` (a list of heading anchors), optional `wiki`, optional **`includeProvenance`**. With `includeProvenance: true` it also returns "the page's recorded frontmatter sources and Claim evidence resources with their stored versions".
- **Claims:** they're stored under `openwiki/.claims/` as versioned sidecars next to the Markdown. A Claim's evidence is a `repo://path#Lstart-Lend` resource (README: `repo://src/server.ts#L40-L82`). Each page also gets an entry in `openwiki/.page-manifest.json`, and `openwiki/.run.json` checkpoints a run.
- **Section→claims link (KTD4):** use `openwiki_read(..., includeProvenance=true)`. That's the supported path. It returns the Claim evidence for the **page** in the same call, so U8 doesn't have to parse `.claims/` itself. Still open: are Claims tagged per *section*, or only per page? If only per page, U8 attributes a page's claims to every section read from that page. This gets confirmed from the captured fixtures.
- **Pinning:** `OPENWIKI_PROVIDER`, `OPENWIKI_MODEL_ID`, and `OPENWIKI_TELEMETRY_DISABLED=1` all exist. `openwiki code --init` writes `openwiki/`, maintains `AGENTS.md` (and refreshes `CLAUDE.md` if there is one), and may add CI files to the checkout. The resolver must therefore validate spans against `git show <sha>:<path>`, not against working-tree files.

KTD4 claims-first path: **plausible, not yet verified.** The Goal Capsule stop condition (claims missing or not in `repo://path#Lx-Ly` form) is **not triggered** by the documentation. It gets confirmed or triggered by the fixture capture below.

## 5. Remaining steps (user runs OpenWiki; then fixtures are captured)

1. Install the pinned version (needs your approval per CLAUDE.md): `npm install -g openwiki@0.9.0`, or a local `package.json` pin.
2. For each repo, from its checkout (`artifacts/checkouts/flask-3.1.2`, `artifacts/checkouts/requests-v2.32.5`):
   ```sh
   OPENWIKI_TELEMETRY_DISABLED=1 OPENWIKI_PROVIDER=openai OPENWIKI_MODEL_ID=<pinned model> \
     /usr/bin/time -p openwiki code --init
   ```
   Record the version, provider, model, elapsed time, and the cost reported by the provider dashboard.
3. Then capture the fixtures: start `openwiki mcp --host dwb`, call `openwiki_search` and `openwiki_read` (with `includeProvenance: true`) for two Flask questions, and save the raw results to `tests/fixtures/mcp_responses/`. Copy one page with its `.claims` sidecar and `.page-manifest.json` to `tests/fixtures/openwiki/`. Then trace one cross-file question from wiki hit to claim range to the original span.

U7 and U8 stay blocked until step 3. U2–U6 and U9–U11 (condition A) don't depend on it.
