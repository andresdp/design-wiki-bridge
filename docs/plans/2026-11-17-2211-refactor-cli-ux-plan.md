---
title: CLI UX Consistency - Plan
type: refactor
date: 2026-11-17
---

# CLI UX Consistency - Plan

**Status: approved, ready to implement.** The user accepted the plan and the four recommended decisions (see Resolved decisions). The benchmark PR (#3), which also edited `src/dwb/cli.py`, has merged. KTD5 was refined and KTD6-KTD7 added on 2026-11-18 after checking the plan against the code.

## Summary

Give `dwb` one presentation layer used by every command in both modes: one visual language (symbols, colors, headers), one output contract (human output on a terminal; JSON for the data commands when piped or with `--json`), real progress display for long-running work, short structured errors instead of tracebacks, and help grouped by mode. Retrieval, generation, evaluation, and every saved artifact stay exactly as they are; this is presentation only.

## Problem frame

Using `dwb` as a user surfaced that the current output works but does not feel like one product:

- **Two output styles for results.** `status`, `ask`, and `chat` print Rich tables and panels. `index code`, `index pdf`, `wiki snapshot`, `experiment run/check/evaluate`, and `benchmark` print raw JSON to stdout, even at a terminal. `ask` alone has `--json`.
- **Progress is a log, not a display.** Long jobs print timestamped log lines (`[21:46:30] INFO   embedded 256/876`). Embedding has no progress bar, OpenWiki generation has no live status, and an experiment run is a scroll of `[3/12]` lines.
- **Unexpected errors print full tracebacks.** The duplicate-PDF crash showed a 60-line Rich traceback through Chroma internals. Expected errors are already short (`Error: ...`, exit 2), but anything else is not.
- **Log labels by default.** `INFO` / `WARNING` and timestamps appear in normal use; CLIG reserves level labels for verbose mode.
- **Help mixes the modes.** Top-level help says "Run from the design-wiki-bridge folder", which is wrong for user mode. Commands are listed flat (`init`, `ask`, `chat`, `status`, `index`, `wiki`, `experiment`, `benchmark`) with no grouping by mode, no examples in most commands, and no `--version`.
- **Inconsistent context.** `chat` opens with `dwb chat · flask · sources: ...`; `ask` and `index` print no equivalent header, and the two `status` tables have different shapes.

## Research basis

- **CLIG** (clig.dev): stdout for data, stderr for messages; detect a TTY and switch to machine output when piped; honor `NO_COLOR`; show progress for anything slow and print something within ~100 ms; no animations without a TTY; say what changed and suggest the next command; rewrite expected errors as what happened + how to fix, put debug detail behind a flag; prompt only with a TTY and offer `--no-input`; `--version`; examples first in help.
- **Heroku CLI style guide**: output commands print data, action commands show progress on stderr; `--json` for machine output; yellow and red only for warnings and errors; grep-parseable human output.
- **Rich / Typer** (the stack already in use): `Progress` with spinner, bar, and elapsed columns, `transient` bars, `console.status` spinners; Typer `rich_help_panel` to group commands, `epilog` for examples, command suggestions for typos.
- **Atlassian, "10 design principles for delightful CLIs"**: a reaction for every action; progress broken into meaningful steps; human error messages with a fix; text kept short with minimal icons; suggest the next step; prompt for missing input with sensible defaults; clear exit paths (remind that Ctrl-C stops); flags over positional arguments.
- **sberan/cli-ux** (a crowdsourced link list): mostly pointers to the sources above; its one rule relevant here, avoid `noun:verb` command names, already holds (`dwb` uses `group command`, e.g. `dwb index code`).
- **OpenWiki CLI** (the reference the user named): a framed, product-like terminal app (Ink); a guided onboarding (`--init`); masked secret prompts; selection glyphs (`○`/`●`); one-shot `-p/--print` mode for scripts; framed text on stderr so piped stdout stays clean; usage-style error messages; a once-only telemetry notice. `dwb` should borrow its feel (framed, calm, guided), not its full-screen TUI: `dwb` is a command suite, and CLIG excludes full-screen programs from its guidance.

## Requirements

**Visual language**

- R1. One style module defines the symbols, colors, and building blocks every command uses: `✓` done, `✗` failed, `!` warning, `•` list item, `→` next step; green success, yellow warning, red error, cyan accent, dim secondary. Symbols are Unicode, with ASCII fallbacks (`ok`, `x`, `!`, `-`, `->`) when the terminal cannot encode them. Messages stay short (one line where possible) and use symbols sparingly: only those five, only where they carry meaning. The other glyphs the display uses fall back too:
  - spinner frames to `|/-\`
  - progress-bar blocks to `#` and `-`
  - the `·` header separator to `-`
  - `…` to `...`
- R2. Every command that works on a repository opens with the same one-line context header: repository, mode, and commit (for example `requests · user mode · b25c87d7cb8d`). `chat` and `init` open with their R13 and R14 headers instead, so every command has exactly one opening header.
- R3. Every action command ends with a one-line outcome and, where useful, the next command (`✓ Indexed requests at b25c87d7 → next: dwb chat`).

**Output contract**

- R4. Results go to stdout and progress, messages, and errors to stderr, in both modes.
- R5. At a terminal, every command prints human output. When stdout is piped (redirected to another program or a file, e.g. `dwb index code flask | jq`, or run from a script), the data commands print JSON instead: `status`, `index` (user mode), `index code`, `index pdf`, `wiki snapshot`, `experiment check/run/evaluate`, and `benchmark`. Their JSON shapes stay as today, so existing scripts keep working. Two commands print no JSON today and gain a new shape:
  - `status` in research mode emits the per-repo status rows as they are. In user mode it emits `{name, root, sources: [{source, state, detail}]}`.
  - `index` in user mode emits the project state that indexing returns: the commit plus the code, PDF, and wiki index ids. `ask` and `chat` stay human when piped; `ask --json` gives the trace. A global `--json` forces JSON for any data command at a terminal.
- R6. Colors and animations are off when the stream is not a terminal, when `NO_COLOR` is set, or with `--no-color`.

**Progress**

- R7. Embedding (code and PDF indexes) shows a progress bar with counts and elapsed time; OpenWiki generation and network waits show a spinner with elapsed time; experiment runs show one bar over questions with running failed/degraded counts and a one-line result per question; `experiment evaluate` shows one bar over the questions it judges.
- R8. Normal output has no timestamps or level labels; `-v`/`--verbose` restores them (and per-stage timings), as today; `-q` keeps warnings and errors only.

**Errors**

- R9. Expected errors print `✗ <what happened>` and `→ <how to fix>`, and exit 2 (setup or usage) or 1 (problems found, failed run). This keeps today's codes.
- R10. Unexpected errors print a two-line summary (`✗ Unexpected error: <type>: <message>`, `→ details: <log path>; rerun with -v for the traceback`) and write the full traceback to a log file, instead of printing it.

**Help and discoverability**

- R11. Top-level help groups commands by mode, and each command appears in exactly one section, because Typer assigns a single help section per top-level command:
  - "Ask": `ask`, `chat`.
  - "User mode": `init`, `index`, `status`. The help text of `index` and `status` names their research-mode forms.
  - "Research mode": `wiki`, `experiment`, `benchmark`.

  Inside `dwb index --help`, the `code` and `pdf` subcommands sit in a "Research mode" section. Top-level help states both modes correctly and links `docs/usage.md`.
- R12. Each command's help ends with one or two examples; `dwb --version` prints the version (long form only, since `-v` stays verbose).

**Interactive**

- R13. `chat` opens with a framed header (a `dwb <version>` name line, then repository, mode, commit, active sources, and `/help · /quit or Ctrl-D to leave`), shows a spinner with elapsed time while answering, and uses the same answer layout as `ask`.
- R14. `init` opens with the same `dwb <version>` name line, shows its sections as numbered steps (`Step 2 of 6: PDF documents`), and ends with a summary of what it wrote plus the next command. Without a terminal it never prompts: it fails with a message naming `--yes`.

**Exit paths and missing input**

- R15. Long-running commands (`index`, `experiment run`, `experiment evaluate`, wiki generation) state that Ctrl-C stops them. Ctrl-C prints `! Stopped` with what was kept (finished indexes, traces already written), exits 130, and shows no traceback. Some commands handle Ctrl-C differently:
  - **`experiment run`:** the stop message says the run is incomplete (k of N questions, no run summary) and cannot be evaluated, and names `--overwrite` to restart it.
  - **`chat`:** Ctrl-C while an answer is generating cancels only that question, prints `! Stopped`, and returns to the prompt. Ctrl-C or Ctrl-D at the prompt leaves quietly with exit 0.
  - **`init`:** Ctrl-C prints `! Stopped` and says what was written, or that nothing was.
- R16. Missing input at a terminal offers the choices instead of a bare error: `dwb ask` with no question prompts for it; `--repo` with an unknown name lists the configured repositories; a command run outside both a project and the research folder names both ways to start (`dwb init`, or `--repo` from design-wiki-bridge). Without a terminal these stay errors (exit 2).

### Scope boundaries

- No change to retrieval, reranking, generation, evaluation, traces, manifests, or any saved artifact. Paired runs stay comparable (`dwb experiment check` unaffected).
- No full-screen TUI (no Textual/Ink-style app), no streaming answers (the query graph keeps `ainvoke`), no telemetry.
- No new dependencies: Rich, Typer, and prompt_toolkit are already installed.
- Deferred to follow-up work: `experiment evaluate` refusing incomplete runs (no `run_summary.json`). That changes evaluation, so it is outside this presentation-only plan. R15's stop message covers the gap for now.
- Considered, not adopted: flags instead of positional arguments (Atlassian #10). The positional operands (`ask "<question>"`, `experiment run <manifest>`, `evaluate <A> <B>`, `benchmark draft <selection>`) are each the command's single obvious input; turning them into flags would break every documented command for little gain.

---

## Planning contract

### Key technical decisions

- KTD1. **A single `dwb.ui` module (layer 1, interface) owns presentation.** It holds the theme, the symbol set with ASCII fallback, the header/outcome/error helpers, the progress factories, and the output-mode switch (human vs JSON). Commands call it; no command builds Rich markup ad hoc. The module view in `docs/architecture.md` and `tests/test_layers.py` gain the module.
- KTD2. **Progress is reported through a callback, not through logging.** Lower layers (`retrieval.vector_store`, `retrieval.code_index`, `retrieval.pdf_index`, `runner`, `project.index_project`) accept an optional `on_progress(event)` callback; `dwb.ui` renders it as a bar or spinner. Without a callback they behave as today, so tests and library use are unaffected. This keeps layers 2-4 free of UI imports.
- KTD3. **Machine output is chosen by TTY detection plus `--json`.** Data commands produce a result object; `dwb.ui.emit(result, human=renderer)` prints JSON when stdout is piped or `--json` is set, and calls the human renderer otherwise. This keeps scripts that pipe today's JSON working (R5). Terminal behavior needs a real TTY. `conda run` pipes the child's output by default, so the documented research commands must use `conda run --no-capture-output -n taxonomy dwb …` (or an activated `taxonomy` env) to see tables, progress bars, and prompts. U6 updates CLAUDE.md and `docs/usage.md` to match.
- KTD4. **Logging stays for diagnostics only.** User-facing progress moves to `dwb.ui`. Log records still exist and show with `-v` (with timestamps and levels) or in the error log file; the default handler drops level labels and timestamps (R8).
- KTD5. **One exception boundary, inside the app.** Today `cli.py` has 10 `except WorkspaceError` blocks that call `_fail`, plus 5 catches for other expected errors. They move into one boundary on the Typer app itself, not into `main()`. The boundary (a custom Click group class) catches:
  - every expected error type, rendered in R9 form with exit 2: `WorkspaceError`, `OpenWikiError`, `QuestionSetError`, `IndexMismatchError`, `ManifestError` (uncaught today), `EvidenceTextError`, and `UnpairedRunsError`;
  - `KeyboardInterrupt`, for R15;
  - any other exception, for R10.

  `FileExistsError` stays caught in `experiment run`, because its `--overwrite` hint is specific to that command. Click's own control-flow exceptions (`click.exceptions.Exit`, `click.Abort`, `click.ClickException`) pass through unchanged before the catch-all branch. In Click 8 they subclass `RuntimeError`, so without this every `raise typer.Exit(1)` would report as an unexpected error. Tests call `cli.app` through `CliRunner` and never pass through `main()`, so a boundary there would go untested. The traceback goes to `$XDG_CACHE_HOME/dwb/last-error.log` (default `~/.cache/dwb/`). `_fail` remains only for usage errors raised inside a command. On Ctrl-C the boundary reports what was kept from a record that long-running commands keep on the command context. They update it from their KTD2 progress events (index finished, trace written), because the boundary itself cannot see command state.
- KTD6. **Consoles and output mode are resolved per invocation, not at import.** `cli.py` builds its stdout and stderr consoles when the module loads. `CliRunner` swaps the streams on every call, so terminal detection must happen when output is written. `dwb.ui` exposes accessors for the current consoles and output mode. Tests get a hook to force terminal behavior, so human renderers can be checked without a real terminal.
- KTD7. **OpenWiki's own output is captured.** `project._generate_wiki` runs `openwiki code` with inherited stdout, which would tear a spinner apart. By default its output goes to `.dwb/openwiki.log` while a spinner shows elapsed time. On failure, the last lines and the log path are shown. With `-v`, output streams straight to the terminal as today, with no spinner.

### Visual reference (directional)

```text
requests · user mode · b25c87d7cb8d

Indexing
  ✓ Code     876 spans          code-requests-…   8.8 s
  ⠋ PDFs     ███████░░░  212/388 pages             4.1 s
  • Wiki     openwiki/ matches HEAD

✓ Indexed requests at b25c87d7
→ next: dwb chat
```

```text
✗ PDF ../../pdfs/rfc9110.pdf is listed more than once.
→ Remove the duplicate from pdfs: in .dwb/config.yaml, then run dwb index.
```

### Sequencing

U1 (foundation) comes first; U2-U5 depend on it and are otherwise independent, so they can land in any order; U6 closes.

---

## Implementation Units

### U1. Presentation foundation

- **Goal:** the `dwb.ui` module, the exception boundary, logging defaults, `--version`, `--json`/`--no-color` globals.
- **Requirements:** R1, R4, R6, R8, R9, R10, R15; KTD1, KTD4, KTD5, KTD6.
- **Files:** `src/dwb/ui.py` (new), `src/dwb/log.py`, `src/dwb/cli.py`, `tests/test_ui.py` (new), `tests/test_layers.py`, `docs/architecture.md`.
- **Approach:**
  1. Add `dwb.ui` with the theme, the symbols, accessors for the current consoles, and the output-mode switch (KTD6).
  2. Install the app-level boundary (KTD5), then delete the per-command `except WorkspaceError` blocks in `cli.py`.
  3. Change the default log handler to drop time and level, and keep both under `-v`.
- **Test scenarios:**
  - Every existing CLI test that expects exit 2 plus a message still passes once the per-command catches are removed.
  - Symbols, spinner frames, bar blocks, the header separator, and the ellipsis all fall back to ASCII when the console encoding cannot represent them.
  - A command that raises `typer.Exit(1)` still exits 1 and prints no "Unexpected error" line.
  - With `NO_COLOR` or a non-TTY stream, rendered output contains no ANSI codes.
  - A `WorkspaceError` prints the two-line `✗`/`→` form and exits 2; an unexpected exception prints the summary, writes the traceback file, and exits 1; `-v` prints the traceback.
  - Default log output has no timestamps or level labels; `-v` has both.
  - `dwb --version` prints the package version; `-v` is still verbose.
  - Ctrl-C (a `KeyboardInterrupt` raised inside a command) prints `! Stopped`, exits 130, and prints no traceback.
  - Each expected error type KTD5 lists, raised inside a command, prints the two-line `✗`/`→` form and exits 2, with no "Unexpected error" line. This includes `ManifestError` from a malformed manifest, which crashes today.
  - Tests redirect `XDG_CACHE_HOME`, so the traceback file never lands in the developer's real cache directory.

### U2. Results: one contract for data commands

- **Goal:** human renderers plus the JSON switch for `status`, `index code`, `index pdf`, `wiki snapshot`, `experiment check/run/evaluate`, and `benchmark`; the same `status` table shape in both modes. U2 also covers `ask`: its R2 header and the answer layout that U5's `chat` reuses.
- **Requirements:** R2, R3, R5, R16; KTD3.
- **Files:** `src/dwb/cli.py`, `src/dwb/ui.py`, existing CLI tests (`tests/test_chat.py`, `tests/test_project.py`, `tests/test_curation.py`, `tests/test_runner.py`).
- **Test scenarios:**
  - Each data command prints JSON identical to today's when stdout is not a TTY. The current tests run without a TTY and keep passing unchanged.
  - Piped `status` prints the new R5 shape in both modes, and the existing status test is updated to match. Piped user-mode `dwb index` prints the project state.
  - With a TTY-forced console, each prints the header, a table or summary, and the outcome line.
  - `--json` forces JSON at a terminal.
  - `ask` and `chat` print human output when piped; `ask --json` prints the trace.
  - `ask` at a TTY-forced console prints the R2 header, then the answer with its cited sources.
  - An unknown `--repo` lists the configured repositories; `dwb ask` with no question prompts at a terminal and exits 2 without one.

### U3. Progress for long-running work

- **Goal:** progress callbacks in indexing, wiki generation, and experiment runs, rendered by `dwb.ui`.
- **Requirements:** R7; KTD2, KTD7.
- **Files:** `src/dwb/retrieval/vector_store.py`, `src/dwb/retrieval/code_index.py` and `src/dwb/retrieval/pdf_index.py` (their `build_*` wrappers must pass the callback through), `src/dwb/project.py`, `src/dwb/runner.py`, `src/dwb/cli.py`, `src/dwb/ui.py`, `src/dwb/eval/command.py`, `tests/test_ui.py`, `tests/test_project.py`, `tests/test_runner.py`.
- **Approach:** Four places emit progress today:
  1. The `embedded N/M` log lines in `VectorIndex.build`.
  2. The per-question `[i/N]` and `✓ … candidates` lines in `runner`.
  3. The OpenWiki start line in `project._generate_wiki`.
  4. The per-question `[i/N]` judge loop in `eval.command.evaluate_runs`.

  Each becomes a callback event. The log lines remain as debug records.
- **Test scenarios:**
  - `VectorIndex.build` reports item counts through the callback and behaves as today without one.
  - OpenWiki output goes to `.dwb/openwiki.log` by default. A failing fake `openwiki` produces a `WorkspaceError` that carries the last lines and the log path (KTD7).
  - A run reports per-question outcomes (ok, failed, degraded, empty context) through the callback; the summary counts match `run_summary.json`.
  - Without a TTY, no animation is drawn; progress falls back to one line per milestone.
  - An `experiment run` interrupted after one question reports one trace kept, says the run is incomplete and cannot be evaluated, and names `--overwrite` (R15).

### U4. Help and discoverability

- **Goal:** help panels by mode, corrected top-level text, examples, next-step hints.
- **Requirements:** R11, R12.
- **Files:** `src/dwb/cli.py`, `tests/test_chat.py` (help test).
- **Test scenarios:**
  - Top-level help lists the three sections and mentions both modes; each command appears in exactly one section.
  - `dwb index --help` shows `code` and `pdf` under a "Research mode" section.
  - Every command's help includes an example.
  - Long-running commands' help and start line mention that Ctrl-C stops them.

### U5. Interactive polish: chat and init

- **Goal:** chat header and spinner, numbered init steps, no prompting without a TTY.
- **Requirements:** R13, R14.
- **Files:** `src/dwb/chat.py`, `src/dwb/init_wizard.py`, `tests/test_chat.py`, `tests/test_project.py`.
- **Test scenarios:**
  - The chat header shows the `dwb <version>` line, repository, mode, commit, sources, and how to leave; `/sources` uses the same wording.
  - Ctrl-C while `chat` is answering cancels that question, prints `! Stopped`, and the next question still answers.
  - Ctrl-C or Ctrl-D at the `chat` prompt exits 0 quietly.
  - Ctrl-C during `init` prints `! Stopped` and states what was written.
  - `init` without a TTY and without `--yes` exits 2 naming `--yes`; existing piped-input tests use a test hook rather than a real TTY.
  - `init` ends with the written path and `→ next: dwb index`.

### U6. Documentation

- **Goal:** `docs/usage.md` shows the new output (header, progress, errors) and the `--json`/`NO_COLOR` contract.
- **Requirements:** R1-R14.
- **Files:** `docs/usage.md`, `docs/architecture.md`, `CLAUDE.md`. These switch interactive commands to `conda run --no-capture-output -n taxonomy` (KTD3).
- **Test expectation:** none -- documentation only.

---

## Verification contract

| Gate | Command |
|---|---|
| Offline tests | `conda run -n taxonomy pytest` |
| Lint | `conda run -n taxonomy ruff check src tests` |
| Pairing unaffected | `dwb experiment check configs/manifests/flask-A.yaml configs/manifests/flask-B.yaml` passes |
| Manual | At a real terminal (an activated `taxonomy` env, or `conda run --no-capture-output`): `dwb init`, `dwb index`, `dwb chat` (including Ctrl-C mid-answer) in a project; `dwb status`, `dwb index code flask`, `dwb experiment run` in research mode. The same commands piped to `cat` print JSON and no color |

## Definition of done

- Every command uses `dwb.ui`. No command prints ad hoc Rich markup, and none prints raw JSON at a terminal unless `--json` is given.
- Piped output of research commands is byte-compatible with today's JSON. `status` and user-mode `index` are exempt, since they gain new shapes (R5).
- No traceback reaches the terminal without `-v`.
- Experiment traces and run summaries are unchanged for the same inputs.

## Deferred to implementation

- The exact shape of a progress event (fields, and whether it is one dataclass or a few). U3 settles it while wiring the three emitters.
- Whether the custom Click group class or Typer's own hooks give the cleaner boundary (KTD5). Either is fine as long as `CliRunner` exercises it.

## Resolved decisions

Accepted by the user on 2026-11-17:

- **Symbols:** Unicode (`✓ ✗ ! • →`) with ASCII fallback (R1).
- **Piped output:** automatic JSON when piped, for the data commands only; `ask` and `chat` stay human (R5).
- **`-v`:** stays `--verbose`; `--version` is long-form only (R8, R12).
- **Branding:** a small `dwb <version>` name line at the top of `chat` and `init` only; no banner elsewhere (R13, R14).
