"""scripts/paired_stats.py: pairing, effect size, Holm, permutation and reproducibility."""

import csv
import json

import numpy as np
import paired_stats as ps
import pytest

FIELDS = ["question_id", "subset", "condition", "correctness", "recall_candidates",
          "recall_final", "citation_support", "total_tokens", "latency_ms"]


def write_report(path, rows):
    path.mkdir(parents=True)
    with (path / "per_question.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def rows(x, y, xs, ys, subset="public", prefix="q"):
    out = []
    for i, (a, b) in enumerate(zip(xs, ys, strict=True)):
        for cond, v in ((x, a), (y, b)):
            out.append({"question_id": f"{prefix}{i}", "subset": subset, "condition": cond,
                        "correctness": v, "recall_candidates": v, "recall_final": v,
                        "citation_support": v, "total_tokens": 1000 * v, "latency_ms": 10 * v})
    return out


def test_rank_biserial_bounds_and_sign():
    assert ps.rank_biserial(np.array([0.1, 0.5, 0.0, 1.0])) == 1.0
    assert ps.rank_biserial(np.array([-0.1, -0.5])) == -1.0
    assert ps.rank_biserial(np.array([0.5, -0.5])) == 0.0
    assert ps.rank_biserial(np.zeros(4)) == 0.0


def test_holm_matches_hand_computation():
    # sorted 0.01, 0.02, 0.04 -> 0.03, 0.04, 0.04 (monotone), returned in input order
    assert ps.holm([0.04, 0.01, 0.02]) == pytest.approx([0.04, 0.03, 0.04])
    assert ps.holm([0.9, 0.8]) == pytest.approx([1.0, 1.0])


def test_permutation_and_bootstrap():
    rng = np.random.default_rng(0)
    assert ps.permutation_p(np.zeros(5), rng) == 1.0
    strong = np.full(15, 0.5)
    assert ps.permutation_p(strong, rng, n_perm=20_000) < 0.001
    d = np.array([0.1, 0.2, -0.1, 0.4, 0.0, 0.3])
    lo, hi = ps.bootstrap_ci(d, rng, n_boot=5_000)
    assert lo < d.mean() < hi


def test_load_pairs_keeps_primary_questions_scored_in_both(tmp_path):
    r = rows("A", "B", [1.0, 0.5], [0.5, 0.5])
    r += rows("A", "B", [1.0], [0.0], subset="control", prefix="c")   # not primary
    r.append({"question_id": "q9", "subset": "public", "condition": "A", "correctness": 1})
    r.append({"question_id": "q9", "subset": "public", "condition": "B", "correctness": ""})
    write_report(tmp_path / "flask-test-AB", r)
    pairs = ps.load_pairs(tmp_path / "flask-test-AB", "correctness", "A", "B")
    assert [p[0] for p in pairs] == ["q0", "q1"]
    assert pairs[0][1:] == (1.0, 0.5)


def test_analyse_pools_repos_adjusts_only_secondary_and_is_reproducible(tmp_path):
    rng = np.random.default_rng(1)
    for repo, n in (("flask", 12), ("requests", 8)):
        for pair in ("AB", "AC", "BC"):
            xs = rng.choice([0.0, 0.5, 1.0], n)
            ys = np.clip(xs + rng.choice([-0.5, 0.0, 0.5], n), 0, 1)
            write_report(tmp_path / f"{repo}-test-{pair}", rows(pair[0], pair[1], xs, ys))
    res = ps.analyse(tmp_path, metrics=("correctness",))
    by = {(r.pair, r.repo): r for r in res}
    assert by[("AB", "pooled")].n == 20
    assert by[("AB", "flask")].p_wilcoxon_holm is None        # primary: not adjusted
    assert all(by[(p, r)].p_wilcoxon_holm is not None for p in ("AC", "BC")
               for r in ("flask", "requests"))
    assert by[("AC", "pooled")].p_wilcoxon_holm is None
    again = {(r.pair, r.repo): r for r in ps.analyse(tmp_path, metrics=("correctness",))}
    assert again[("BC", "flask")].p_permutation == by[("BC", "flask")].p_permutation
    assert again[("BC", "flask")].ci_low == by[("BC", "flask")].ci_low


def test_write_outputs_json_and_csv(tmp_path):
    for pair in ("AB", "AC", "BC"):
        for repo in ("flask", "requests"):
            write_report(tmp_path / "r" / f"{repo}-test-{pair}",
                         rows(pair[0], pair[1], [0.0, 1.0, 0.5], [1.0, 1.0, 0.0]))
    ps.main(["--reports", str(tmp_path / "r"), "--out", str(tmp_path / "out")])
    data = json.loads((tmp_path / "out" / "paired_stats.json").read_text())
    assert data["meta"]["seed"] == ps.SEED and len(data["results"]) == 6 * 3 * 3
    with (tmp_path / "out" / "paired_stats.csv").open() as fh:
        assert len(list(csv.DictReader(fh))) == 54
