# Statistical tests for the test-set results (2026-11-23)

This document describes the statistical tests run on the paired experiment of [`2026-11-23-test-set-results.md`](2026-11-23-test-set-results.md), explains why each test was chosen, and interprets the results, including effect sizes. The tests are computed from the saved evaluation reports only; no model is called again.

- Script: [`scripts/paired_stats.py`](../../scripts/paired_stats.py) (tests: `tests/test_paired_stats.py`).
- Output: [`stats/paired_stats.csv`](stats/paired_stats.csv) and [`stats/paired_stats.json`](stats/paired_stats.json), 54 comparisons (6 metrics × 3 pairs × 2 repositories + pooled).
- Reproduce: `pip install -e '.[analysis]'`, then `python scripts/paired_stats.py --reports reports --out docs/results/stats`. Seeds are fixed (20261124), and a second run produces byte-identical output.

## 1. Summary

| Comparison | Correctness: mean difference [95% CI] | Wilcoxon p | Permutation p | Effect size (rank-biserial) | Verdict |
|---|---|---|---|---|---|
| **B − A, Flask** (primary) | −0.20 [−0.32, −0.10] | 0.003 | 0.001 | −0.80 | B worse; large effect |
| **B − A, Requests** (primary) | −0.20 [−0.35, −0.05] | 0.052 | 0.028 | −0.77 | Same size and direction; borderline significance |
| B − A, pooled | −0.20 [−0.30, −0.11] | <0.001 | <0.001 | −0.79 | B worse |
| C − A, Flask | +0.02 [−0.04, +0.08] | 0.53 (Holm 0.57) | 0.54 | +0.22 | No detectable difference |
| C − A, Requests | +0.06 [−0.04, +0.15] | 0.29 (Holm 0.57) | 0.32 | +0.39 | No detectable difference |
| C − A, pooled | +0.03 [−0.02, +0.08] | 0.23 | 0.20 | +0.32 | No detectable difference |
| C − B, Flask | +0.22 [+0.11, +0.35] | 0.002 (Holm 0.007) | 0.001 | +0.90 | C better; very large effect |
| C − B, Requests | +0.26 [+0.13, +0.40] | 0.001 (Holm 0.005) | <0.001 | +0.93 | C better; very large effect |

- **The primary result holds up.** Replacing vector search with the wiki route (B − A) lowers claim correctness by 0.20 in both repositories, with a large effect size in both. On Flask every test is significant. On Requests the confidence interval excludes zero and the permutation test is significant (p = 0.028), while the Wilcoxon test just misses the 0.05 level (p = 0.052); the honest description is "the same effect, with borderline significance at n = 18".
- **Adding the wiki to vector search (C − A) shows no detectable gain.** All intervals include zero. Pooled over both repositories, the gain is between −0.02 and +0.08.
- **Adding vector search to the wiki (C − B) clearly helps**, also after the Holm correction.
- **Citation support does not differ between any conditions** (all p > 0.77).
- **Cost:** C uses more tokens than A in both repositories; B uses fewer tokens than A on Flask but not detectably on Requests; latency differences are not reliable per repository.

## 2. Data and unit of analysis

- **Unit:** one question. Every question was answered once under each of the three conditions, so the comparison is paired: each question is compared with itself, which removes the large differences in difficulty between questions.
- **Questions:** the primary questions fixed before the runs, those that need code evidence (SWE-QA public, authored code, code+book and code+web): 25 for Flask and 18 for Requests, 43 pooled. Book-only questions and controls are excluded, as in the protocol. Citation support has 17 Requests pairs for A/B and B/C, because one B answer declined entirely and cited nothing.
- **Difference:** second condition minus first, as in the comparison's name. For B − A a negative value means B scored lower. For correctness and recall, higher is better; for tokens and latency, lower is better.
- **Input:** `reports/<repo>-test-<XY>/per_question.csv`, written by `dwb experiment evaluate`. Each report holds one pair of conditions, so each comparison reads its own report.

