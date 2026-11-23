# Test-set results: vector search, generated wiki, and both (2026-11-23)

This report covers the paired experiment on the frozen test sets, run on 2026-11-23. It compares three ways of finding repository evidence for questions about a codebase:

- **A, vector search:** top-k code spans from an embedding index of the repository.
- **B, generated wiki:** search an OpenWiki wiki, read the matching sections, and follow their file and symbol references back to the code. Wiki text is never cited.
- **C, both:** A and B run in parallel, and the shared reranker chooses from both pools, under the same pool size and context budget.

Everything after retrieval is identical across conditions: the PDF search, the web policy, the reranker, the 8,000-token context budget, the generator and the citation format. Protocol: [benchmark-and-evaluation.md](../benchmark-and-evaluation.md). Decisions: [the plan](../plans/2026-11-22-1050-feat-authored-benchmark-experiment-plan.md) (KTD-B10 to KTD-B14).

## Summary

- **The primary comparison, B − A, is negative in both repositories.** Replacing vector search with the wiki route lowers claim correctness by 0.20 on Flask and on Requests.
  - On Flask, B was better on 3 questions and worse on 15 (exact sign test, two-sided, p = 0.008).
  - On Requests, B was better on 3 and worse on 8 (p = 0.23, not significant at this size).
- **The loss happens before ranking or generation: the wiki route does not reach the needed code.** It reached none of the annotated code on 5 of 25 Flask questions and 7 of 18 Requests questions.
- **Running both routes (C) recovers everything B loses, but adds little over vector search alone.** C − A is +0.02 on Flask and +0.06 on Requests, neither distinguishable from zero. C − B is +0.22 and +0.26 (p = 0.007 and 0.002).
- **C finds much more of the evidence than A, but most of it never reaches the answer.** Candidate recall rises by 0.17 in both repositories, while correctness barely moves. The reranker and the context budget are the bottleneck.
- **Citations are equally reliable in every condition** (support 0.79 to 0.90). Better grounding did not mean more correct answers.
- **B is the cheapest and fastest option per question; C is the most expensive.**
  - Over all questions, B uses 12–14% fewer tokens than A and is 9–11% faster (about 0.9 s on Flask, 1.5 s on Requests).
  - C uses 9% (Flask) to 20% (Requests) more tokens than A, with about the same latency.
  - B's saving comes from the reranker, which reads a smaller pool. Generation costs about the same in every condition.
  - Building each wiki was a one-time cost of 14 to 18 minutes; its tokens were not recorded.
  - Per correct answer, B is the most expensive, because it answers less correctly.

## Setup

| | Flask 3.1.2 | Requests |
|---|---|---|
| Pinned commit | `2c1b30d0503c` | `b25c87d7cb8d` |
| Test questions (frozen 2026-11-22) | 30 | 20 |
| Primary questions (need code evidence) | 25: 10 public SWE-QA, 6 code, 7 code+book, 2 code+web | 18: 10 public SWE-QA, 1 code, 7 code+web |
| Other questions | 2 book-only, 3 controls (unanswerable) | 2 controls |
| Code index | `text-embedding-3-large`, 1,362 chunks | `text-embedding-3-large`, 876 chunks |
| Wiki snapshot | OpenWiki 0.9.0, `gpt-5.6-luna`, effort medium, 12 pages, 132 claims, 1,055 s | OpenWiki 0.9.0, `gpt-5.6-luna`, effort medium, 836 s |
| Books | three Flask books, 638 pages | none |

**Models**

| Role | Model |
|---|---|
| Wiki generation, reranker, web-gate judge, answer generator | `gpt-5.6-luna`, temperature 0 |
| Embeddings | `text-embedding-3-large` |
| Judge (claim correctness, citation support) | `gpt-5.5-2026-04-23`, temperature 0 |
| DeepEval diagnostics (secondary) | `gpt-5.4-mini-2026-03-17` |

**Shared budgets:**
- Candidate pool for the reranker: 40.
- Context budget: 8,000 tokens.
- Repository candidates per condition: A 40, B 20 wiki-resolved spans, C 60. Because the pool is capped, C does not get more context than A.

**Web evidence:**
- A web-only record pass captured Tavily results once per question.
- Every condition then replayed the same stored results.
- Only authored questions may use the web: 2 in Flask, 7 in Requests. No public SWE-QA question can.

