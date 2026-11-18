---
title: CLI UX Consistency - Plan
type: refactor
date: 2026-11-17
---

# CLI UX Consistency - Plan

**Status: approved, not started.** The user accepted the plan and the four recommended decisions (see Resolved decisions). Implementation waits for the benchmark PR to merge, since both edit `src/dwb/cli.py`.

## Summary

Give `dwb` one presentation layer used by every command in both modes: one visual language (symbols, colors, headers), one output contract (human output on a terminal, JSON when piped or with `--json`), real progress display for long-running work, short structured errors instead of tracebacks, and help grouped by mode. Retrieval, generation, evaluation, and every saved artifact stay exactly as they are; this is presentation only.

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

- R1. One style module defines the symbols, colors, and building blocks every command uses: `✓` done, `✗` failed, `!` warning, `•` list item, `→` next step; green success, yellow warning, red error, cyan accent, dim secondary. Symbols are Unicode, with ASCII fallbacks (`ok`, `x`, `!`, `-`, `->`) when the terminal cannot encode them. Messages stay short (one line where possible) and use symbols sparingly: only those five, only where they carry meaning.
- R2. Every command that works on a repository opens with the same one-line context header: repository, mode, and commit (for example `requests · user mode · b25c87d7cb8d`).
- R3. Every action command ends with a one-line outcome and, where useful, the next command (`✓ Indexed requests at b25c87d7 → next: dwb chat`).

**Output contract**

- R4. Results go to stdout and progress, messages, and errors to stderr, in both modes.
- R5. At a terminal, every command prints human output. When stdout is piped (redirected to another program or a file, e.g. `dwb index code flask | jq`, or run from a script), the data commands print JSON instead: `status`, `index code`, `index pdf`, `wiki snapshot`, `experiment check/run/evaluate`, and `benchmark`. Their JSON shapes stay as today, so existing scripts keep working. `ask` and `chat` stay human when piped; `ask --json` gives the trace. A global `--json` forces JSON for any data command at a terminal.
- R6. Colors and animations are off when the stream is not a terminal, when `NO_COLOR` is set, or with `--no-color`.

**Progress**

- R7. Embedding (code and PDF indexes) shows a progress bar with counts and elapsed time; OpenWiki generation and network waits show a spinner with elapsed time; experiment runs show one bar over questions with running failed/degraded counts and a one-line result per question.
- R8. Normal output has no timestamps or level labels; `-v`/`--verbose` restores them (and per-stage timings), as today; `-q` keeps warnings and errors only.

**Errors**

- R9. Expected errors print `✗ <what happened>` and `→ <how to fix>`, and exit 2 (setup or usage) or 1 (problems found, failed run). This keeps today's codes.
- R10. Unexpected errors print a two-line summary (`✗ Unexpected error: <type>: <message>`, `→ details: <log path>; rerun with -v for the traceback`) and write the full traceback to a log file, instead of printing it.

**Help and discoverability**

- R11. Help groups commands by mode: "Ask" (`ask`, `chat`), "User mode" (`init`, `index`, `status`), "Research mode" (`index code|pdf`, `wiki`, `experiment`, `benchmark`). Top-level help states both modes correctly and links `docs/usage.md`.
- R12. Each command's help ends with one or two examples; `dwb --version` prints the version (long form only, since `-v` stays verbose).

**Interactive**

- R13. `chat` opens with a framed header (a `dwb <version>` name line, then repository, mode, commit, active sources, and `/help · /quit or Ctrl-D to leave`), shows a spinner with elapsed time while answering, and uses the same answer layout as `ask`.
- R14. `init` opens with the same `dwb <version>` name line, shows its sections as numbered steps (`Step 2 of 6: PDF documents`), and ends with a summary of what it wrote plus the next command. Without a terminal it never prompts: it fails with a message naming `--yes`.

**Exit paths and missing input**

