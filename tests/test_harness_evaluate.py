"""End-to-end entry points: windows without future data, the 25-stock review counterexample, unmatched
controls, subset conditions, segment shuffles, defaults, constants."""

import json
import math
import time

import numpy as np
import pandas as pd
import pytest

from riskbound import rules
from riskbound.harness import (B5_OVERLAY_CELLS, B5_SEGMENT_CELLS, CellSpec, controls, evaluate_overlay,
                               evaluate_segments, ledger, stats)
from riskbound.harness import evaluate as ev

V_RULE = dict(vol=True, vol_floor=None, below_sma=1.0)
F_RULE = dict(vol=False, vol_floor=None, below_sma=0.5)


def v_target(C):
    return rules.weights(C, **V_RULE)["w"]


def f_target(C):
    return rules.weights(C, **F_RULE)["w"]


def universe(n_stocks=12, n=700, seed=0, start="2015-01-02"):
    """GBM with volatility clustering; O[t] = C[t-1] * small gap."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    out = {}
    for k in range(n_stocks):
        vol = 0.012 * np.exp(np.cumsum(0.05 * rng.standard_normal(n)).clip(-1.2, 1.2))
        C = 100 * np.exp(np.cumsum(0.0003 + vol * rng.standard_normal(n)))
        O = np.r_[C[0], C[:-1]] * np.exp(0.002 * rng.standard_normal(n))
        out[f"T{k:02d}"] = pd.DataFrame({"O": O, "H": np.maximum(O, C), "L": np.minimum(O, C), "C": C, "V": 1e6},
                                        index=idx)
    return out


def periods_of(bars, a, b, c):
    idx = next(iter(bars.values())).index
    return [(str(idx[a].date()), str(idx[b].date())), (str(idx[b + 1].date()), str(idx[c].date()))]


# ---------------------------------------------------------------- constants
def test_constants():
    assert B5_OVERLAY_CELLS == (CellSpec("MDD", "mdd", "exposure_matched", 1.0, 1),
                                CellSpec("Sharpe", "sharpe", "exposure_matched", 0.05, 1))
    assert B5_SEGMENT_CELLS == (CellSpec("MDD", "mdd", "segment_shuffle", 1.0, 1),
                                CellSpec("Calmar", "calmar", "segment_shuffle", 0.05, 1))
    assert (ledger.COST, controls.N_SHUFFLE, stats.SEED, stats.N_BOOT, stats.MIN_STOCKS) == (0.001, 200, 20261005,
                                                                                             2000, 10)


# ---------------------------------------------------------------- no future information
def test_window_targets_truncation_has_no_future_information():
    C = universe(1, 600, seed=1)["T00"]["C"].to_numpy()
    i0, N = 300, 250
    full = ev.window_target(lambda x: rules.weights(x, **rules.PRESETS["vf75"])["w"], C, i0, N)
    for t in (1, 2, 50, 137, 249, 250):
        # data up to close j = i0 + t - 1 only (cut everything after it)
        cut = ev.window_target(lambda x: rules.weights(x, **rules.PRESETS["vf75"])["w"], C[:i0 + t], i0, t)
        assert np.array_equal(cut, full[:t + 1])


def test_evaluate_never_passes_closes_after_last_target_close():
    bars = universe(10, 520, seed=2)
    P = periods_of(bars, 300, 409, 519)
    seen = []

    def spy(C):
        seen.append(len(C))
        return v_target(C)

    res = evaluate_overlay(bars, spy, P, n_boot=50)
    idx = bars["T00"].index
    ends = [pd.Timestamp(e) for _, e in P]
    for n in set(seen):
        assert any(idx[n - 1] < e for e in ends)       # last close given to target_fn precedes a period end
    assert max(seen) == 519                            # = i0 + N for the last period (close 519 never passed)
    for (p, s), r in res.per_stock.items():
        assert pd.Timestamp(r["last_date"]) <= pd.Timestamp(p.split("/")[1])
        assert pd.Timestamp(r["first_date"]) >= pd.Timestamp(p.split("/")[0])


def test_warmup_gate_and_zero_sigma_inside_window():
    C = universe(1, 500, seed=3)["T00"]["C"].to_numpy().copy()
    C[330:360] = C[329]
    tg = ev.window_target(v_target, C, 100, 399)        # window opens read closes 100..498
    j = 100 + np.arange(399)
    assert (tg[1:][j < 269] == 1.0).all()              # fewer than 250 defined sigma_hat values
    s = rules.sigma_hat(C)
    zero = s[j] == 0                                   # closes 329..359 identical -> 11 all-zero windows
    assert zero.sum() == 11 and (tg[1:][zero] == 1.0).all()
    assert (tg[1:][j >= 269] < 1.0).any()


# ---------------------------------------------------------------- review r1 counterexample (25 stocks)
A_EARLY, A_LATE = 0.04298307112765168, 0.03882216231250669
N_E, N_L = 1257, 1075
LEN_EARLY = 582 + N_E                  # early cache (581 closes before d_0, plus d_0) + window
LATE_CACHE_START = LEN_EARLY - 580     # the late cache = last 580 closes of the early path + late window


def _window_returns(s, N, amp, a):
    t = np.arange(1, N + 1)
    r = np.where(t <= 400, 0.000002 * s + amp * (-1.0) ** t, a + 0.008 * (-1.0) ** t)
    r[399] = math.log(0.7)                                      # r_400 = ln(0.7)
    return r


def _r1_path(s):
    j = np.arange(1, 582)
    pre = 0.0002 + 0.01 * (-1.0) ** (j - 1)                    # C[0] = 1, then 581 returns
    early = np.exp(np.cumsum(np.r_[0.0, pre, _window_returns(s, N_E, 0.02, A_EARLY)]))
    d0_late = early[-1] * math.exp(A_EARLY)
    late = d0_late * np.exp(np.cumsum(np.r_[0.0, _window_returns(s, N_L, 0.016, A_LATE)]))
    return np.r_[early, late]                                  # one continuous series


def _period_anchored_v(C):
    """B5 computes each period's indicators on that period's own cache: the late expanding window starts at the
    late cache. target_fn only receives closes before the period end, so the period is known from len(C)."""
    a = LATE_CACHE_START if len(C) > LEN_EARLY else 0
    return np.r_[np.ones(a), v_target(C[a:])]


@pytest.fixture(scope="module")
def r1():
    full = {s: _r1_path(s) for s in range(25)}
    idx = pd.bdate_range("2010-01-04", periods=LEN_EARLY + 1 + N_L)
    bars = {f"S{s:02d}": pd.DataFrame({"O": np.r_[C[0], C[:-1]], "H": C, "L": C, "C": C, "V": 1e6}, index=idx)
            for s, C in full.items()}
    P = [(str(idx[581].date()), str(idx[LEN_EARLY - 1].date())), (str(idx[LEN_EARLY].date()), str(idx[-1].date()))]
    return full, bars, P


def test_r1_counterexample_c0_control_would_fake_a_drawdown_advantage(r1):
    full, _, _ = r1
    d0 = {"early": [], "late": []}
    c0s = {}
    for s, C in full.items():
        for name, i0, N, a in (("early", 581, N_E, 0), ("late", LEN_EARLY, N_L, LATE_CACHE_START)):
            Cw = C[i0:i0 + N + 1]
            O = np.r_[C[i0], Cw[:-1]]
            sim = ledger.simulate_w(O, Cw, ev.window_target(_period_anchored_v, C, i0, N), "threshold")
            mv = ledger.metrics(sim["V"], sim["w"])
            b0 = controls.buyhold_c(O, Cw, mv["avg_w"])                 # c0 = avg_w: the rejected control
            d0[name].append((ledger.metrics(b0["V"], b0["w"])["mdd"] - mv["mdd"]) * 100)
            if s == 0:
                c0s[name] = mv["avg_w"]
    print(f"\nr1 c0 (s=0): early {c0s['early']!r} late {c0s['late']!r}")
    print(f"r1 C0 delta pp: early {np.mean(d0['early'])!r} late {np.mean(d0['late'])!r}")
    assert abs(c0s["early"] - 0.843058096489) < 1e-11 and abs(c0s["late"] - 0.765581150322) < 1e-11
    assert abs(np.mean(d0["early"]) - 9.320916082334) < 1e-6
    assert abs(np.mean(d0["late"]) - 10.451296891143) < 1e-6


def test_r1_counterexample_c_dagger_control(r1):
    _, bars, P = r1
    res = evaluate_overlay(bars, _period_anchored_v, P)
    mdd = res.cells[0]
    e, l = res.periods
    c_e = res.per_stock[(e, "S00")]["c_dagger"]["c"]
    c_l = res.per_stock[(l, "S00")]["c_dagger"]["c"]
    print(f"\nr1 c_dagger (s=0): early {c_e!r} late {c_l!r}")
    print(f"r1 C_dagger delta pp: early {mdd.d[e]!r} late {mdd.d[l]!r}; verdict {mdd.verdict}")
    print(f"diff vs r1: c {c_e - 0.553859325869:.3e} / {c_l - 0.438842204595:.3e}; "
          f"delta {mdd.d[e] - 0.262189925368:.3e} / {mdd.d[l] - 0.288422675729:.3e}")
    assert abs(mdd.d[e] - 0.262189925368) < 1e-6 and abs(mdd.d[l] - 0.288422675729) < 1e-6
    # c_dagger: the frozen bisection stops at |exposure residual| <= 1e-9, so c is within a few 1e-9 of the exact
    # root in review r1; review r2's replay of the frozen solver gives 0.553859323263 / 0.438842203468.
    assert abs(c_e - 0.553859323263) < 1e-9 and abs(c_l - 0.438842203468) < 1e-9
    assert abs(c_e - 0.553859325869) < 5e-9 and abs(c_l - 0.438842204595) < 5e-9
    assert all(abs(r["c_dagger"]["resid"]) <= 1e-9 for r in res.per_stock.values())
    assert mdd.verdict != "consistent"


# ---------------------------------------------------------------- unmatched C_dagger -> excluded -> untestable
def test_unmatched_c_dagger_excludes_stock_period(monkeypatch):
    bars = universe(12, 520, seed=4)
    P = periods_of(bars, 300, 409, 519)
    real = controls.solve_c_dagger
    bad = {"T00", "T01", "T02"}
    calls = iter(range(10 ** 6))
    order = [s for _ in P for s in bars]               # evaluate visits periods in order, members in order

    def injected(O, C, goal, cost=ledger.COST):
        s = order[next(calls)]
        return real(O, C, 1.5 if s in bad else goal, cost)

    monkeypatch.setattr(controls, "solve_c_dagger", injected)
    res = evaluate_overlay(bars, v_target, P, n_boot=200)
    for (p, s), r in res.per_stock.items():
        assert r["c_dagger"]["matched"] is (s not in bad)
        if s in bad:
            assert r["h"] == {"MDD": False, "Sharpe": False}
    for c in res.cells:
        assert all(n <= 9 for n in c.h_count.values())
        assert 1 in c.untestable and 4 in c.untestable and c.verdict == "inconclusive" and c.p == 1.0


# ---------------------------------------------------------------- subset conditions (grading layer)
def _fake(members, plabels, g_by_period, h_by_period=None):
    per_stock = {}
    for k, p in enumerate(plabels):
        for i, s in enumerate(members):
            h = True if h_by_period is None else bool(h_by_period[k][i])
            per_stock[(p, s)] = {"g": {"MDD": float(g_by_period[k][i])}, "h": {"MDD": h}}
    return per_stock


MEM = [f"T{k:02d}" for k in range(12)]
PL = ["2016-01-01/2018-12-31", "2019-01-01/2021-12-31"]
CELL = (CellSpec("MDD", "mdd", "exposure_matched", 1.0, +1),)
G_POS = [2 + np.linspace(0, 1, 12), 3 + np.linspace(0, 1, 12)]


def test_subset_none_is_skipped():
    (c,) = ev._grade(CELL, MEM, PL, _fake(MEM, PL, G_POS), None, stats.SEED, 500)
    assert (c.verdict, c.direction, c.subset_check, c.wl) == ("consistent", "as_predicted", "skipped", None)


def test_subset_same_sign_can_be_consistent():
    (c,) = ev._grade(CELL, MEM, PL, _fake(MEM, PL, G_POS), ["T00", "T05"], stats.SEED, 500)
    assert (c.verdict, c.subset_check) == ("consistent", "passed") and all(v > 0 for v in c.wl.values())


def test_subset_opposite_sign_blocks_consistent():
    g = [G_POS[0].copy(), G_POS[1].copy()]
    g[1][[0, 5]] = -4.0                                 # subset members negative in the second period
    (c,) = ev._grade(CELL, MEM, PL, _fake(MEM, PL, g), ["T00", "T05"], stats.SEED, 500)
    assert c.d[PL[1]] > 1.0 and c.wl[PL[1]] < 0
    assert c.verdict != "consistent" and c.subset_check == "failed"


def test_subset_without_informative_members_is_inconclusive():
    h = [np.ones(12, dtype=bool), np.r_[False, np.ones(11, dtype=bool)]]
    (c,) = ev._grade(CELL, MEM, PL, _fake(MEM, PL, G_POS, h), ["T00"], stats.SEED, 500)
    assert (c.verdict, c.direction, c.subset_check) == ("inconclusive", None, "no informative members")
    (c,) = ev._grade(CELL, MEM, PL, _fake(MEM, PL, G_POS), ["NOT_A_MEMBER"], stats.SEED, 500)
    assert (c.verdict, c.subset_check) == ("inconclusive", "no informative members")


def test_subset_end_to_end_flags():
    bars = universe(12, 520, seed=5)
    P = periods_of(bars, 300, 409, 519)
    a = evaluate_overlay(bars, v_target, P, n_boot=100)
    b = evaluate_overlay(bars, v_target, P, n_boot=100, subset=["ZZZ"])
    assert all(c.subset_check == "skipped" for c in a.cells)
    assert all((c.verdict, c.subset_check) == ("inconclusive", "no informative members") for c in b.cells)
    assert [c.d for c in a.cells] == [c.d for c in b.cells]


# ---------------------------------------------------------------- segments
def test_evaluate_segments_small():
    bars = universe(12, 560, seed=6)
    P = periods_of(bars, 300, 429, 559)
    res = evaluate_segments(bars, f_target, P, n_boot=200, n_shuffle=20)
    assert [c.name for c in res.cells] == ["MDD", "Calmar"] and res.params["n_shuffle"] == 20
    n_seg = 0
    for (p, s), r in res.per_stock.items():
        if r["segments"]:
            n_seg += 1
            assert r["strategy"]["low"] == 0.5 and r["shuffle"]["n"] == 20
            assert r["layout"]["k"] == len(r["segments"]) - (1 if r["strategy"]["tail"] else 0)
        else:
            assert r["shuffle"]["n"] == 0 and r["h"] == {"MDD": False, "Calmar": False}
    assert n_seg >= 10
    for c in res.cells:
        assert c.verdict in ("consistent", "inconsistent", "inconclusive") and c.p_holm is not None
    json.dumps(res.to_dict())
    md = res.to_markdown()
    assert "## Grading" in md and "## Per stock" in md and "segment_shuffle" in md


def test_segment_shuffle_matches_frozen_draw_order():
    """The per-stock P draws follow rng = default_rng(seed + r), r outer, then periods, then members."""
    bars = universe(3, 560, seed=7)
    P = periods_of(bars, 300, 429, 559)
    res = evaluate_segments(bars, f_target, P, n_boot=20, n_shuffle=3)
    seqs = ev._prepare(bars, f_target, P)[2]
    lays = {k: controls.shuffle_layout(r["segments"], len(seqs[k]["C"]) - 1) for k, r in res.per_stock.items()}
    mdd = {k: [] for k in res.per_stock}
    for r in range(3):
        rng = np.random.default_rng(stats.SEED + r)
        for p in res.periods:
            for s in res.members:
                k = (p, s)
                if not res.per_stock[k]["segments"]:
                    continue
                sim = ledger.simulate_segments(seqs[k]["O"], seqs[k]["C"], controls.shuffle_once(lays[k], rng))
                mdd[k].append(ledger.metrics(sim["V"], sim["w"])["mdd"])
    for k, r in res.per_stock.items():
        if mdd[k]:
            assert r["shuffle"]["mdd_mean"] == float(np.mean(mdd[k]))
            assert r["g"]["MDD"] == (float(np.mean(mdd[k])) - r["strategy"]["mdd"]) * 100


def test_segments_reject_multilevel_targets():
    bars = universe(2, 520, seed=8)
    P = periods_of(bars, 300, 409, 519)
    with pytest.raises(ValueError, match="several low values"):
        evaluate_segments(bars, v_target, P, n_boot=10, n_shuffle=2)


def test_cell_control_must_match_entry_point():
    bars = universe(2, 520, seed=9)
    P = periods_of(bars, 300, 409, 519)
    with pytest.raises(ValueError):
        evaluate_overlay(bars, v_target, P, cells=B5_SEGMENT_CELLS)
    with pytest.raises(ValueError):
        evaluate_segments(bars, f_target, P, cells=B5_OVERLAY_CELLS)


def test_single_period_is_descriptive_only():
    bars = universe(10, 520, seed=10)
    idx = bars["T00"].index
    res = evaluate_overlay(bars, v_target, [(str(idx[300].date()), str(idx[519].date()))])
    for c in res.cells:
        assert c.verdict == "not_applicable (single period)" and c.p is None and c.T is None
        assert len(c.d) == 1 and c.subset_check == "skipped"


def test_overlay_change_rebalance_and_markdown():
    bars = universe(10, 520, seed=11)
    P = periods_of(bars, 300, 409, 519)
    res = evaluate_overlay(bars, f_target, P, rebalance="change", n_boot=100)
    r = next(iter(res.per_stock.values()))
    assert set(r) >= {"A", "strategy", "control", "c_dagger", "g", "h", "first_date", "last_date"}
    assert "exposure_matched" in res.to_markdown()
    d = res.to_dict()
    assert set(d) == {"cells", "per_stock", "members", "periods", "params"}
    json.dumps(d)


# ---------------------------------------------------------------- defaults (2000 bootstrap / 200 shuffles)
def test_default_parameters_small_universe():
    bars = universe(12, 560, seed=12)
    P = periods_of(bars, 300, 429, 559)
    t0 = time.perf_counter()
    seg = evaluate_segments(bars, f_target, P)
    t1 = time.perf_counter()
    ovl = evaluate_overlay(bars, v_target, P)
    t2 = time.perf_counter()
    print(f"\ndefault-parameter timing (12 stocks x 2 periods x 130 days): "
          f"evaluate_segments {t1 - t0:.2f}s, evaluate_overlay {t2 - t1:.2f}s")
    assert seg.params["n_boot"] == 2000 and seg.params["n_shuffle"] == 200 and ovl.params["n_boot"] == 2000
    assert all(c.B_valid is not None for c in seg.cells + ovl.cells)
    assert t2 - t0 < 120