**Completeness:**
- All 150 answers (50 questions × 3 conditions) completed.
- There were no failed, degraded or empty-context answers, no unresolved citation IDs and no failed web captures.
- Reports are in `reports/{flask,requests}-test-{AB,AC,BC}/` and traces in `runs/*-test-001/`. Both directories are gitignored.

## Results

### Primary comparison (fixed before the runs)

Claim correctness is the share of each question's required claims the answer states correctly, as scored by the judge. Each comparison cell gives the mean difference, then the counts of questions where the second condition did better, tied or did worse, then the sign-test p-value.

| Repository | A | B | C | B − A | C − A | C − B |
|---|---|---|---|---|---|---|
| Flask (n = 25) | 0.71 | 0.51 | 0.73 | −0.20 · 3 / 7 / 15 · p = 0.008 | +0.02 · 7 / 13 / 5 · p = 0.77 | +0.22 · 13 / 10 / 2 · p = 0.007 |
| Requests (n = 18) | 0.58 | 0.38 | 0.64 | −0.20 · 3 / 7 / 8 · p = 0.23 | +0.06 · 7 / 7 / 4 · p = 0.55 | +0.26 · 13 / 4 / 1 · p = 0.002 |

The protocol fixed the paired differences and the better/tied/worse counts as the analysis. The sign tests are exploratory: two-sided, exact, ties excluded, with no correction for multiple comparisons.

### Retrieval, grounding and cost (primary questions)

| Metric | Flask A | Flask B | Flask C | Requests A | Requests B | Requests C |
|---|---|---|---|---|---|---|
| Evidence recall, candidates | 0.63 | 0.56 | **0.80** | 0.56 | 0.36 | **0.73** |
| Evidence recall, final context | 0.62 | 0.56 | **0.73** | 0.54 | 0.36 | **0.63** |
| Citation support | 0.90 | 0.89 | 0.90 | 0.79–0.81 | 0.82 | 0.83 |
| "Not established" claims per answer | 0.20 | 0.72 | 0.28 | 0.78 | 1.17 | 0.67 |
| Total tokens per question (all stages) | 23.3k | 20.5k | 25.7k | 22.4k | 21.3k | 28.0k |
| Mean latency per question | 10.8 s | 9.9 s | 10.7 s | 14.4 s | 12.8 s | 13.3 s |

- **Requests A shows two citation-support values.** It is 0.79 against B and 0.81 against C, because B's answer to `requests-pub-029` declined entirely (3 "not established" claims, nothing cited). That question therefore drops out of the A/B citation pairing.
### Tokens and latency

All token counts are OpenAI's reported usage for each call; none of the 637 recorded stage counts had to be estimated. Latency is wall-clock time per question, from a single run of each condition, so it includes API variance. The figures below cover all questions (Flask 30, Requests 20). The primary-question figures are in the table above.

**Per question, all questions**

| | Flask A | Flask B | Flask C | Requests A | Requests B | Requests C |
|---|---|---|---|---|---|---|
| LLM tokens | 23.6k | **20.4k** (−14%) | 25.7k (+9%) | 22.9k | **20.1k** (−12%) | 27.5k (+20%) |
| Latency | 10.2 s | **9.4 s** (−9%) | 10.2 s (0%) | 13.6 s | **12.2 s** (−11%) | 12.6 s (−8%) |
| Context sent to the generator | 7.7k | 7.7k | 7.7k | 7.3k | 6.9k | 7.6k |

**Paired, all questions** (exact sign test)

| | Flask | Requests |
|---|---|---|
| B fewer tokens than A | 24 of 30 · p = 0.001 | 12 of 20 · p = 0.50 |
| B faster than A | 21 of 30 · p = 0.043 | 14 of 20 · p = 0.12 |
| C more tokens than A | 22 of 30 · p = 0.016 | 15 of 20 · p = 0.041 |
| C faster than A | 15 of 30 · p = 1.0 | 11 of 20 · p = 0.82 |
| C more tokens than B | 30 of 30 | 20 of 20 |

**Per stage, mean per question** (latency, then input / output tokens)

