"""Controls: exposure-matched buy-and-hold C_dagger and the low-weight segment shuffle P(F).

Ported line by line from the frozen backtest (b5/position.py: f_segments, buyhold_c, match_ok, solve_c_dagger;
b4/position.py: shuffle_layout, shuffle_once).
"""

from __future__ import annotations

import numpy as np

from .ledger import COST, trade_to

MATCH_STOP = 1e-9          # bisection stop tolerance
MATCH_OK = 1e-6            # grading eligibility tolerance
BISECT_MAX = 100
N_SHUFFLE = 200


def f_segments(target: np.ndarray) -> list[tuple[int, int]]:
    """Consecutive opens with target < 1 as [s, e); unrecovered at the end -> e = N + 1. Index 0 is ignored."""
    N = len(target) - 1
    segs, s = [], None
    for t in range(1, N + 1):
        low = target[t] < 1.0
        if low and s is None:
            s = t
        elif not low and s is not None:
            segs.append((s, t))
            s = None
    if s is not None:
        segs.append((s, N + 1))
    return segs


# ---------------------------------------------------------------- C_dagger
def buyhold_c(O: np.ndarray, C: np.ndarray, c: float, cost: float = COST) -> dict:
    """B(c): fully invested at d_0, rebalanced to c at the d_1 open, no trades afterwards
    (vectorized; same as simulate_w(rule='once'))."""
    n, m, X, fee = trade_to(1.0 / C[0], 0.0, O[1], c, cost)
    V = np.r_[1.0, n * C[1:] + m]
    w = np.r_[1.0, n * C[1:] / V[1:]]
    return {"V": V, "w": w, "cost": fee, "trades": int(X > 0)}


def match_ok(resid: float) -> bool:
    return abs(resid) <= MATCH_OK


def solve_c_dagger(O: np.ndarray, C: np.ndarray, goal: float, cost: float = COST) -> dict:
    """Bisection on c in (0, 1): keep the evaluated mid with the smallest |rho| (ties: the first evaluated);
    never fall back to an endpoint."""
    lo, hi = 0.0, 1.0
    best = None
    it = 0
    for it in range(1, BISECT_MAX + 1):
        mid = (lo + hi) / 2
        rho = float(buyhold_c(O, C, mid, cost)["w"][1:].mean() - goal)
        if best is None or abs(rho) < abs(best[1]):
            best = (mid, rho)
        if abs(rho) <= MATCH_STOP:
            break
        if rho < 0:
            lo = mid
        else:
            hi = mid
    return {"c": best[0], "resid": best[1], "iters": it, "matched": match_ok(best[1])}


# ---------------------------------------------------------------- low-weight segment shuffle
def shuffle_layout(segments: list[tuple[int, int]], N: int) -> dict:
    """Shufflable blocks, placement range R, free days F, and whether the legal placement is unique."""
    tail = [g for g in segments if g[1] == N + 1]
    full = [g for g in segments if g[1] <= N]
    R = N if not tail else tail[0][0] - 1
    L = [e - s for s, e in full]
    F = R - sum(x + 1 for x in L)
    unique = len(L) == 0 or (F == 0 and len(set(L)) <= 1)
    return {"tail": tail, "L": L, "R": R, "F": F, "k": len(L), "unique": unique}


def shuffle_once(layout: dict, rng: np.random.Generator) -> list[tuple[int, int]]:
    """Direct construction (same distribution as independent uniform placement + rejection):
    pi = permutation(k); c = sort(choice(F+k, k)); start_j = 1 + (c_j - (j-1)) + sum_{i<j} L_{pi_i}.
    k = 0 does not touch the generator."""
    L, F, k = layout["L"], layout["F"], layout["k"]
    if k == 0:
        return list(layout["tail"])
    pi = rng.permutation(k)
    c = np.sort(rng.choice(F + k, size=k, replace=False))
    segs, used = [], 0
    for j in range(k):
        Lj = L[pi[j]]
        start = 1 + (int(c[j]) - j) + used
        segs.append((start, start + Lj))
        used += Lj + 1
    return segs + list(layout["tail"])
