# Question Sets and Evaluation Protocol

Status as of 2026-11-22 (test sets frozen). This document describes where the question sets stand and how the paper's experiment will be run and scored. The implementation plan is `docs/plans/2026-11-22-1050-feat-authored-benchmark-experiment-plan.md`; this document is the readable summary of it.

## 1. The experiment in one paragraph

Two conditions answer the same questions about two pinned repositories, Flask 3.1.2 and Requests 2.32.5. **Condition A** finds repository evidence by vector search over the code. **Condition B** finds it by following the generated OpenWiki pages to the code they describe. Everything else is shared: the PDF retriever, web search (Tavily, with replayed results), the sufficiency judge that decides whether to search the web, the reranker, the context budget, the generator and the citation format. Each question is answered once per condition, and the answers are scored offline from the saved traces.

## 1a. Experimental setup (as it will be described in the paper)

**Design.** A paired, within-question comparison of two configurations of one question-answering system. The system (a LangGraph pipeline) is treated as a configurable black box. It takes a question and a configuration, and returns a cited answer plus a trace of the evidence it used. Every question is answered under both configurations, so each question serves as its own control.

**System under test.**
- **Inputs:** the question text and the question's allowed source types (code, PDF, web).
- **Configuration:** the repository commit, the PDF corpus, the web policy and replayed web results, the models and prompts, and the retrieval and context budgets.
- **Outputs:** an answer made of claims that cite evidence items (code spans, PDF pages, web passages), and a trace recording the retrieved candidates, the final context, the citations, cost and latency.

**Independent variable.** One configuration parameter, the repository-retrieval mode:
- **A (baseline):** repository evidence found by vector search over the code.
- **B (wiki-guided):** repository evidence found through generated OpenWiki pages, resolved to code at the same commit. Wiki text is not cited.

**Controlled variables.** Every other configuration parameter is identical in A and B. A paired configuration check (`dwb experiment check`) verifies this before any run.

**Subjects.** Two Python libraries with public SWE-QA splits and links to external specifications: Flask 3.1.2 (with three Flask books as PDF sources) and Requests 2.32.5 (with web sources only).

**Questions.** About 30 per Flask and 19 per Requests, in four groups:
- SWE-QA public questions (code-only);
- authored single-source questions (code or book);
- authored cross-source questions (code with a book or a web page);
- unanswerable controls.

Authored questions carry expected claims and annotated evidence spans. The set is frozen and hashed before any run.

**Dependent measures.** Computed only from the system's outputs and traces:
- **Primary:**
  - evidence recall at the candidate and final-context stages;
  - required-claim correctness;
  - citation support and coverage;
  - decline accuracy on controls;
  - web-search use;
  - cost and latency.
- **Diagnostic:** DeepEval Contextual Recall and Faithfulness.

**Procedure.**
1. A pilot on separate development questions.
2. A record pass that stores all web results.
3. The paired runs of configurations A and B, both replaying the same stored web evidence.
4. Offline scoring of the saved outputs by a judge model different from the system's generator.

**Analysis.** For each repository: paired B − A differences, with improved, tied and regressed counts, over the questions that need code evidence. Groups under about 10 questions, book-only questions and controls are reported descriptively. A co-author independently checks a stratified sample of judgments.

```mermaid
flowchart LR
    subgraph Q["Frozen question set (per repository)"]
        Q1["SWE-QA public<br/>(code)"]
        Q2["Authored single-source<br/>(code or book)"]
        Q3["Authored cross-source<br/>(code + book / web)"]
        Q4["Unanswerable controls"]
    end

    subgraph CFG["Configuration"]
        IV{"Repository retrieval<br/>(independent variable)"}
        IVA["A: vector search"]
        IVB["B: wiki-guided"]
        FIX["Fixed parameters<br/>(identical in A and B):<br/>commit, PDF corpus, replayed web,<br/>models, prompts, budgets"]
        IV --- IVA
        IV --- IVB
    end

    SYS[["QA system<br/>(LangGraph pipeline,<br/>black box)"]]

    Q --> SYS
    CFG --> SYS
    SYS --> OUT[("Cited answers + traces<br/>for A and for B")]

    subgraph E["Offline evaluation (separate judge model)"]
        M1["Evidence recall"]
        M2["Claim correctness"]
        M3["Citation support and coverage"]
        M4["Decline accuracy (controls)"]
        M5["Web-search use, cost, latency"]
        M6["DeepEval diagnostics"]
    end

    OUT --> E
    E --> P["Paired analysis per repository:<br/>B - A differences,<br/>improved / tied / regressed"]
```

## 2. Question sets today