- R15. Long-running commands (`index`, `experiment run`, wiki generation) state that Ctrl-C stops them. Ctrl-C prints `! Stopped` with what was kept (finished indexes, traces already written), exits 130, and shows no traceback.
- R16. Missing input at a terminal offers the choices instead of a bare error: `dwb ask` with no question prompts for it; `--repo` with an unknown name lists the configured repositories; a command run outside both a project and the research folder names both ways to start (`dwb init`, or `--repo` from design-wiki-bridge). Without a terminal these stay errors (exit 2).

### Scope boundaries

- No change to retrieval, reranking, generation, evaluation, traces, manifests, or any saved artifact. Paired runs stay comparable (`dwb experiment check` unaffected).
- No full-screen TUI (no Textual/Ink-style app), no streaming answers (the query graph keeps `ainvoke`), no telemetry.
- No new dependencies: Rich, Typer, and prompt_toolkit are already installed.
- Considered, not adopted: flags instead of positional arguments (Atlassian #10). The positional operands (`ask "<question>"`, `experiment run <manifest>`, `evaluate <A> <B>`, `benchmark draft <selection>`) are each the command's single obvious input; turning them into flags would break every documented command for little gain.

---

## Planning contract

### Key technical decisions

- KTD1. **A single `dwb.ui` module (layer 1, interface) owns presentation.** It holds the theme, the symbol set with ASCII fallback, the header/outcome/error helpers, the progress factories, and the output-mode switch (human vs JSON). Commands call it; no command builds Rich markup ad hoc. The module view in `docs/architecture.md` and `tests/test_layers.py` gain the module.
- KTD2. **Progress is reported through a callback, not through logging.** Lower layers (`retrieval.vector_store`, `runner`, `project.index_project`) accept an optional `on_progress(event)` callback; `dwb.ui` renders it as a bar or spinner. Without a callback they behave as today, so tests and library use are unaffected. This keeps layers 2-4 free of UI imports.
- KTD3. **Machine output is chosen by TTY detection plus `--json`.** Data commands produce a result object; `dwb.ui.emit(result, human=renderer)` prints JSON when stdout is piped or `--json` is set, and calls the human renderer otherwise. This keeps scripts that pipe today's JSON working (R5).
- KTD4. **Logging stays for diagnostics only.** User-facing progress moves to `dwb.ui`. Log records still exist and show with `-v` (with timestamps and levels) or in the error log file; the default handler drops level labels and timestamps (R8).
- KTD5. **One top-level exception boundary.** The Typer app's entry point catches `WorkspaceError` (expected, R9) and any other exception (unexpected, R10), writes the traceback to `~/.cache/dwb/last-error.log`, and maps both to the exit codes.

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
- **Requirements:** R1, R4, R6, R8, R9, R10, R15; KTD1, KTD4, KTD5.
- **Files:** `src/dwb/ui.py` (new), `src/dwb/log.py`, `src/dwb/cli.py`, `tests/test_ui.py` (new), `tests/test_layers.py`, `docs/architecture.md`.
- **Test scenarios:**
  - Symbols fall back to ASCII when the console encoding cannot represent them.
  - With `NO_COLOR` or a non-TTY stream, rendered output contains no ANSI codes.
  - A `WorkspaceError` prints the two-line `✗`/`→` form and exits 2; an unexpected exception prints the summary, writes the traceback file, and exits 1; `-v` prints the traceback.
  - Default log output has no timestamps or level labels; `-v` has both.
  - `dwb --version` prints the package version; `-v` is still verbose.
  - Ctrl-C (a `KeyboardInterrupt` raised inside a command) prints `! Stopped`, exits 130, and prints no traceback.

### U2. Results: one contract for data commands

- **Goal:** human renderers plus the JSON switch for `status`, `index code`, `index pdf`, `wiki snapshot`, `experiment check/run/evaluate`, and `benchmark`; the same `status` table shape in both modes.
- **Requirements:** R2, R3, R5, R16; KTD3.
- **Files:** `src/dwb/cli.py`, `src/dwb/ui.py`, existing CLI tests (`tests/test_chat.py`, `tests/test_project.py`, `tests/test_curation.py`, `tests/test_runner.py`).
- **Test scenarios:**
  - Each data command prints JSON identical to today's when stdout is not a TTY (the current tests run without a TTY and keep passing unchanged).
  - With a TTY-forced console, each prints the header, a table or summary, and the outcome line.
  - `--json` forces JSON at a terminal.
  - `ask` and `chat` print human output when piped; `ask --json` prints the trace.
  - An unknown `--repo` lists the configured repositories; `dwb ask` with no question prompts at a terminal and exits 2 without one.

### U3. Progress for long-running work

- **Goal:** progress callbacks in indexing, wiki generation, and experiment runs, rendered by `dwb.ui`.
- **Requirements:** R7; KTD2.
- **Files:** `src/dwb/retrieval/vector_store.py`, `src/dwb/project.py`, `src/dwb/runner.py`, `src/dwb/cli.py`, `src/dwb/ui.py`, `tests/test_ui.py`.
- **Test scenarios:**
  - `VectorIndex.build` reports item counts through the callback and behaves as today without one.
  - A run reports per-question outcomes (ok, failed, degraded, empty context) through the callback; the summary counts match `run_summary.json`.
  - Without a TTY, no animation is drawn; progress falls back to one line per milestone.

### U4. Help and discoverability

- **Goal:** help panels by mode, corrected top-level text, examples, next-step hints.
- **Requirements:** R11, R12.
- **Files:** `src/dwb/cli.py`, `tests/test_chat.py` (help test).
- **Test scenarios:**
  - Top-level help lists the three panels and mentions both modes.
  - Every command's help includes an example.
  - Long-running commands' help and start line mention that Ctrl-C stops them.

### U5. Interactive polish: chat and init

- **Goal:** chat header and spinner, numbered init steps, no prompting without a TTY.
- **Requirements:** R13, R14.
- **Files:** `src/dwb/chat.py`, `src/dwb/init_wizard.py`, `tests/test_chat.py`, `tests/test_project.py`.
- **Test scenarios:**
  - The chat header shows the `dwb <version>` line, repository, mode, commit, sources, and how to leave; `/sources` uses the same wording.
  - `init` without a TTY and without `--yes` exits 2 naming `--yes`; existing piped-input tests use a test hook rather than a real TTY.
  - `init` ends with the written path and `→ next: dwb index`.

### U6. Documentation and screenshots

- **Goal:** `docs/usage.md` shows the new output (header, progress, errors) and the `--json`/`NO_COLOR` contract.
- **Requirements:** R1-R14.
- **Files:** `docs/usage.md`, `docs/architecture.md`.
- **Test expectation:** none -- documentation only.

---

## Verification contract

| Gate | Command |
|---|---|
| Offline tests | `conda run -n taxonomy pytest` |
| Lint | `conda run -n taxonomy ruff check src tests` |
| Pairing unaffected | `dwb experiment check configs/manifests/flask-A.yaml configs/manifests/flask-B.yaml` passes |
| Manual | At a real terminal: `dwb init`, `dwb index`, `dwb chat` in a project; `dwb status`, `dwb index code flask`, `dwb experiment run` in research mode; the same commands piped to `cat` print JSON and no color |

## Definition of done

- Every command uses `dwb.ui`; no command prints ad hoc Rich markup or raw JSON at a terminal.
- Piped output of research commands is byte-compatible with today's JSON.
- No traceback reaches the terminal without `-v`.
- Experiment traces and run summaries are unchanged for the same inputs.

## Resolved decisions

Accepted by the user on 2026-11-17:

- **Symbols:** Unicode (`✓ ✗ ! • →`) with ASCII fallback (R1).
- **Piped output:** automatic JSON when piped, for the data commands only; `ask` and `chat` stay human (R5).
- **`-v`:** stays `--verbose`; `--version` is long-form only (R8, R12).
- **Branding:** a small `dwb <version>` name line at the top of `chat` and `init` only; no banner elsewhere (R13, R14).
