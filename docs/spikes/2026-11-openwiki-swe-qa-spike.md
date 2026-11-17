# U1 spike: revisions, SWE-QA items, OpenWiki interface

Status: **Flask done; Requests wiki pending.** Revisions, the dataset pin, the OpenWiki interface, the Flask snapshot, and the MCP fixtures are settled (§6). Still open: the Requests wiki run, the Flask generation cost from the provider dashboard, and the KTD4 section-link decision.

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

Result shapes, from the 0.9.0 source (`dist/retrieval/wiki.js`, `dist/integrations/mcp/server.js`). The MCP server returns `structuredContent` = the result object, with a JSON copy in `content[0].text`:

- search → `{"results": [{"kind": "section", "ref": ["<page>#<anchor>"], "content": "<title>\nSection: <heading>\n<description>\n<excerpt>"}]}`. The array order is the rank. **There is no numeric score.** Ranking goes path-hint matches, then query-term coverage, then FTS score, then row order. An empty `results` list is valid.
- read → `{"page": "<page>", "sections": [{"section": "<anchor>", "content": "<raw markdown>"}], "provenance"?: {"sources": [...], "claims": [{"id", "evidence": [{"resource": "repo://path#Lx-Ly", "version"}]}]}}`.
- Claim sidecar type (`dist/claims/core/types.d.ts`): `Claim = {id, statement, evidence[]}`, stored per page as `PageClaims = {schemaVersion, pageVersion, claims[], verification?}`. Evidence resources must be line ranges (`#L10-L24`). Whole-file resources are rejected.

**Section→claims finding: claims are page-level.** Neither the sidecar nor `openwiki_read` provenance records which section a claim belongs to, so "the section's claims" can't be read directly as KTD4 assumes. Options for the KTD4 revision (user decision before U8):
  (a) attribute all of a page's claims to every section read from it (simple, but coarse: it can pull spans that have nothing to do with the section);
  (b) read `statement` from the `.claims/` sidecar and keep a page's claims whose statement overlaps the section text (deterministic token overlap), falling back to (a) when none match;
  (c) claims only for pages whose single read section is the whole page, otherwise the AST fallback only.

KTD4 claims-first path: **feasible, but the section link has to be revised** (see the options above). It isn't verified on real output yet. The Goal Capsule stop condition (claims missing or not in `repo://path#Lx-Ly` form) is **not triggered** by the documentation. It gets confirmed or triggered by the fixture capture below.

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

## 7. Requests wiki (2026-11-17)

Generated in `artifacts/checkouts/requests-v2.32.5`. `.last-update.json` records `gitHead` = the pinned SHA, `gpt-5.6-luna`, and `status: complete`. 11 pages and 115 claims with 296 evidence resources: **294 valid line ranges at the pinned SHA, and 2 whole-file resources** (`repo://tests/test_hooks.py`, `repo://tests/test_packages.py`). That contradicts the source-reading claim in §4 that evidence must be line ranges, so the resolver now accepts whole-file resources as whole-file spans (`whole_file: true`, split with the shared helper). Resolver coverage: **all 59 sections resolve to ≥ 1 span** (median 7, max 21; claim_section 342, claim_page 91, path 26, ast 1). The 16 unresolved references are fallback-step names. Registered as `wiki-requests-4308cae002e4` (effort medium, 13 min 32 s, `real 836.29`, 202 reads and 126 searches). Cost: still to be read from the provider dashboard.

## 6. Flask snapshot and fixture results (2026-11-17)

**Generation.** `openwiki` 0.9.0 (global nvm install at `~/.nvm/versions/node/v22.22.3/bin/openwiki`; the old 0.3.0 at `/usr/local` was removed). `OPENWIKI_PROVIDER=openai`. The model wasn't set on the command line and came from the saved config: **`gpt-5.6-luna`, effort medium**. Run in `artifacts/checkouts/flask-3.1.2`. Result: 12 pages (72 `##` sections), 227 reads and 118 searches by the agent, **16 min 47 s** wall time (`real 1054.92`). `openwiki/.last-update.json` records `gitHead` = the pinned SHA, `model`, and `status: complete`. One page (`architecture/configuration-and-state.md`) is still marked `openwiki_generated: true` and has no description. Cost: **to be read from the provider dashboard.** The checkout also got `AGENTS.md` and `.github/workflows/openwiki-update.yml`. Neither matters, because resolution reads through `git show <sha>:<path>`.