| File | Contents | Status |
|---|---|---|
| `data/questions/test/flask.jsonl` | 30 questions: 10 SWE-QA public, 17 authored (code, book, code+book, code+web), 3 controls | Reviewed, frozen (`6e376067…`) |
| `data/questions/test/requests.jsonl` | 20 questions: 10 SWE-QA public, 8 authored (code, code+web), 2 controls | Reviewed, frozen (`5bb97f34…`) |
| `data/questions/dev/flask.jsonl`, `dev/requests.jsonl` | 10 and 7: SWE-QA development rows and the generated variants (smoke test only) | Not frozen; no overlap with test |
| `data/questions/selections/` | Seeded SWE-QA samples (test seed 20261122, dev seed 20261123) | Final |
| `data/questions/cross_source_benchmark.md`, `tests/eval/.dataset.json` | The original 25 authored questions and the DeepEval dataset with the 10 variants | Source and provenance records; nothing reads them |
| `data/questions/drafts/` | Re-sourcing notes, SWE-QA drafts and exclusions, the web pre-check report | Records |
| `configs/manifests/<repo>-{A,B}.yaml`, `<repo>-{A,B}-dev.yaml` | Test and development manifest pairs; Flask uses the book index `pdf-flask-books-v1-5b41dac5d845`, Requests has PDFs off | Pass `dwb experiment check` |

### Why the authored benchmark was reworked before it could run

- **Format.** The harness's question format requires every authored question to cite code plus an external source. Only 11 of the 25 do. It has no type for unanswerable controls, and no question names a repository.
- **Corpus change.** The evaluation PDFs for Flask are now the three Flask books, not the RFCs used during testing. Requests gets no PDFs; it uses web pages instead. Ten questions cited RFCs, so they need new sources.
- **Web rule.** Question 19 cited `requests.readthedocs.io`, which the experiment excludes: a project's own docs restate the code and would let either condition answer without finding it.
- **Old books.** The books describe Flask 0.12.2 (Grinberg) and 0.10.1 (Aggarwal). Answers must hold for Flask 3.1.2, so book claims that are out of date are excluded from expected answers. Aronowitz states no version and supports no question.

## 3. The frozen test set

Per repository: the re-sourced authored questions, plus 10 SWE-QA public questions drawn at random (seed 20261122) from the eligible rows. Every question below was reviewed and accepted; the IDs are in `data/questions/REVIEW.md`.

### Flask (30 questions)

| Group | Questions | Evidence | Status |
|---|---|---|---|
| Code-only | 1–6 | Flask code | Frozen |
| Book-only | 12′ (request object, rewritten), 13 (contexts) | Grinberg pages | Frozen |
| Code + book | 14′ (automatic OPTIONS), 15′ (static-file route), 17′ (WSGI middleware), N1–N4 (new) | Flask code + Grinberg or Aggarwal | Frozen |
| Code + web | 2 new (session-cookie attributes, `Vary: Cookie`) | Flask code + MDN / rfc-editor.org | Frozen (the OPTIONS draft was dropped as a duplicate of 14′) |
| Controls | 21, 23, 25 | none (expected: decline) | Frozen |
| SWE-QA public | 10 | Flask code | Frozen |

### Requests (20 questions)

| Group | Questions | Evidence | Status |
|---|---|---|---|
| Code-only | 7 | Requests code | Frozen |
| Code + web | 10′, 16′, 18′, 19′ (re-sourced from RFC PDFs and the docs page), 3 new (fragments on redirect, body framing, auth headers on redirect) | Requests code + rfc-editor.org / MDN | Frozen |
| Controls | 22, 24 | none (expected: decline) | Frozen |
| SWE-QA public | 10 | Requests code | Frozen |

### Removed from the test set

- **Items 8, 9, 11** (safe and idempotent methods, CGI variables): no book covers them.
- **Item 20** (the project paper): the paper is in no corpus, and the question cannot distinguish A from B.

### Development set (for the pilot)

- The existing SWE-QA development question per repository.
- The four RFC-based development questions, re-sourced to the new corpus policy.
- The generated variants whose parent question survives, re-sourced like their parents. They reword test questions, so they serve only as a harness smoke test. Prompt or configuration changes may only come from failures on the other development questions. Each variant is listed against its parent for disclosure.

### Rules for every test question

- **Human review.** Questions drafted by Claude are reviewed and edited by you, and their provenance records it. The paper discloses that drafting happened after the wikis were generated. Drafting and review must not look at the wikis or any condition's traces.
- **Allowed sources.** Authored answerable questions may use every local source their repository has (Flask: code and PDFs; Requests: code). Only code+web questions also allow web. Controls never allow web. SWE-QA questions are code-only.
- **Web retrievability measured, not filtered.** Before freezing, each web question was sent to Tavily exactly as the system will send it and scored against its gold evidence; an RFC also matches its official copies (datatracker.ietf.org, httpwg.org). Results are recorded in provenance, and no question was reworded or dropped because of them: 1 of 9 web test questions retrieved its gold page (`data/questions/drafts/webcheck-2026-11-22.json`). Web-stage recall is therefore an unconditioned estimate, and low by construction, since the system queries the web with the full code-oriented question.
- **Validated and frozen.** `dwb benchmark validate` checks every code line range and PDF page against the pinned sources. `dwb benchmark freeze` records a hash that each run manifest must match. `dwb benchmark check` confirms development and test sets share no question.