The metrics are the ones defined in the protocol ([`../benchmark-and-evaluation.md`](../benchmark-and-evaluation.md)): claim correctness (0–1, the weighted share of required claims the judge finds stated, with 0.5 for partly stated), evidence recall in the candidates and in the final context (0–1), citation support (0–1), total LLM tokens and latency per question.

## 3. Tests and why they were chosen

The scores are bounded (0–1), take few distinct values (0, 0.5, 1 and a few averages), contain many ties, and the samples are small (18–25). A paired t-test assumes roughly normal differences, which these are not, so the analysis relies on tests that do not assume a distribution, and reports several of them so that their agreement or disagreement is visible.

| Test | What it uses | Why |
|---|---|---|
| **Sign test** (exact, two-sided, ties dropped) | Only the direction of each non-zero difference | Fewest assumptions; reported first in the results document. It ignores how large the differences are, so it has low power. |
| **Wilcoxon signed-rank test** (Pratt's handling of zeros) | The ranks of the absolute differences and their signs | The standard paired non-parametric test; more powerful than the sign test because large differences count more. Pratt's method keeps zero differences in the ranking instead of discarding them, which matters with many ties. With ties, SciPy uses the normal approximation. |
| **Sign-flip permutation test** (200,000 random flips, two-sided, add-one correction) | The mean difference itself | If the conditions were equivalent, each question's difference would be equally likely to have either sign. Flipping signs at random gives the distribution of the mean under that hypothesis, without assuming any shape. It tests the same quantity the paper reports (the mean difference). |
| **Bootstrap 95% confidence interval** (20,000 resamples of questions, percentile method) | The mean difference | Shows the plausible range of the effect, not only whether it is zero. Percentile intervals can be slightly too narrow with n < 20; treat interval edges near zero with care. |
| **Matched-pairs rank-biserial correlation** | Ranks of the non-zero differences | Effect size; see Section 4. |
| **Holm correction** | Wilcoxon p-values of the secondary correctness comparisons | Controls the chance of any false positive across the four secondary tests (C − A and C − B in each repository). The primary B − A test per repository was fixed before the runs and is not adjusted. |

**Pooled analysis.** The pooled rows combine the 43 primary questions of both repositories. Each question is still paired with itself, and the sign-flip permutation and bootstrap resample questions, so pooling does not mix questions from different repositories within a pair. Pooling increases power but assumes the effect has a similar direction in both repositories, which holds for every metric where either repository shows a clear effect.

**Exploratory status.** The protocol fixed the paired differences and the better/tied/worse counts as the analysis of record. These tests were added after the runs to quantify uncertainty. Only B − A per repository was pre-specified as primary; everything else is secondary or exploratory, and no correction is applied across metrics.

## 4. Effect size: the matched-pairs rank-biserial correlation

The rank-biserial correlation r is computed from the non-zero paired differences: rank their absolute values, add the ranks of the positive differences (R+) and of the negative ones (R−), and take r = (R+ − R−) / (R+ + R−).

- **Range:** −1 to +1. r = +1 means every question that changed improved in the second condition; r = −1 means every one got worse; r = 0 means gains and losses balance out.
- **Reading it:** it is the probability that a changed question moved up minus the probability that it moved down, weighted by how much it moved. For example, r = −0.80 for Flask B − A means that among questions whose score changed, losses outweighed gains by a wide margin.
- **Rough guide:** |r| around 0.1 is small, around 0.3 medium, and 0.5 or more large. These thresholds are conventions, not rules; the mean difference with its interval is the more interpretable quantity for correctness and recall.
- **Why not Cohen's d:** d divides the mean difference by the standard deviation of the differences and assumes they are roughly normal, which bounded, tied scores are not. The rank-biserial correlation needs no such assumption and is the effect size that matches the Wilcoxon test.
- **When r and the mean disagree in sign:** r counts how many questions moved and by how many ranks, while the mean is dominated by large single changes. For citation support B/C on Flask the mean is +0.009 but r is −0.19; both say the difference is negligible.

## 5. Results by metric

Columns: mean of each condition, mean difference with 95% bootstrap interval, number of questions higher / equal / lower in the second condition, p-values, and rank-biserial r. Holm-adjusted Wilcoxon p-values are given for the secondary correctness comparisons.

### 5.1 Claim correctness (RQ2)

| Pair | Repository | n | Means | Difference [95% CI] | Higher / equal / lower | Sign p | Wilcoxon p | Permutation p | r |
|---|---|---|---|---|---|---|---|---|---|
| B − A | Flask | 25 | 0.71 → 0.51 | −0.20 [−0.32, −0.10] | 3 / 7 / 15 | 0.008 | 0.003 | 0.001 | −0.80 |
| B − A | Requests | 18 | 0.58 → 0.38 | −0.20 [−0.35, −0.05] | 3 / 7 / 8 | 0.23 | 0.052 | 0.028 | −0.77 |
| B − A | Pooled | 43 | 0.66 → 0.45 | −0.20 [−0.30, −0.11] | 6 / 14 / 23 | 0.002 | <0.001 | <0.001 | −0.79 |
| C − A | Flask | 25 | 0.71 → 0.73 | +0.02 [−0.04, +0.08] | 7 / 13 / 5 | 0.77 | 0.53 (Holm 0.57) | 0.54 | +0.22 |
| C − A | Requests | 18 | 0.58 → 0.64 | +0.06 [−0.04, +0.15] | 7 / 7 / 4 | 0.55 | 0.29 (Holm 0.57) | 0.32 | +0.39 |
| C − A | Pooled | 43 | 0.66 → 0.69 | +0.03 [−0.02, +0.08] | 14 / 20 / 9 | 0.41 | 0.23 | 0.20 | +0.32 |
| C − B | Flask | 25 | 0.51 → 0.73 | +0.22 [+0.11, +0.35] | 13 / 10 / 2 | 0.007 | 0.002 (Holm 0.007) | 0.001 | +0.90 |
| C − B | Requests | 18 | 0.38 → 0.64 | +0.26 [+0.13, +0.40] | 13 / 4 / 1 | 0.002 | 0.001 (Holm 0.005) | <0.001 | +0.93 |
| C − B | Pooled | 43 | 0.45 → 0.69 | +0.24 [+0.15, +0.33] | 26 / 14 / 3 | <0.001 | <0.001 | <0.001 | +0.90 |

**Interpretation.**

- **B − A.** In both repositories the wiki route loses about a fifth of a required claim per question on average, and the effect is large (r ≈ −0.8): most questions that changed got worse. On Flask the result is significant by every test. On Requests the sign test (p = 0.23) is weak because it ignores the size of the differences; the tests that use the sizes give p = 0.052 (Wilcoxon) and p = 0.028 (permutation), and the interval [−0.35, −0.05] excludes zero. The Requests result should be reported as consistent with Flask and borderline significant on its own; pooled over both repositories, the effect is clearly significant.
- **C − A.** Combining both routes gives a small positive mean in both repositories, but every interval includes zero and no test is close to significance. The data are compatible with no gain and rule out gains larger than about 0.08 (Flask) and 0.15 (Requests). The moderate r (+0.22, +0.39) reflects that slightly more questions improved than worsened, not a reliable effect.
- **C − B.** Adding vector search to the wiki route helps strongly in both repositories (r ≈ +0.9; only 3 of 43 questions got worse), and survives the Holm correction.

### 5.2 Evidence recall (RQ1)

| Metric | Pair | Repository | Difference [95% CI] | Higher / equal / lower | Wilcoxon p | Permutation p | r |
|---|---|---|---|---|---|---|---|
| Candidates | B − A | Flask | −0.07 [−0.24, +0.11] | 5 / 11 / 9 | 0.41 | 0.50 | −0.13 |
| Candidates | B − A | Requests | −0.19 [−0.45, +0.07] | 4 / 5 / 9 | 0.17 | 0.18 | −0.41 |
| Candidates | B − A | Pooled | −0.12 [−0.27, +0.03] | 9 / 16 / 18 | 0.12 | 0.14 | −0.28 |
| Candidates | C − A | Flask | +0.17 [+0.08, +0.28] | 10 / 15 / 0 | 0.002 | 0.002 | +1.00 |
| Candidates | C − A | Requests | +0.17 [+0.06, +0.31] | 7 / 11 / 0 | 0.009 | 0.016 | +1.00 |
| Candidates | C − B | Pooled | +0.29 [+0.19, +0.40] | 25 / 18 / 0 | <0.001 | <0.001 | +1.00 |
| Final context | B − A | Pooled | −0.11 [−0.27, +0.04] | 10 / 15 / 18 | 0.16 | 0.16 | −0.27 |
| Final context | C − A | Flask | +0.11 [−0.00, +0.24] | 8 / 14 / 3 | 0.11 | 0.087 | +0.58 |
| Final context | C − A | Requests | +0.09 [−0.05, +0.25] | 6 / 8 / 4 | 0.44 | 0.28 | +0.33 |
| Final context | C − A | Pooled | +0.10 [+0.01, +0.20] | 14 / 22 / 7 | 0.084 | 0.039 | +0.48 |
| Final context | C − B | Pooled | +0.22 [+0.12, +0.32] | 19 / 21 / 3 | <0.001 | <0.001 | +0.86 |

**Interpretation.**

- **C finds more evidence than A at the candidate stage, with certainty.** C was never worse than A on any question (r = +1.00), and the gain of 0.17 is significant in both repositories. This is expected by construction, since C's candidates include A's, but the size of the gain shows how much the wiki route adds before ranking.
- **Most of that gain is lost at reranking.** In the final context the C − A gain shrinks to about 0.10 and is significant only when pooled, and only by the permutation test (p = 0.039; Wilcoxon p = 0.084). This supports the interpretation that ranking and the context budget, not retrieval, limit C.
- **B's lower recall than A is not statistically detectable** (pooled p ≈ 0.14–0.16, r ≈ −0.28), although B's correctness is clearly lower. Recall is measured only against the annotated spans, while correctness also captures answers that lack the code needed, so the coverage diagnostic (`wiki_gold_coverage`) is the more direct evidence for B's retrieval gap.

### 5.3 Citation support (RQ2)

| Pair | Pooled difference [95% CI] | Higher / equal / lower | Wilcoxon p | r |
|---|---|---|---|---|
| B − A | +0.005 [−0.07, +0.09] | 11 / 18 / 13 | 0.89 | +0.05 |
| C − A | +0.010 [−0.05, +0.08] | 12 / 21 / 10 | 0.85 | −0.05 |
| C − B | +0.010 [−0.06, +0.09] | 12 / 18 / 12 | 0.93 | −0.05 |

No comparison is close to significance in either repository (all per-repository p > 0.77), and every effect size is near zero. The pooled intervals exclude differences larger than about 0.09 in either direction. Retrieval mode does not change how well the generator's citations support its claims. These tests cannot prove the conditions are equal; an equivalence test (TOST) with a margin chosen in advance, for example ±0.1, would be needed to claim that formally.

### 5.4 Tokens and latency (RQ3)

| Metric | Pair | Repository | Difference [95% CI] | Higher / lower | Wilcoxon p | Permutation p | r |
|---|---|---|---|---|---|---|---|
| Tokens | B − A | Flask | −2,823 [−4,140, −1,469] | 6 / 19 | <0.001 | <0.001 | −0.72 |
| Tokens | B − A | Requests | −1,140 [−4,654, +2,613] | 8 / 10 | 0.52 | 0.56 | −0.18 |
| Tokens | B − A | Pooled | −2,118 [−3,810, −336] | 14 / 29 | 0.007 | 0.022 | −0.47 |
| Tokens | C − A | Flask | +2,361 [+1,265, +3,550] | 19 / 6 | <0.001 | <0.001 | +0.75 |
| Tokens | C − A | Requests | +5,555 [+3,094, +8,178] | 15 / 3 | <0.001 | <0.001 | +0.86 |
| Tokens | C − B | Pooled | +5,816 [+5,043, +6,580] | 43 / 0 | <0.001 | <0.001 | +1.00 |
| Latency (ms) | B − A | Flask | −944 [−1,933, +27] | 7 / 18 | 0.063 | 0.078 | −0.43 |
| Latency (ms) | B − A | Requests | −1,621 [−3,909, +881] | 5 / 13 | 0.067 | 0.21 | −0.50 |
| Latency (ms) | B − A | Pooled | −1,228 [−2,354, −23] | 12 / 31 | 0.006 | 0.046 | −0.48 |
| Latency (ms) | C − A | Pooled | −546 [−1,368, +253] | 21 / 22 | 0.41 | 0.21 | −0.15 |
| Latency (ms) | C − B | Pooled | +681 [−495, +1,716] | 30 / 13 | 0.015 | 0.24 | +0.42 |

**Interpretation.**

- **C costs more tokens than A, reliably:** about 2,400 more per question on Flask and 5,600 on Requests, more on nearly every question (r = +0.75, +0.86). C costs more than B on every single question (r = +1.00).
- **B's token saving over A is reliable on Flask** (about 2,800 per question, r = −0.72) **but not on Requests**, where the interval spans −4,700 to +2,600. On Requests the web gate runs on more questions and its token use varies between conditions, which may add to the noise.
- **Latency differences are not reliable per repository.** Pooled, B is about 1.2 s faster than A; the Wilcoxon test is significant (p = 0.006) but the permutation test only borderline (p = 0.046). The two disagree because latency has a long tail from API variance: the Wilcoxon test uses ranks and is barely affected by a few slow calls, while the permutation test uses the mean, which those calls dominate. With one run per condition, latency results should be treated as indicative.

## 6. Power and what "not significant" means

With 18–25 questions, small differences cannot be detected. From the spread of the observed correctness differences (standard deviation 0.30 for Flask B − A, 0.34 for Requests B − A, 0.15 and 0.20 for C − A), a paired test at α = 0.05 with 80% power can detect a mean difference of about:

| Comparison | Flask (n = 25) | Requests (n = 18) |
|---|---|---|
| B − A | 0.17 | 0.22 |
| C − A | 0.08 | 0.13 |

The observed B − A difference (0.20) is at or just below the detection threshold for Requests, which is why its significance is borderline. For C − A, "no detectable gain" means a gain larger than about 0.08–0.13 is unlikely; smaller gains cannot be ruled out with this sample.

## 7. Limitations of the analysis

- **The judge is treated as exact.** Correctness and citation support come from an LLM judge; its errors are not modelled in these intervals. The pending second-reviewer check of a sample of verdicts (Cohen's kappa between judge and person) would quantify that error.
- **Exploratory and uncorrected across metrics.** Only B − A per repository was pre-specified. The other comparisons (secondary pairs, recall, cost, pooled rows) are exploratory; Holm is applied only to the secondary correctness family. With 54 comparisons, a few p-values below 0.05 are expected by chance alone; the conclusions above rest on effects that are consistent across tests and repositories.
- **One run per condition.** The answers were generated once; run-to-run variation of the generator and reranker is not included in the intervals.
- **Bounded scores and ties.** The Wilcoxon p-values use the normal approximation with tie correction; the permutation test is exact up to Monte Carlo error (about ±0.001 at p = 0.05 with 200,000 flips).
- **Controls are not tested.** Five unanswerable questions are too few for any test.

## 8. Implications for the paper

- Report B − A with its interval, Wilcoxon and permutation p-values and the effect size. For Requests, say the effect matches Flask in size and is borderline significant (Wilcoxon p = 0.052, permutation p = 0.028).
- Report C − A as "no detectable gain", with the interval that bounds the possible gain.
- Report C − B as a clear, large improvement.
- For citation support, report "no difference detected"; claiming equivalence would need a TOST.
- Report token differences for C as reliable, B's token saving as reliable only on Flask, and latency as indicative.
