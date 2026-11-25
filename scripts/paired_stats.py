"""Paired statistical tests over saved evaluation reports (no model calls).

Reads ``reports/<repo>-test-<XY>/per_question.csv`` (written by ``dwb experiment evaluate``),
pairs the two conditions by question over the primary questions (those that need code
evidence: subsets public, code, code+pdf, code+web), and for every metric, comparison and
repository (plus both repositories pooled) reports:

- the mean of each condition and the mean paired difference (second minus first condition);
- a percentile bootstrap 95% confidence interval of the mean difference;
- counts of questions where the second condition is higher / equal / lower;
- the exact two-sided sign test (ties dropped);
- the Wilcoxon signed-rank test (Pratt's treatment of zero differences);
- a two-sided sign-flip permutation test of the mean difference;
- the matched-pairs rank-biserial correlation (effect size, -1..+1);
- Holm-adjusted Wilcoxon p-values for the secondary correctness comparisons (C-A, C-B in each
  repository). The primary comparison (B-A per repository) was fixed before the runs and is not
  adjusted.

Seeds are fixed, so the output is reproducible. Usage (from the repository root):

    python scripts/paired_stats.py --reports reports --out docs/results/stats

Needs numpy and scipy (``pip install -e '.[analysis]'``).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from scipy import stats

PRIMARY_SUBSETS = frozenset({"public", "code", "code+pdf", "code+web"})
METRICS = ("correctness", "recall_candidates", "recall_final", "citation_support",
           "total_tokens", "latency_ms")
PAIRS = ("AB", "AC", "BC")
REPOS = ("flask", "requests")
SEED = 20261124
N_PERM = 200_000
N_BOOT = 20_000


@dataclass
class Result:
    metric: str
    pair: str
    repo: str
    n: int
    mean_x: float
    mean_y: float
    mean_diff: float
    ci_low: float
    ci_high: float
    n_higher: int
    n_equal: int
    n_lower: int
    p_sign: float
    p_wilcoxon: float
    p_permutation: float
    rank_biserial: float
    p_wilcoxon_holm: float | None = None
    notes: list[str] = field(default_factory=list)


def load_pairs(report_dir: Path, metric: str, x: str, y: str,
               subsets: frozenset[str] = PRIMARY_SUBSETS) -> list[tuple[str, float, float]]:
    """(question_id, x value, y value) for questions scored in both conditions."""
    by_q: dict[str, dict[str, str]] = {}
    with (report_dir / "per_question.csv").open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["subset"] in subsets:
                by_q.setdefault(row["question_id"], {})[row["condition"]] = row.get(metric, "")
    out = []
    for q, vals in sorted(by_q.items()):
        a, b = vals.get(x, ""), vals.get(y, "")
        if a not in ("", None) and b not in ("", None):
            out.append((q, float(a), float(b)))
    return out


def rank_biserial(d: np.ndarray) -> float:
    """Matched-pairs rank-biserial correlation: (R+ - R-) / (R+ + R-) over non-zero
    differences, with average ranks of |d|. +1: every non-zero difference is positive."""
    nz = d[d != 0]
    if len(nz) == 0:
        return 0.0
    r = stats.rankdata(np.abs(nz))
    return float((r[nz > 0].sum() - r[nz < 0].sum()) / r.sum())


def permutation_p(d: np.ndarray, rng: np.random.Generator, n_perm: int = N_PERM) -> float:
    """Two-sided sign-flip permutation test of the mean difference. Under H0 each paired
    difference is equally likely to have either sign."""
    if not np.any(d):
        return 1.0
    obs = abs(d.mean())
    hits = 0
    for start in range(0, n_perm, 20_000):
        k = min(20_000, n_perm - start)
        flips = rng.choice((-1.0, 1.0), size=(k, len(d)))
        hits += int((np.abs((flips * d).mean(axis=1)) >= obs - 1e-12).sum())
    return (hits + 1) / (n_perm + 1)  # add-one: never report an exact zero


def bootstrap_ci(d: np.ndarray, rng: np.random.Generator, n_boot: int = N_BOOT,
                 level: float = 0.95) -> tuple[float, float]:
    idx = rng.integers(0, len(d), size=(n_boot, len(d)))
    means = d[idx].mean(axis=1)
    tail = (1 - level) / 2 * 100
    lo, hi = np.percentile(means, [tail, 100 - tail])
    return float(lo), float(hi)


def paired_tests(metric: str, pair: str, repo: str, xs: np.ndarray, ys: np.ndarray,
                 rng: np.random.Generator) -> Result:
    d = ys - xs
    nz = d[d != 0]
    n_hi, n_lo = int((d > 0).sum()), int((d < 0).sum())
    notes = []
    p_sign = float(stats.binomtest(n_hi, n_hi + n_lo, 0.5).pvalue) if len(nz) else 1.0
    if len(nz):
        p_wil = float(stats.wilcoxon(d, zero_method="pratt").pvalue)
    else:
        p_wil = 1.0
        notes.append("all differences are zero")
    lo, hi = bootstrap_ci(d, rng)
    return Result(metric=metric, pair=pair, repo=repo, n=len(d), mean_x=float(xs.mean()),
                  mean_y=float(ys.mean()), mean_diff=float(d.mean()), ci_low=lo, ci_high=hi,
                  n_higher=n_hi, n_equal=int((d == 0).sum()), n_lower=n_lo, p_sign=p_sign,
                  p_wilcoxon=p_wil, p_permutation=permutation_p(d, rng),
                  rank_biserial=rank_biserial(d), notes=notes)


def holm(pvalues: list[float]) -> list[float]:
    """Holm step-down adjustment, returned in the input order."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, pvalues[i] * (m - rank)))
        adjusted[i] = running
    return adjusted