## 4. Sources

| | Flask | Requests |
|---|---|---|
| Repository | `artifacts/checkouts/flask-3.1.2` at `2c1b30d0` | `artifacts/checkouts/requests-v2.32.5` at `b25c87d7` |
| PDFs | Grinberg, Aggarwal and Aronowitz books (new index) | none |
| Wiki (condition B) | pinned OpenWiki snapshot | pinned OpenWiki snapshot |
| Web | Tavily, replayed; excludes flask.palletsprojects.com, werkzeug.palletsprojects.com, github.com, pypi.org | Tavily, replayed; excludes requests.readthedocs.io, docs.python-requests.org, github.com, pypi.org |

## 5. How a question is answered

1. Repository search (A or B) and, where allowed, PDF search run in parallel and are merged.
2. If the question allows web, the sufficiency judge decides whether the local evidence answers every part of the question. Web is searched only if it does not, if there is no local evidence, or if the judge fails.
3. The reranker orders the evidence, the context is cut to 8,000 tokens, and the generator answers as cited claims. A claim the evidence does not support is written as "not established by the evidence".

## 6. How the experiment is run

1. **Pilot** on the development set, A and B per repository, to check the pipeline end to end.
2. **Record pass** per repository: Tavily is called live and every web result is stored, including for questions whose judge says the local evidence is enough. `web_capture_failed` must be 0.
3. **Paired runs**: condition A and condition B on the frozen test set, both replaying the stored web results, so both see identical web evidence.
4. **Failures** are scored as 0. A question is rerun only for an infrastructure fault (provider outage, timeout), and then in both conditions, with the cause logged.

## 7. How answers are scored

All scoring runs afterwards, with `dwb experiment evaluate runs/<A> runs/<B> --out reports/<name> --deepeval`, from the saved traces. The judge model (`gpt-5.5-2026-04-23`) differs from the generator.

### Primary metrics

| Metric | What it measures | Applies to |
|---|---|---|
| Evidence recall (candidates, final context) | Did the system retrieve the annotated evidence spans? | Answerable questions with annotated evidence |
| Claim correctness | Share of the required claims the answer states correctly | Answerable questions |
| Citation support and coverage | Are the answer's claims supported by the evidence they cite, and is every claim cited? | Answerable questions |
| Decline accuracy (`control_correct`) | Did the system decline (all claims "not established")? | Controls only |
| Web gate | How often each condition searched the web; agreement between A and B | Web-eligible questions |
| Cost and latency | Tokens and time per question, including the judge | All |

### Primary comparison

Fixed before the runs. Per repository, the paired A − B differences, plus improved, tied and regressed counts, over the questions that need code evidence: SWE-QA, code-only, code+book and code+web. Book-only questions and controls are sanity checks, expected to show no A/B difference. Any group with fewer than about 10 questions is reported as counts, without comparative claims.

### DeepEval diagnostics (secondary)

DeepEval runs as a library over the same saved traces. There is no tracing, no Confident AI and no generated data. For each question and condition it receives the question, the answer as shown to the user, the text of the passages that reached the generator, and the reference answer.

| Question type | Contextual Recall | Faithfulness |
|---|---|---|
| Answerable (all types) | Runs: did the final context contain what the reference needs? | Runs: is everything the answer states supported by that context? |
| Control | Skipped: the reference is a decline | Runs: catches answers made up despite missing evidence |
| No answer or no context | Skipped, with the reason recorded | Skipped |
| Failed question | No row (already scored 0) | No row |

Results go to `deepeval_diagnostics.csv` per question, and to a summary per question type and condition, with each metric's cost. DeepEval does not pin its own temperature, so these scores can vary between runs; they are reported as diagnostics next to the primary metrics, never instead of them.

## 8. What happens next, and who does it

| Step | Who | Status |
|---|---|---|
| Extend the question format, evaluation changes, corpus and manifests | Claude | Done |
| Review the drafts and the SWE-QA sample | You | Done |
| Convert, web pre-check (measured), validate and freeze | Claude, with your sign-off | Done |
| Pilot on the development set, record pass, paired runs, evaluation | Claude (model and Tavily cost) | Next |
| Independent check of a stratified sample by a second reviewer | A co-author | After freezing |
| Update the paper's method text with the final numbers | Claude drafts, you edit | After the runs |
