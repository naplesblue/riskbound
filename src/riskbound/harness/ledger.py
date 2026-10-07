"""Target-weight ledger: shares + cash, rebalancing at the open, cost proportional to traded value.

Ported line by line from the frozen backtest (b5/position.py, b4/position.py). Window indices are 0..N:
d_0 is the first close of the window (fully invested, no cost); target[i] (i = 1..N) is the target for the
open of day i and index 0 is a sentinel.

The only generalization: `simulate_segments` (b4 `simulate`) takes the reduced weight `low` as a parameter
instead of the constant HALF = 0.5; with low = 0.5 it is the same computation.
"""

from __future__ import annotations

import math

import numpy as np

from .. import rules

COST = 0.001          # 10 bps of traded value
HALF = 0.5


def trade_to(n: float, m: float, o: float, wstar: float, cost: float = COST) -> tuple[float, float, float, float]:
    """Rebalance to target w* at open o. Returns (n, m, traded value X, fee).
    Reduce: X = n*o - w*V_open; increase: X = min(w*V_open - n*o, m/(1+c))."""
    v_open = n * o + m
    if n * o > wstar * v_open:
        X = n * o - wstar * v_open
        n -= X / o
        m += X - cost * X
        return n, m, X, cost * X
    want = wstar * v_open - n * o
    cap = m / (1 + cost)
    if want <= 0:
        return n, m, 0.0, 0.0
    if want >= cap:                                 # cash exhausted (= the b4 "recover" step)
        X = cap
        n += X / o
        return n, 0.0, X, cost * X
    X = want
    n += X / o
    m -= (1 + cost) * X
    return n, m, X, cost * X


def decide(rule: str, i: int, ws: float, prev: float, w_open: float) -> bool:
    """Whether to rebalance at the open of day i."""
    if rule == "never":
        return False
    if rule == "threshold":
        return rules.should_rebalance(ws, w_open)
    if rule == "change":
        return ws != prev
    if rule == "daily":
        return True
    if rule == "once":
        return i == 1
    raise ValueError(rule)


def simulate_w(O: np.ndarray, C: np.ndarray, target: np.ndarray, rule: str, cost: float = COST) -> dict:
    """rule: never / threshold / change (the target before d_1 counts as 1) / daily / once (d_1 only)."""
    N = len(C) - 1
    n, m = 1.0 / C[0], 0.0
    V = np.empty(N + 1)
    w = np.empty(N + 1)
    V[0], w[0] = 1.0, 1.0
    paid, trades, prev = 0.0, 0, 1.0
    for i in range(1, N + 1):
        ws = float(target[i])
        go = decide(rule, i, ws, prev, n * O[i] / (n * O[i] + m))
        prev = ws
        if go:
            n, m, X, fee = trade_to(n, m, O[i], ws, cost)
            if X > 0:
                paid += fee
                trades += 1
        V[i] = n * C[i] + m
        w[i] = n * C[i] / V[i]
    return {"V": V, "w": w, "cost": paid, "trades": trades}


def simulate_segments(O: np.ndarray, C: np.ndarray, segments: list[tuple[int, int]], cost: float = COST,
                      low: float = HALF) -> dict:
    """Trade only at reduce / recover opens of segments [s, e) (e = N + 1: not recovered in the window).
    Share count + cash recursion; returns close NAV V, weight w, total cost and trade count."""
    N = len(C) - 1
    events = {}
    for s, e in segments:
        events[s] = "reduce"
        if e <= N:
            events[e] = "recover"
    n, m = 1.0 / C[0], 0.0
    V = np.empty(N + 1)
    w = np.empty(N + 1)
    V[0], w[0] = 1.0, 1.0
    paid, trades = 0.0, 0
    for i in range(1, N + 1):
        ev = events.get(i)
        if ev == "reduce":
            v_open = n * O[i] + m
            X = n * O[i] - low * v_open
            n -= X / O[i]
            m += X - cost * X
            paid += cost * X
            trades += 1
        elif ev == "recover":
            X = m / (1 + cost)
            n += X / O[i]
            paid += cost * X
            m = 0.0
            trades += 1
        V[i] = n * C[i] + m
        w[i] = n * C[i] / V[i]
    return {"V": V, "w": w, "cost": paid, "trades": trades}


def _base_metrics(V: np.ndarray, w: np.ndarray | None = None) -> dict:
    """All as fractions (b4 metrics)."""
    N = len(V) - 1
    peak = np.maximum.accumulate(V)
    mdd = float(np.max(1 - V / peak))
    ret = float(V[-1] - 1)
    cagr = float(V[-1] ** (252 / N) - 1) if N > 0 else float("nan")
    calmar = cagr / mdd if mdd > 0 else float("nan")
    lr = np.diff(np.log(V))
    vol = float(lr.std(ddof=1) * math.sqrt(252)) if len(lr) > 1 else float("nan")
    out = {"ret": ret, "mdd": mdd, "cagr": cagr, "calmar": calmar, "vol": vol}
    if w is not None:
        out["avg_w"] = float(w[1:].mean()) if N > 0 else 1.0
    return out


def metrics(V: np.ndarray, w: np.ndarray | None = None) -> dict:
    """ret / mdd / cagr / calmar / vol / avg_w + Sharpe (simple daily returns, ddof=1, rf=0)."""
    out = _base_metrics(V, w)
    rho = V[1:] / V[:-1] - 1
    sd = float(rho.std(ddof=1)) if len(rho) > 1 else float("nan")
    out["sharpe"] = float(rho.mean() / sd * math.sqrt(252)) if sd and np.isfinite(sd) and sd > 0 else float("nan")
    return out