| Stage | Flask A | Flask B | Flask C | Requests A | Requests B | Requests C |
|---|---|---|---|---|---|---|
| Code search (embedding call) | 0.65 s | — | 0.51 s | 0.55 s | — | 0.53 s |
| Wiki search + read + resolve (lexical, no model call) | — | 0.51 s | 0.51 s | — | 0.48 s | 0.51 s |
| Rerank | 3.9 s · 14.2k / 0.28k | 3.2 s · **11.1k** / 0.20k | 3.5 s · 16.3k / 0.23k | 4.3 s · 11.3k / 0.30k | 3.8 s · **9.6k** / 0.25k | 3.9 s · 15.9k / 0.27k |
| Generate | 5.1 s · 8.2k / 0.42k | 5.1 s · 8.1k / 0.42k | 5.2 s · 8.2k / 0.44k | 7.6 s · 7.7k / 0.61k | 6.7 s · 7.3k / 0.55k | 6.5 s · 8.0k / 0.55k |
| Web gate (only on web-eligible questions: Flask 2, Requests 7) | 2.8 s · 7.8k | 2.1 s · 8.1k | 3.0 s · 7.8k | 3.3 s · 8.1k | 3.2 s · 6.8k | 3.0 s · 8.0k |

**Where B's saving comes from**

- **Mostly the reranker.**
  - B puts 20 wiki-resolved spans in the pool, where A puts 40 code spans, so the reranker reads 15–22% fewer tokens and finishes about 0.5–0.75 s sooner.
  - That follows from the candidate budget as much as from the wiki itself. A with a 20-span pool would likely be about as cheap.
- **A little from retrieval.** Wiki search, read and resolve take about 0.5 s with no model call. Code search takes about 0.55–0.65 s, including one embedding call, which costs only about 50 tokens.
- **Not from generation.** Generation reads the same 8,000-token budget in every condition. Flask shows no difference. Requests B and C generate faster than A, by 0.9 s and 1.0 s, with slightly shorter answers. With one run per condition, that is within normal API variation.

**Where C's extra cost comes from.** C's reranker reads the largest pool (both routes, about 16k tokens), but C's latency matches A's. Its two retrieval routes run in parallel, and its rerank and generation were not slower in these runs.

**Per correct answer** (primary questions, tokens divided by mean correctness)

| | A | B | C |
|---|---|---|---|
| Flask | 32.9k | 40.4k | 35.2k |
| Requests | 38.6k | 56.0k | 43.9k |

B is cheapest per question but most expensive per unit of correctness. A is cheapest per correct answer in both repositories.

**One-time and fixed costs**

- **Wiki generation:** Flask 1,055 s, Requests 836 s. OpenWiki recorded no token usage or cost for these builds, so their token cost is unknown.
- **OpenWiki server start:** 1.2–1.4 s once per run of B or C, not per question.
- **Index building:** code and book indexes are built once and shared by A and C. Their time and embedding tokens are not part of these reports.

**Scoring cost** (approximate, reconstructed without calling the model again)

The judge's token use is not logged. It was reconstructed by rendering each of the 1,101 judge calls from the saved traces and the judge cache; all 1,101 matched a cached verdict, the same count the reports made.

| | Judge calls | Input tokens | Output tokens (lower bound) |
|---|---|---|---|
| Flask A / B / C | 222 / 198 / 222 | 168k / 139k / 180k | 12k / 10k / 12k |
| Requests A / B / C | 156 / 142 / 161 | 98k / 119k / 124k | 9k / 8k / 9k |
| Total | 1,101 | 828k | 60k |

- **Judge:** about $5.9 at DeepEval's listed price for `gpt-5.5` ($5 / $30 per million input / output tokens). Output is counted from the stored verdicts, which excludes any hidden reasoning tokens, so the real cost may be higher.
- **DeepEval:** about $4, as reported by DeepEval.
- **No dollar figure for answering.** The runs themselves can't be priced, because no price is recorded for `gpt-5.6-luna`.

### Wiki coverage (B and C)

`wiki_gold_coverage` is the share of a question's annotated evidence that the wiki route reached before reranking, at span level.

| | Mean coverage | Questions where the wiki reached none of the needed code |
|---|---|---|
| Flask (n = 25) | 0.55 | 5 |
| Requests (n = 18) | 0.44 | 7 |

When coverage is 0, nothing downstream can recover the evidence, because ranking and generation only see what retrieval returned. The pilot traced this to the wiki not citing the code at all, for example `morsel_to_cookie` or the `TestUrlFor` tests. Search is also lexical over the full question text, so a long question dilutes it. In the pilot, searching the symbol name alone found the right page.

### By question type (correctness)

| Subset | n | Flask A / B / C | n | Requests A / B / C |
|---|---|---|---|---|
| Public SWE-QA | 10 | 0.58 / 0.27 / 0.62 | 10 | 0.59 / 0.23 / 0.58 |
| Code only | 6 | 0.90 / 0.71 / 0.88 | 1 | 1.00 / 1.00 / 1.00 |
| Code + book | 7 | 0.79 / 0.69 / 0.76 | — | — |
| Code + web | 2 | 0.54 / 0.46 / 0.71 | 7 | 0.51 / 0.51 / 0.67 |
| Book only (sanity check) | 2 | 0.92 / 0.83 / 0.92 | — | — |

