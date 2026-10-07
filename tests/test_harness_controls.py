"""Controls: segments, C_dagger bisection (incl. the floating-point failure branch), shuffle invariants."""

import numpy as np
import pytest

from riskbound.harness import controls, ledger, stats


def test_constants():
    assert (controls.MATCH_STOP, controls.MATCH_OK, controls.BISECT_MAX, controls.N_SHUFFLE) == (1e-9, 1e-6, 100, 200)


def test_f_segments_with_unrecovered_tail():
    t = np.array([1, 1, .5, .5, 1, .5, 1, 1, .5, .5])
    assert controls.f_segments(t) == [(2, 4), (5, 6), (8, 10)]
    assert controls.f_segments(np.array([1, 1, 1.0])) == []
    assert controls.f_segments(np.array([0.5, 1, 1.0])) == []          # index 0 is a sentinel


def test_first_target_low_rebalances_on_day_one():
    rng = np.random.default_rng(0)
    C = 100 * np.exp(np.cumsum(0.01 * rng.standard_normal(11)))
    t = np.r_[1.0, 0.5, 0.5, 1, 1, 1, 1, 1, 1, 1, 1]
    sim = ledger.simulate_w(C, C, t, "change")
    assert controls.f_segments(t) == [(1, 3)] and sim["trades"] == 2
    assert abs(sim["w"][1] - 0.5) < 0.05


def _oc(N, seed):
    rng = np.random.default_rng(seed)
    C = 50 * np.exp(np.cumsum(0.02 * rng.standard_normal(N + 1)))
    return np.r_[C[0], C[:-1]], C


def test_c_dagger_bisection():
    O, C = _oc(400, 3)
    w = np.r_[1.0, 0.6 + 0.3 * np.sin(np.arange(400) / 20)]
    strat = ledger.simulate_w(O, C, w, "threshold")
    goal = ledger.metrics(strat["V"], strat["w"])["avg_w"]
    mt = controls.solve_c_dagger(O, C, goal)
    assert abs(mt["resid"]) <= 1e-9 and mt["matched"] and 0 < mt["c"] < 1
    b = controls.buyhold_c(O, C, mt["c"])
    assert abs(b["w"][1:].mean() - goal) <= 1e-9
    once = ledger.simulate_w(O, C, np.full(401, mt["c"]), "once")
    assert np.allclose(once["V"], b["V"], rtol=1e-12) and once["trades"] == b["trades"] == 1
    assert once["cost"] == b["cost"]
    one = controls.buyhold_c(O, C, 1.0)
    assert one["trades"] == 0 and one["cost"] == 0.0 and np.allclose(one["V"], C / C[0], rtol=1e-12)


@pytest.mark.parametrize("goal,approx", [(0.6, 1.06e-9), (0.75, 2.91e-9)])
def test_c_dagger_floating_point_branch(goal, approx):
    # review r2: d_0 close and d_1 open = 1, closes from d_1 on = 1e-8, 10 bps
    C = np.r_[1.0, np.full(5, 1e-8)]
    O = np.r_[1.0, 1.0, np.full(4, 1e-8)]
    mt = controls.solve_c_dagger(O, C, goal)
    assert 0.0 < mt["c"] < 1.0                                     # never an endpoint
    assert 1e-9 < abs(mt["resid"]) <= 1e-6 and mt["matched"] is True
    assert abs(abs(mt["resid"]) - approx) < 0.02e-9                 # spec: about 1.06e-9 / 2.91e-9 (the best candidate, not the last mid)
    assert mt["iters"] == 100
    w = controls.buyhold_c(O, C, mt["c"])["w"][1:].mean()
    assert w - goal == mt["resid"]


def test_c_dagger_keeps_first_of_tied_candidates():
    # monotone w(c) = c here (no price change, zero cost) -> exact match at the first mid for goal 0.5
    C = np.ones(5)
    mt = controls.solve_c_dagger(C, C, 0.5, cost=0.0)
    assert mt["c"] == 0.5 and mt["resid"] == 0.0 and mt["iters"] == 1


def test_c_dagger_unreachable_goal_is_unmatched():
    O, C = _oc(100, 4)
    mt = controls.solve_c_dagger(O, C, 1.5)
    assert mt["matched"] is False and 0 < mt["c"] < 1 and mt["iters"] == 100


def test_shuffle_invariants():
    N = 300
    segs = [(5, 12), (40, 41), (60, 90), (120, 125), (200, 230), (280, 301)]
    lay = controls.shuffle_layout(segs, N)
    assert lay["k"] == 5 and lay["tail"] == [(280, 301)] and lay["R"] == 279
    assert lay["F"] == 279 - sum(L + 1 for L in lay["L"]) and not lay["unique"]
    rng0 = np.random.default_rng(0)
    C = 100 * np.exp(np.cumsum(0.01 * rng0.standard_normal(N + 1)))
    O = np.r_[C[0], C[:-1]]
    base = ledger.simulate_segments(O, C, segs)
    for r in range(controls.N_SHUFFLE):
        out = controls.shuffle_once(lay, np.random.default_rng(stats.SEED + r))
        assert len(out) == len(segs) and out[-1] == (280, 301)
        assert sorted(e - s for s, e in out) == sorted(e - s for s, e in segs)
        body = sorted(out[:-1])
        assert body[0][0] >= 1 and body[-1][1] <= lay["R"]
        assert all(b[0] >= a[1] + 1 for a, b in zip(body, body[1:]))          # a recovery open between blocks
        assert ledger.simulate_segments(O, C, out)["trades"] == base["trades"]


def test_shuffle_k0_does_not_touch_rng():
    lay = controls.shuffle_layout([(10, 21)], 20)
    assert lay["k"] == 0 and lay["unique"]
    rng = np.random.default_rng(5)
    before = rng.bit_generator.state
    assert controls.shuffle_once(lay, rng) == [(10, 21)]
    assert rng.bit_generator.state == before
    assert controls.shuffle_once(controls.shuffle_layout([], 20), rng) == []
    assert rng.bit_generator.state == before


def test_unique_layout():
    lay = controls.shuffle_layout([(1, 3), (4, 6)], 6)      # two blocks of length 2, F = 0
    assert lay["F"] == 0 and lay["unique"]
