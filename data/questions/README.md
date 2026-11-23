# Question records

- `test/<repo>.jsonl`: the frozen evaluation set (public SWE-QA items plus custom cross-source items). After freezing, `test/<repo>.jsonl.sha256` holds the content hash, and each run manifest's `question_set.hash` must match it.
- `dev/<repo>.jsonl`: development questions, used only for tuning and pilots. They must not share IDs or question text with the test set.
- `selections/<repo>.yaml`: the human-authored SWE-QA row selection (with include/exclude reasons) that produces draft public records. Rows are 0-based line indexes in the pinned split file (`<split>:<index>`).
- `drafts/<repo>.public.jsonl` and `drafts/<repo>.excluded.jsonl`: output of `dwb benchmark draft`. Annotate the drafts and copy them into the test file; `draft` never overwrites them without `--overwrite`.

The loop: `dwb benchmark draft` → annotate → `dwb benchmark validate` until clean → `dwb benchmark freeze` (copy the printed hash into the manifests) → `dwb benchmark check <dev> <test>`. See docs/usage.md, "Building the test set".

Records follow `dwb.benchmark.schemas.QuestionRecord`. People curate, annotate, and second-review them; they are never synthesized.

## Current sets (2026-11-22)

- `test/flask.jsonl` (30) and `test/requests.jsonl` (20): SWE-QA public questions (seeded sample, `selections/<repo>.yaml`), authored questions from `cross_source_benchmark.md` (re-sourced to the Flask books and to web pages for Requests), and unanswerable controls (`kind: control`). All 50 reviewed and accepted (`REVIEW.md`). Frozen on 2026-11-22: Flask `6e376067…`, Requests `5bb97f34…` (`test/<repo>.jsonl.sha256`, also in `configs/manifests/<repo>-{A,B,C}.yaml`). Do not edit; a change needs a new freeze and new run IDs.
- `dev/flask.jsonl` (10) and `dev/requests.jsonl` (7): SWE-QA development rows (`selections/<repo>-dev.yaml`) and generated variants.
- Drafting notes: `drafts/web-items.draft.md`, `drafts/flask-book-resourcing.draft.md`.
- Web retrievability pre-check (measured, not filtered; KTD-B9): `drafts/webcheck-2026-11-22.json`; each web question's result is in its provenance.

### Generated variants (development only)

These were generated with `deepeval generate` and reword test questions, so they are a harness smoke test only, never used for tuning. Disclose them in the paper.

| Variant | Parent test question |
|---|---|
| flask-dev-var-26 | flask-cs-02 |
| flask-dev-var-27 | flask-cs-03 |
| flask-dev-var-28 | flask-cs-01 |
| flask-dev-var-29 | flask-cs-05 |
| flask-dev-var-30 | flask-cs-06 |
| flask-dev-var-33 | flask-cs-13 |
| requests-dev-var-32 | requests-cs-10r |
| requests-dev-var-34 | requests-cs-16r |
| requests-dev-var-35 | requests-cs-19r |

Variant 31 was dropped with its parent (item 8).
