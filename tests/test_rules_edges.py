import json
import math
from pathlib import Path

import numpy as np
import pytest

from riskbound import rules


def _rw(n, seed=0, vol=0.02):
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(vol * rng.standard_normal(n)))


def test_constants():
    assert (rules.SIG_N, rules.MED_MIN, rules.SMA_N) == (20, 250, 200)
    assert (rules.THRESH, rules.TOL, rules.HALF, rules.LOW) == (0.10, 1e-9, 0.5, 0.75)
    assert rules.ANCHOR == "2019-06-01"
    assert rules.PRESETS == {
        "v": {"vol": True, "vol_floor": None, "below_sma": 1.0},
        "f": {"vol": False, "vol_floor": None, "below_sma": 0.5},
        "vf": {"vol": True, "vol_floor": None, "below_sma": 0.5},
        "vf75": {"vol": True, "vol_floor": 0.75, "below_sma": 0.75},
    }


def test_should_rebalance_threshold():
    assert rules.should_rebalance(0.70, 0.80) is True
    assert rules.should_rebalance(0.80, 0.70) is True
    assert rules.should_rebalance(0.701, 0.80) is False
    assert rules.should_rebalance(0.75, 0.75) is False
    assert rules.should_rebalance(1.0, 0.5) is True
    # the 1e-9 tolerance itself: |delta| in [0.10 - 1e-9, 0.10) still rebalances
    assert rules.should_rebalance(0.8 - (0.10 - 5e-10), 0.8) is True
    assert rules.should_rebalance(0.8 - (0.10 - 2e-9), 0.8) is False


def test_sigma_hat_definition():
    C = _rw(60, seed=1)
    s = rules.sigma_hat(C)
    assert np.isnan(s[:20]).all() and np.isfinite(s[20:]).all()
    r = np.diff(np.log(C))
    assert math.isclose(s[20], np.std(r[0:20], ddof=1) * math.sqrt(252), rel_tol=1e-12)
    assert math.isclose(s[59], np.std(r[39:59], ddof=1) * math.sqrt(252), rel_tol=1e-12)
    assert np.isnan(rules.sigma_hat(C[:20])).all()


def test_sma_undefined_prefix():
    C = _rw(250)
    m = rules.sma(C)
    assert np.isnan(m[:199]).all() and np.isfinite(m[199:]).all()
    assert math.isclose(m[199], C[:200].mean(), rel_tol=1e-12)
    assert np.isnan(rules.sma(C[:199])).all()


def test_sigma_tgt_needs_250_defined():
    C = _rw(400)
    t = rules.sigma_tgt(rules.sigma_hat(C))
    # sigma_hat defined from j = 20, so the 250th defined value is at j = 269
    assert np.isnan(t[:269]).all() and np.isfinite(t[269:]).all()


def test_undefined_segments_give_full_weight():
    C = _rw(400, vol=0.03)
    out = rules.weights(C, **rules.PRESETS["vf75"])
    assert (out["wV"][:269] == 1.0).all()
    assert out["up"][:199].all()
    assert (out["w"][:199] == out["w_vol"][:199]).all()


def test_zero_sigma_gives_full_weight():
    C = _rw(500, seed=3)
    C[350:380] = C[349]
    out = rules.weights(C, **rules.PRESETS["v"])
    zero = out["sig"] == 0
    # closes 349..379 are identical: 30 zero returns -> 11 all-zero 20-return windows
    assert zero.sum() == 11 and np.isfinite(out["tgt"][zero]).all()
    assert (out["wV"][zero] == 1.0).all()


def test_vol_false_still_reports_sigma():
    C = _rw(400, seed=4)
    out = rules.weights(C, **rules.PRESETS["f"])
    assert (out["w_vol"] == 1.0).all()
    assert set(np.unique(out["w"])) <= {0.5, 1.0}
    assert np.isfinite(out["sig"][20:]).all() and np.isfinite(out["tgt"][269:]).all()
    assert (out["wV"][269:] < 1).any()


def test_vol_floor_branch():
    C = _rw(500, seed=5)
    C[300:] = C[299] * np.exp(np.cumsum(0.05 * np.random.default_rng(6).standard_normal(200)))
    raw = rules.weights(C, vol=True, vol_floor=None, below_sma=1.0)
    fl = rules.weights(C, vol=True, vol_floor=0.75, below_sma=1.0)
    assert (raw["wV"] < 0.75).any()
    assert np.array_equal(fl["w_vol"], np.maximum(0.75, raw["wV"]))
    assert (fl["w_vol"] >= 0.75).all()
    assert np.array_equal(raw["w_vol"], raw["wV"])


def test_below_sma_multiplier():
    C = np.r_[np.linspace(200, 100, 300)]
    out = rules.weights(C, vol=False, vol_floor=None, below_sma=0.3)
    assert (~out["up"][199:]).all()
    assert (out["w"][199:] == 0.3).all() and (out["w"][:199] == 1.0).all()


def test_custom_indicator_lengths():
    C = _rw(200, seed=7)
    out = rules.weights(C, sig_n=10, med_min=50, sma_n=50)
    assert np.isnan(out["sig"][:10]).all() and np.isfinite(out["sig"][10:]).all()
    assert np.isnan(out["tgt"][:59]).all() and np.isfinite(out["tgt"][59:]).all()
    assert np.isnan(out["sma"][:49]).all() and np.isfinite(out["sma"][49:]).all()


def test_golden_fixture_exercises_edge_branches():
    """The golden fixture must actually contain the edge cases it is meant to cover."""
    doc = json.loads((Path(__file__).parent / "fixtures" / "golden_rules.json").read_text())
    by = {s["name"]: np.array(s["C"], dtype=float) for s in doc["series"]}
    flat = rules.weights(by["flat_run"])
    assert (flat["sig"] == 0).sum() >= 5 and np.isfinite(flat["tgt"][flat["sig"] == 0]).all()
    fb = rules.weights(by["floor_boundary"], vol_floor=None)
    d = fb["wV"][300:]
    assert (d < 0.75).sum() >= 10 and ((d > 0.75) & (d < 1)).sum() >= 10
    x = rules.weights(by["sma_crossings"])
    flips = np.count_nonzero(np.diff(x["up"][199:].astype(int)))
    assert flips >= 4
    wu = rules.weights(by["warmup"])
    assert (wu["wV"][269:] < 1).any()