- **B's loss is concentrated on the public SWE-QA questions.** On Flask, B was worse on 8 of 10 and better on none; on Requests, worse on 7 of 10. These questions were written by the benchmark's authors about specific functions and tests, which is where wiki coverage is thinnest.
- **C's small gain over A on Requests comes from the code+web questions** (0.51 to 0.67, better on 5 of 7, worse on none). Every subset has fewer than 10 questions, so these are counts, not claims.

### Web gate

On Flask, every condition searched the web for both web-eligible questions. On Requests, A searched all 7, while B and C each searched 6. The conditions disagreed on 1–2 questions where one judge found its local evidence sufficient.

### Controls (unanswerable questions)

No condition declined any of the 3 Flask controls, and every condition declined 1 of the 2 Requests controls. The answers are sound but don't meet the scoring rule fixed before the runs.

- **The rule:** a decline counts only when every claim in the answer is "not established".
- **What the answers did:** each one says the requested fact is not established, then adds a related fact that is true and cited. For example, it says the default session cookie is named `session` but that the evidence does not establish who proposed the name.

The rule was not changed after the runs. Read this result as a property of the rule as much as of the system.

### DeepEval diagnostics (secondary, `gpt-5.4-mini`)

| | Flask A | Flask B | Flask C | Requests A | Requests B | Requests C |
|---|---|---|---|---|---|---|
| Contextual recall | 0.89 | 0.81 | 0.92 | 0.80 | 0.73 | 0.90 |
| Faithfulness | 1.00 | 0.95 | 0.98 | 0.99 | 1.00 | 0.97 |

The diagnostics rank the conditions in the same order as the primary metrics on context quality. Faithfulness is at or near its ceiling everywhere. The scores are on all questions; contextual recall is not scored for controls.

Four DeepEval rows first failed because the mini model returned invalid JSON. They succeeded when rescored. Total DeepEval cost was about $4.

## Interpretation

1. **A generated wiki is a poor replacement for code search on questions about specific code.** The wiki summarises the architecture and cites a subset of the code. Questions about a particular function, test or edge case often fall outside that subset, and B then has nothing to resolve. That is the coverage gap measured above (zero coverage on 20–39% of questions), and it accounts for most of B's loss.
2. **The wiki's evidence is mostly redundant with vector search.** Adding the wiki route to vector search (C) raised the candidate recall a lot (+0.17), but correctness only a little (+0.02 / +0.06). Much of what the wiki adds is either already among A's candidates or loses to them in the reranker under the same 8,000-token budget. The gain appears where questions combine code with other sources (code+web on Requests). Those groups are too small to conclude anything.
3. **Correctness is limited after retrieval, not only by it.** In C, candidate recall is 0.73–0.80, final-context recall 0.63–0.73, and correctness 0.64–0.73. Each step loses some, so in this pipeline better retrieval alone has diminishing returns.
4. **Grounding stays good whatever the retrieval.** Citation support is around 0.8–0.9 in every condition, and DeepEval faithfulness is near 1. The system rarely says things its context does not support. When retrieval misses, the generator says "not established" instead of inventing an answer, which is why B's gap claims rise as its correctness falls.

## Limitations

- **Sample size.** There are 25 and 18 primary questions in two Python repositories. Only the Flask B − A and the two C − B differences are clearly distinguishable from zero. Every subset has fewer than 10 questions, and nothing here generalises to other languages, larger codebases or other wiki generators.
- **One wiki, one run.** Each repository has a single wiki snapshot (OpenWiki 0.9.0, effort medium) and a single run per condition. Wiki generation is stochastic, and a different snapshot, higher effort or a larger model could cover more code. Temperature 0 does not make the generator fully deterministic.
- **Wiki search was used as shipped.** Condition B sends the full question to OpenWiki's lexical search, with no query rewriting or identifier extraction. The pilot suggests identifier-focused queries would help. That was deliberately not tuned, because B measures the tool as shipped.
- **Shared models.**
  - The wiki, reranker, web gate and generator all use `gpt-5.6-luna` (KTD10), so B's evidence was written by the same model that answers.
  - The judge is a different model from the same provider (`gpt-5.5`).
  - No human has checked the judge's verdicts yet; the co-author's stratified check is pending.
  - On the dev pilot, a smaller judge changed many scores and the condition gaps (KTD-B14). That shows how sensitive these numbers are to the scorer.
