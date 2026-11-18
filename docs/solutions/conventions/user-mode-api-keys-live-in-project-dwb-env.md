---
title: User-mode API keys live in the project's .dwb/.env, not a global file
date: 2026-11-18
category: conventions
module: dwb.project / dwb.init_wizard (user mode)
problem_type: convention
component: service_layer
severity: medium
applies_when:
  - Changing how dwb loads or stores API keys
  - Adding a new secret or setting that user mode reads from a file
  - Writing anything into a project's .dwb/ folder
tags: [api-keys, dotenv, user-mode, dwb-init, secrets, dwb-folder]
related_components: [security]
---

# User-mode API keys live in the project's .dwb/.env, not a global file

## Context

User mode first saved keys in `~/.config/dwb/.env` and loaded that file in every folder, along with a few names from the repository's own `.env`. When the user tested `dwb init` in a new project, it reported `OPENAI_API_KEY: found` without saying where the key came from or offering to change it. The key had been saved by an earlier `init` in a different repository. The user decided that in user mode nothing should come from global configuration: each project owns its keys.

## Guidance

- **One source per mode.** User mode reads keys only from `<repo>/.dwb/.env`, found by walking up from the current folder to the nearest `.dwb/config.yaml` (`_project_root` and `project_env` in `src/dwb/project.py`). It never reads `~/.config/dwb/.env` or the repository's own `.env`. Research mode is unchanged: it reads `design-wiki-bridge/.env` in full, then `~/.config/dwb/.env` (`load_env` in `src/dwb/project.py`).
- **Allow-list what a project file can set.** Only `OPENAI_API_KEY` and `TAVILY_API_KEY` (`LOCAL_ENV_KEYS`) are taken from `.dwb/.env`. A cloned repository could ship its own `.dwb/.env` that sets `OPENAI_BASE_URL` or a proxy and sends your key elsewhere.
- **The shell still wins.** A variable already in the environment is never overridden. `init` reports it as "found in your shell environment" and does not offer to replace it, because a new key saved in the file would be ignored.
- **Hide before you write.** `init` adds `.dwb/` to `.git/info/exclude` before the key file exists (`hide_from_git(env.parent.parent)` in `_ensure_key`, `src/dwb/init_wizard.py`). Otherwise a Ctrl-C between saving the key and writing the config could leave a committable secret. The file is created with mode 0600 (`save_key`).
- **Say where a found key came from.** `init` prints the source and a masked preview (`sk-*****abc`) and asks `Use this OPENAI_API_KEY?`. A key in `.dwb/.env` can be replaced; `--yes` keeps it without asking.

## Why This Matters

A global key file couples unrelated projects: a key saved for one repository silently applies to every other one, and the user cannot tell which key a project uses. Per-project keys make the source visible and keep billing, quotas and revocation scoped to one project. The allow-list and hide-before-write rules keep the per-project file from becoming a way to leak or redirect a key.

## When to Apply

Apply whenever user-mode code reads or writes secrets or settings from a file, or adds files under `.dwb/`. Do not apply it to research mode, which deliberately keeps its harness `.env` plus the global fallback.

## Examples

Before, `dwb init` in a fresh repository:

```text
OPENAI_API_KEY: found
```

After, with a key saved in this project:

```text
OPENAI_API_KEY: found in /path/to/repo/.dwb/.env (sk-*****abc)
Use this OPENAI_API_KEY? [Y/n]:
```

Tests that pin the behavior live in `tests/test_project.py`:
- `test_user_mode_reads_keys_only_from_the_project_dwb_env`: also checks that the repository `.env` and the global file are ignored, from a subfolder.
- `test_user_mode_outside_a_project_reads_no_env_file`
- `test_init_saves_a_pasted_key_in_the_project_dwb_env`: checks mode 0600 and that `.dwb/` is excluded from git.
- `test_init_shows_where_a_saved_key_is_and_keeps_it`
- `test_init_replaces_a_saved_key_when_asked`
- `test_init_does_not_offer_to_replace_a_shell_key`

Open risk, not yet handled: if `dwb index` generates the wiki, OpenWiki reads the working tree. It is not verified whether it skips git-excluded folders, so it might read `.dwb/.env`.

## Related

- [duplicate-pdf-entries-chroma-duplicateiderror.md](../runtime-errors/duplicate-pdf-entries-chroma-duplicateiderror.md): also concerns what user-mode `init`/`index` accept from `.dwb/config.yaml`.
- Change made on branch `refactor/cli-ux` (unmerged as of this writing).