def analyse(reports: Path, metrics: Iterable[str] = METRICS, pairs: Iterable[str] = PAIRS,
            repos: Iterable[str] = REPOS, seed: int = SEED) -> list[Result]:
    rng = np.random.default_rng(seed)
    results: list[Result] = []
    for metric in metrics:
        for pair in pairs:
            x, y = pair
            pooled_x, pooled_y = [], []
            for repo in repos:
                rows = load_pairs(reports / f"{repo}-test-{pair}", metric, x, y)
                xs = np.array([r[1] for r in rows])
                ys = np.array([r[2] for r in rows])
                pooled_x.append(xs)
                pooled_y.append(ys)
                results.append(paired_tests(metric, pair, repo, xs, ys, rng))
            results.append(paired_tests(metric, pair, "pooled", np.concatenate(pooled_x),
                                        np.concatenate(pooled_y), rng))
    family = [r for r in results if r.metric == "correctness" and r.pair in ("AC", "BC")
              and r.repo != "pooled"]
    for r, adj in zip(family, holm([r.p_wilcoxon for r in family]), strict=True):
        r.p_wilcoxon_holm = adj
    return results


def write(results: list[Result], out: Path, meta: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rows = [asdict(r) for r in results]
    (out / "paired_stats.json").write_text(json.dumps({"meta": meta, "results": rows},
                                                      indent=2, sort_keys=True) + "\n")
    cols = [k for k in rows[0] if k != "notes"] + ["notes"]
    with (out / "paired_stats.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({**{k: _round(r[k]) for k in cols if k != "notes"},
                        "notes": "; ".join(r["notes"])})


def _round(v):
    return round(v, 6) if isinstance(v, float) and not math.isnan(v) else v


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--reports", type=Path, default=Path("reports"),
                    help="folder with <repo>-test-<XY>/per_question.csv")
    ap.add_argument("--out", type=Path, default=Path("docs/results/stats"))
    ap.add_argument("--seed", type=int, default=SEED)
    a = ap.parse_args(argv)
    results = analyse(a.reports, seed=a.seed)
    meta = {"seed": a.seed, "n_permutations": N_PERM, "n_bootstrap": N_BOOT,
            "primary_subsets": sorted(PRIMARY_SUBSETS), "numpy": np.__version__,
            "scipy": __import__("scipy").__version__,
            "difference": "second condition minus first (e.g. AB: B - A)"}
    write(results, a.out, meta)
    print(f"{len(results)} comparisons written to {a.out}/paired_stats.{{json,csv}}")


if __name__ == "__main__":
    main()