- **Benchmark construction.**
  - The researchers who built the system wrote the authored questions, with the code available. The questions were frozen before any test run.
  - Condition C was added after the dev pilot and before any test run (KTD-B12). The pilot saw only development questions.
  - Public SWE-QA reference answers are free text, and correctness against them depends on the judge.
- **Control scoring is strict.** "Every claim not established" penalises answers that correctly decline and also add true context. The 0 / 3 Flask decline rate overstates real hallucination on unanswerable questions.
- **Web evidence is a snapshot.** Tavily results were captured once, on 2026-11-23, and replayed. Live results would differ.
- **Cost is reported in tokens and seconds, not dollars.**
  - No price is recorded for `gpt-5.6-luna`.
  - OpenWiki recorded no tokens for the wiki builds.
  - Judge tokens are reconstructed, not measured, with output as a lower bound.
  - Latency comes from one run per condition and includes API variance.
  - B's token saving partly reflects its smaller candidate budget (20 vs 40), not only the wiki.
- **Baseline.** Condition A is a Langflow-style vector baseline. The original conference export was not available, so A makes no claim to be equivalent to it.

## Insights for practitioners

- **Keep code search as the main retrieval route.** If you add a generated wiki, add it alongside code search, as in C, not instead of it. Instead of it, as in B, cost 0.20 correctness here.
- **Measure wiki coverage before relying on a wiki.** Generated wikis describe structure and cite a subset of the code. A quick check: collect the files and symbols your users ask about, and see whether the wiki cites them. Wherever it does not, wiki-guided retrieval cannot help.
- **Query a lexical wiki search with identifiers, not whole questions.** Long natural-language questions dilute lexical search. Extracting symbol and file names first is a cheap fix to try, though it was not tested here.
- **Once you have more candidates, look at the reranker and context budget.** Combining routes raised candidate recall far more than correctness. Gains are more likely from better ranking, larger budgets or deduplication than from adding more sources.
- **A wiki route is cheaper per question, but not per correct answer.** Here B used 12–14% fewer tokens and ran about 10% faster than vector search, because it hands the reranker fewer candidates. It still needed 23–45% more tokens per unit of correctness.
- **Budget about 10–20% more tokens for a combined route, with no extra latency.** The two routes run in parallel, and the extra cost is almost all reranking a larger pool. Wiki generation is a one-time cost of about 15 minutes per repository and must be redone when the code changes.
- **To cut cost, shrink the reranker's input.** Reranking is about half to two thirds of the tokens per question (49–64%), more than generation. The pool size sets its cost.
- **Pinned, cited answers are auditable.** Citation support stayed around 0.8–0.9 in every configuration. Requiring a citation for every claim, and declines stated as "not established", made failures visible instead of silent.
- **Score with a fixed, different judge, and check it.** Changing only the judge model moved the condition gap as much as the effect being measured. Pin a dated judge snapshot, keep it different from the generator, and have people check a sample of its verdicts.

## Insights for `dwb` users

- **Leave `--wiki` off for questions about specific code.** In `dwb ask` and `dwb chat`, `--wiki` currently runs the wiki route instead of vector search (condition B), which was the weakest configuration in these tests. The default vector search (A) is the better choice. A follow-up could make `--wiki` run both routes, as in C.
- **Expect to see "not established" when evidence is missing.** The answer lists what the sources do not establish, rather than guessing. When that happens, rephrase the question with specific function or file names, or add `--pdf` or `--web` if the answer may lie outside the code.
- **Check the evidence.** `--show-context` prints every passage the model saw, and every claim cites one of them.
- **The web is searched only when local evidence is insufficient.** The answer footer says whether the web was used.
- **Rebuild the wiki after large code changes** (`dwb index`). A stale wiki can point to code that has moved, and its references may then fail to resolve.

## Reproducing

```sh
dwb experiment run configs/manifests/<repo>-A.yaml --web-only           # record pass
dwb experiment run configs/manifests/<repo>-{A,B,C}.yaml --web-mode replay
dwb experiment evaluate runs/<repo>-A-test-001 runs/<repo>-B-test-001 --out reports/<repo>-test-AB --deepeval
# likewise A/C and B/C
```

Judge verdicts and DeepEval rows are cached (`artifacts/judge_cache.jsonl`, `artifacts/deepeval_cache.jsonl`), so regenerating the reports from the saved traces costs nothing.
