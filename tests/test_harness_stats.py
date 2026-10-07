"""Statistics: bootstrap weights, shift p, Holm, untestable rules and their priority over the verdict."""

import math

import numpy as np
import pytest

from riskbound.harness import stats


def test_constants():
    assert (stats.SEED, stats.N_BOOT, stats.MIN_STOCKS, stats.MIN_INFO_BLOCKS) == (20261005, 2000, 10, 10)
    assert (stats.MAX_UNDEF_FRAC, stats.TOL) == (0.05, 1e-9)


def test_weight_matrix():
    W = stats.weight_matrix(7, 50, 11)
    idx = np.random.default_rng(11).integers(0, 7, size=(50, 7))
    assert W.shape == (50, 7) and (W.sum(axis=1) == 7).all()
    assert np.array_equal(W[3], np.bincount(idx[3], minlength=7).astype(float))


def test_shift_p():
    assert stats.shift_p(0.0, np.array([1.0, 2.0])) == (1.0, 2)
    assert stats.shift_p(float("nan"), np.array([1.0])) == (1.0, 1)
    Ts = np.array([1.0, 1.5, 2.5, 3.1, np.nan])          # |T* - 1.5| >= 1.5: 3.1 only
    assert stats.shift_p(1.5, Ts) == (2 / 5, 1)


def test_holm_hand_example():
    # sorted: 0.0005*5 = 0.0025, 0.01*4 = 0.04, 0.03*3 = 0.09, 0.04*2 = 0.08 -> 0.09 (monotone), 0.20*1 = 0.20
    out = stats.holm(np.array([0.01, 0.04, 0.03, 0.20, 0.0005]))
    assert np.allclose(out, [0.04, 0.09, 0.09, 0.20, 0.0025], rtol=0, atol=1e-15)


def test_holm_cap_and_ties():
    assert np.array_equal(stats.holm(np.array([0.5, 0.6, 1.0])), [1.0, 1.0, 1.0])
    assert np.allclose(stats.holm(np.array([0.01, 0.01])), [0.02, 0.02])


def test_period_stat():
    a, h = np.array([1.0, 2.0, 100.0]), np.array([True, True, False])
    assert stats.period_stat(np.ones(3), a, h) == 1.5
    assert math.isnan(stats.period_stat(np.ones(3), a, np.zeros(3, dtype=bool)))


def _per(late, early, h=None):
    h = np.ones(len(late), dtype=bool) if h is None else h
    return [(np.asarray(late, dtype=float), h), (np.asarray(early, dtype=float), h)]


def test_rule5_opposite_signs_constant_is_inconclusive_not_inconsistent():
    per = _per(np.full(25, 2.0), np.full(25, -2.0))
    W = stats.weight_matrix(25, 2000, stats.SEED)
    r = stats.cell_stats(per, None, W, 1.0, +1)
    assert 5 in r["untestable"] and r["p"] == 1.0
    assert (r["verdict"], r["direction"]) == ("inconclusive", None)
    # the same deltas without an untestable rule would be "inconsistent": priority matters
    assert stats.grade(r["d"], [], [], 1.0, +1) == ("inconsistent", None)


def test_rule3_undefined_resamples():
    m = 12
    h = np.r_[np.ones(10, dtype=bool), np.zeros(2, dtype=bool)]
    g = np.r_[np.linspace(1.5, 2.5, 10), 0, 0]
    W = stats.weight_matrix(m, 200, 1)
    W[:20] = 0.0
    W[:20, 10:] = 6.0                     # 10% of resamples draw only uninformative stocks -> T* undefined
    r = stats.cell_stats([(g, h), (g * 1.1, h)], None, W, 1.0, +1)
    assert r["untestable"] == [3] and r["B_valid"] == 180
    assert r["verdict"] == "inconclusive"
    assert stats.grade(r["d"], [], [], 1.0, +1)[0] == "consistent"


def test_rule4_and_rule1_few_informative_stocks():
    h = np.r_[np.ones(9, dtype=bool), np.zeros(16, dtype=bool)]
    g = np.r_[np.full(9, 2.0) + np.linspace(0, 0.5, 9), np.zeros(16)]
    hh = np.ones(25, dtype=bool)
    gg = np.full(25, -2.0) + np.linspace(0, 0.5, 25)
    W = stats.weight_matrix(25, 2000, stats.SEED)
    r = stats.cell_stats([(g, h), (gg, hh)], None, W, 1.0, +1)
    assert 1 in r["untestable"] and 4 in r["untestable"] and r["verdict"] == "inconclusive"
    conf = stats.confirm([(g, h), (gg, hh)], W, 2000)          # rule 4 on its own (no rule-1 counts passed)
    assert 4 in conf["untestable"] and 1 not in conf["untestable"]
    assert stats.grade(r["d"], [], [], 1.0, +1) == ("inconsistent", None)


def test_threshold_boundary_reaches():
    g = (0.31 - 0.30) * 100                       # spec 4.3: MDD 0.30 vs 0.31 -> 1.0pp counts
    assert stats.reaches(g, 1.0) and stats.reaches(0.05, 0.05) and stats.reaches(1.0 - 5e-10, 1.0)
    assert not stats.reaches(1.0 - 2e-9, 1.0) and not stats.reaches(None, 1.0)


def test_version_ok_and_grade():
    assert stats.version_ok([2.0, 3.0], [], 1.0) == (True, False)
    assert stats.version_ok([2.0, 3.0], [0.1, 0.2], 1.0) == (True, False)
    assert stats.version_ok([2.0, 3.0], [0.1, -0.2], 1.0) == (False, False)
    assert stats.version_ok([2.0, 0.5], [], 1.0) == (False, False)
    assert stats.version_ok([2.0, float("nan")], [], 1.0) == (False, False)
    assert stats.grade([-2.0, -3.0], [], [], 1.0, +1) == ("consistent", "opposite")
    assert stats.grade([2.0, 3.0], [], [], 1.0, +1) == ("consistent", "as_predicted")
    assert stats.grade([2.0, 3.0], [], [1], 1.0, +1) == ("inconclusive", None)


def test_confirm_point_and_interval():
    rng = np.random.default_rng(2)
    g1, g2 = 2 + rng.standard_normal(25), 3 + rng.standard_normal(25)
    W = stats.weight_matrix(25, 500, stats.SEED)
    conf = stats.confirm(_per(g1, g2), W, 500, [], [25, 25])
    assert conf["untestable"] == [] and conf["B_valid"] == 500
    assert math.isclose(conf["T"], (g1.mean() + g2.mean()) / 2, rel_tol=1e-12)
    assert conf["T_ci"][0] < conf["T"] < conf["T_ci"][1] and conf["p_is_min"]
