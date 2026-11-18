# Question records

- `test/<repo>.jsonl`: the frozen evaluation set (public SWE-QA items plus custom cross-source items). After freezing, `test/<repo>.jsonl.sha256` holds the content hash, and each run manifest's `question_set.hash` must match it.
- `dev/<repo>.jsonl`: development questions, used only for tuning and pilots. They must not share IDs or question text with the test set.
- `selections/<repo>.yaml`: the human-authored SWE-QA row selection (with include/exclude reasons) that produces draft public records. Rows are 0-based line indexes in the pinned split file (`<split>:<index>`).
- `drafts/<repo>.public.jsonl` and `drafts/<repo>.excluded.jsonl`: output of `dwb benchmark draft`. Annotate the drafts and copy them into the test file; `draft` never overwrites them without `--overwrite`.

The loop: `dwb benchmark draft` → annotate → `dwb benchmark validate` until clean → `dwb benchmark freeze` (copy the printed hash into the manifests) → `dwb benchmark check <dev> <test>`. See docs/usage.md, "Building the test set".

Records follow `dwb.benchmark.schemas.QuestionRecord`. People curate, annotate, and second-review them; they are never synthesized.
