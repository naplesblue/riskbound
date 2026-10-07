"""Ledger: hand-computed trades and costs, threshold boundary, equality with the segment ledger, metric units."""

import math

import numpy as np
import pytest

from riskbound.harness import controls, ledger


def test_constants():
    assert ledger.COST == 0.001 and ledger.HALF == 0.5


def test_threshold_boundary():
    # spec 3.2: w_open = 0.80, w* = 0.70 -> delta 0.10, rebalance; w* = 0.701 -> 0.099, no rebalance
    assert ledger.decide("threshold", 5, 0.70, 0.8, 0.80) is True
    assert ledger.decide("threshold", 5, 0.701, 0.8, 0.80) is False


def test_decide_rules():
    assert ledger.decide("never", 1, 0.5, 1.0, 1.0) is False
    assert ledger.decide("daily", 7, 1.0, 1.0, 1.0) is True
    assert ledger.decide("once", 1, 0.5, 1.0, 1.0) is True and ledger.decide("once", 2, 0.5, 1.0, 1.0) is False
    assert ledger.decide("change", 1, 0.5, 1.0, 1.0) is True       # target before d_1 counts as 1
    assert ledger.decide("change", 3, 0.5, 0.5, 0.5) is False
    with pytest.raises(ValueError):
        ledger.decide("weekly", 1, 1.0, 1.0, 1.0)


def test_trade_to_hand_example():
    n, m = 1 / 100.0, 0.0                       # d_0 close 100: fully invested, no cost
    # sell at open 110 to 0.5: V_open = 1.1, X = 1.1 - 0.55 = 0.55, n = 0.01 - 0.55/110 = 0.005,
    # m = 0.55 - 0.001*0.55 = 0.54945, fee = 0.00055
    n, m, X, fee = ledger.trade_to(n, m, 110.0, 0.5)
    assert math.isclose(X, 0.55, abs_tol=1e-12) and math.isclose(n, 0.005, abs_tol=1e-12)
    assert math.isclose(m, 0.54945, abs_tol=1e-12) and math.isclose(fee, 0.00055, abs_tol=1e-12)
    # partial buy at open 100 to 0.7: V_open = 0.5 + 0.54945 = 1.04945, want = 0.734615 - 0.5 = 0.234615 < m/1.001
    n2, m2, X2, fee2 = ledger.trade_to(n, m, 100.0, 0.7)
    assert math.isclose(X2, 0.234615, abs_tol=1e-12) and math.isclose(fee2, 0.000234615, abs_tol=1e-12)
    assert math.isclose(n2, 0.005 + 0.00234615, abs_tol=1e-12)
    assert math.isclose(m2, 0.54945 - 1.001 * 0.234615, abs_tol=1e-12)
    # buy to 1: want = m >= m/1.001 -> X = m/1.001, cash exhausted
    n3, m3, X3, fee3 = ledger.trade_to(n, m, 100.0, 1.0)
    assert math.isclose(X3, 0.54945 / 1.001, abs_tol=1e-12) and m3 == 0.0
    assert math.isclose(n3, 0.005 + 0.54945 / 1.001 / 100, abs_tol=1e-12)
    assert math.isclose(fee3, 0.001 * 0.54945 / 1.001, abs_tol=1e-12)
    # already at or above target with no cash: no trade
    assert ledger.trade_to(0.01, 0.0, 100.0, 1.0) == (0.01, 0.0, 0.0, 0.0)


def _ohlc(N, seed):
    rng = np.random.default_rng(seed)
    C = 100 * np.exp(np.cumsum(0.02 * rng.standard_normal(N + 1)))
    O = C * np.exp(0.005 * rng.standard_normal(N + 1))
    return O, C


@pytest.mark.parametrize("low", [0.5, 0.75, 0.0])
def test_two_level_target_equals_segment_ledger(low):
    O, C = _ohlc(300, 1)
    up = np.sin(np.arange(301) / 9.0) > -0.2
    target = np.where(up, 1.0, low)
    target[0] = 1.0
    target[-15:] = low                                   # unrecovered tail segment
    segs = controls.f_segments(target)
    assert segs[-1][1] == 301 and len(segs) >= 5
    a = ledger.simulate_w(O, C, target, "change")
    b = ledger.simulate_segments(O, C, segs, low=low)
    assert np.array_equal(a["V"], b["V"]) and np.array_equal(a["w"], b["w"])
    assert a["cost"] == b["cost"] and a["trades"] == b["trades"]
    if low == 0.5:
        assert np.array_equal(ledger.simulate_segments(O, C, segs)["V"], a["V"])     # default low = HALF


def test_simulate_w_basic_paths():
    O, C = _ohlc(50, 2)
    A = ledger.simulate_w(O, C, np.ones(51), "never")
    assert np.allclose(A["V"], C / C[0], rtol=1e-12) and A["trades"] == 0 and A["cost"] == 0.0
    assert (A["w"] == 1.0).all()


def test_metrics_unit_example():
    V = np.array([1.0, 1.02, 0.99, 1.03, 1.01])
    out = ledger.metrics(V, np.array([1.0, 0.9, 0.8, 0.7, 0.6]))
    r = [1.02 / 1.0 - 1, 0.99 / 1.02 - 1, 1.03 / 0.99 - 1, 1.01 / 1.03 - 1]
    mean = sum(r) / 4
    sd = math.sqrt(sum((x - mean) ** 2 for x in r) / 3)
    assert math.isclose(out["sharpe"], mean / sd * math.sqrt(252), rel_tol=1e-12)
    mdd = 1 - 0.99 / 1.02
    cagr = 1.01 ** (252 / 4) - 1
    assert math.isclose(out["mdd"], mdd, rel_tol=1e-12)
    assert math.isclose(out["cagr"], cagr, rel_tol=1e-12)
    assert math.isclose(out["calmar"], cagr / mdd, rel_tol=1e-12)
    assert math.isclose(out["ret"], 0.01, rel_tol=1e-9)
    assert math.isclose(out["avg_w"], 0.75, rel_tol=1e-12)
    lr = [math.log(V[i] / V[i - 1]) for i in range(1, 5)]
    lm = sum(lr) / 4
    assert math.isclose(out["vol"], math.sqrt(sum((x - lm) ** 2 for x in lr) / 3) * math.sqrt(252), rel_tol=1e-12)


def test_metrics_undefined_cases():
    flat = ledger.metrics(np.ones(6))
    assert math.isnan(flat["sharpe"]) and flat["mdd"] == 0.0 and math.isnan(flat["calmar"])