**Claims.** 132 claims across the 12 pages (9–13 per page) with 302 evidence resources. **All are `repo://path#Lx-Ly` (KTD4 format confirmed), and all 302 ranges are valid at the pinned SHA.** They cover 57 distinct files: source, tests, `docs/*.rst`, `pyproject.toml`, and CI workflows. The goal-capsule stop condition is not triggered.

**MCP.** Launched with `openwiki mcp --host dwb` over stdio through `langchain-mcp-adapters` (`client.session` + `load_mcp_tools`). Startup takes about 1.0–1.3 s, and the server exposes 10 tools (including the generation lifecycle tools, which are never called). **The server needs `git` on its `PATH`.** With a `PATH` that has no `git`, every call fails with `invalid_input: The OpenWiki root must be inside a Git repository`. `root` is the absolute checkout path. Fixtures are in `tests/fixtures/mcp_responses/`: two questions with search (10 results each) plus a read of the top section with `includeProvenance`, an empty search (`{"results": []}`, `isError: false`), and the tool schemas in `session_meta.json`. A page, its sidecar, `.page-manifest.json`, and `.last-update.json` are in `tests/fixtures/openwiki/`.

**Trace (q1, "How does Flask's WSGI entry point create the request context and dispatch…").** The top hit is `architecture/request-runtime.md#dispatch-and-routing`. Reading that section returns 10 page claims spread over 8 files (`app.py`, `ctx.py`, `globals.py`, `signals.py`, `views.py`, `docs/*.rst`). The section text itself links `src/flask/app.py#L879-L920` and `#L1261-L1290` (the `full_dispatch_request` and `dispatch_request` ranges), which are original spans at the pinned SHA. Wiki hit → original span works.

**Second retrieval observation.** For q2 (blueprint template filters), the top hit is a testing-matrix section (`testing/test-strategy.md#focused-validation-matrix`), not an architecture section. Search ranking is lexical, and the wiki has no dedicated templating page. This is a recall risk for condition B and should be recorded as a finding, not tuned away.

**Section→claims evidence, for the KTD4 revision:**
- **Inline source links.** Section Markdown sometimes links repository files with line fragments (`../../src/flask/app.py#L879-L920`). They are exact and section-scoped, but sparse: 25 line-range links in total (all valid at the SHA), almost all on one page (`request-runtime.md`), and present in only 7 of 72 sections.
- **Matching claim statements to sections** (option (b): token overlap of each claim's `statement` with each section on its page, threshold 0.5): **99 of 132 claims (75%) map clearly to one section**, 22 are ambiguous between sections, and 11 match no section.
- **KTD4 revision (approved by the user 2026-11-17), implemented in `src/dwb/retrieval/resolver.py`:** (1) inline line-range links in the section; (2) claims whose statement matches this section (token overlap ≥ 0.5), plus ambiguous claims whose near-tied sections (within 0.15) include it; (3) the page's unmatched claims; (4) only when 1–3 give nothing, the AST fallback for backticked dotted symbols and named paths. Each span records which step produced it (`resolution_method`).
- **U8 coverage on the Flask snapshot (`wiki-flask-52e1119f2d9c`):** 70 of 72 sections resolve to ≥ 1 original span (median 6, max 17). Spans by method: claim_section 357, claim_page 88, inline_link 25, path 11, ast 6. Every claim resource resolves. The 12 unresolved references are all fallback-step names with no matching definition or path. In the end-to-end condition B smoke test (`tests/test_condition_b_e2e.py`), the q1 candidates include `src/flask/app.py#L879-L920`.
