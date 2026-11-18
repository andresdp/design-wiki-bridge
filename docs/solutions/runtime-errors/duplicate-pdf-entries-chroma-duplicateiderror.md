---
title: dwb index crashes with Chroma DuplicateIDError when a PDF is listed twice
date: 2026-11-18
category: runtime-errors
module: dwb.project (user-mode indexing)
problem_type: runtime_error
component: service_layer
symptoms:
  - "dwb index fails while building the PDF index with chromadb DuplicateIDError"
  - "Error text reads 'Expected IDs to be unique, found N duplicated IDs: ev_...'"
  - The code index built fine; only the PDF step failed
root_cause: missing_validation
resolution_type: code_fix
severity: medium
tags: [pdf-index, chroma, duplicate-ids, evidence-id, dwb-index, user-mode]
related_components: [data_model]
---

# dwb index crashes with Chroma DuplicateIDError when a PDF is listed twice

## Problem

In user mode, `dwb index` raised a raw chromadb `DuplicateIDError` traceback when `.dwb/config.yaml` listed the same PDF twice under `pdfs:`. In the reported case, `rfc9110` had been entered twice in `dwb init`.

## Symptoms

- `dwb index` builds the code index, then crashes in the PDF step with `DuplicateIDError: Expected IDs to be unique, found 62 duplicated IDs: ev_4b31...`.
- The output is a traceback, not a `WorkspaceError` with a fix hint. Nothing tells the user that the cause is their config.

## What Didn't Work

- Editing the config by hand fixed that one user, but `dwb init` still accepted the same file twice, so the bug would come back.
- Dedup by path alone isn't enough. Two different paths can hold the same bytes (`spec.pdf` and `copy.pdf`). A folder entry can also contain a file that is listed explicitly too.

## Solution

Two changes in `src/dwb/project.py`. Both landed on `main` with the `feat/init-pdf-folders-masked-key` branch, which was merged directly rather than through a PR.

**1. Resolve the PDF entries to distinct files before any indexing runs.** `resolve_pdfs()` expands folders, downloads URLs and dedups by **content digest**:

```python
digest = hashlib.sha256(path.read_bytes()).hexdigest()
seen = by_digest.get(digest)
if seen is None:
    by_digest[digest] = pdf = _Pdf(path, title, digest, e.source, explicit)
    out.append(pdf)
elif explicit and seen.explicit:      # named directly twice -> config mistake
    raise WorkspaceError(f"PDF {e.source} is listed {what}. Remove the duplicate "
                         "from pdfs: in .dwb/config.yaml")
elif explicit:                        # a folder already brought it in: keep once
    seen.title, seen.explicit = title or seen.title, True
```

`index_project` calls this before building the code index, so a bad config fails fast and writes no state.

**2. Make `doc_id`s unique.** Two *different* files can have the same name in two folders, such as `docs/a/spec.pdf` and `docs/b/spec.pdf`. When that happens, the slug gets a `-2`, `-3` suffix so citations stay unambiguous (see the `while doc_id in used:` loop in `index_project`).

`dwb init` (`src/dwb/init_wizard.py`) now rejects a PDF that was already added and reports "Already added".

## Why This Works

PDF evidence IDs are deterministic. `evidence_id()` in `src/dwb/evidence.py` hashes `source_type | locator.canonical()`. For a PDF, `PdfLocator.canonical()` is `pdf:{doc_id}@{doc_version}#p{page}`, and `doc_version` is the file's sha256 prefix. The same file listed twice therefore yields the same `doc_id`, the same version and the same pages, so every page gets the same ID twice. Chroma rejects the whole batch.

Deduping by content digest removes the identical items before they reach Chroma. Unique `doc_id`s stop two different files with the same name from producing confusing citations.

## Prevention

- Any new source that feeds `VectorIndex.build` must dedup on the inputs that make up its evidence ID. Deterministic IDs turn a config duplicate into a storage-layer crash.
- Validate user config at the boundary and raise `WorkspaceError` (exit 2, with the fix) rather than letting a library exception surface.
- Tests in `tests/test_project.py` pin the behavior:
  - `test_the_same_pdf_listed_twice_is_a_clear_error`: also asserts that no `state.json` was written.
  - `test_identical_pdfs_under_two_names_are_a_clear_error`
  - `test_folders_and_files_overlapping_are_indexed_once`: checks for unique ids and that the explicit title wins.
  - `test_init_accepts_folders_and_rejects_repeated_pdfs`

## Related Issues

- None filed. The bug was reported during a manual run of user mode against the pinned `requests` checkout.
