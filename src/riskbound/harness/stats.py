"""Grading statistics: per-period stock-weighted delta, two-period T, stock-block shift bootstrap,
untestable rules 1 / 3 / 4 / 5, Holm, and the three-level verdict.

Ported line by line from the frozen backtest (b2/stats.py: weight_matrix, shift_p, holm; b4/stats.py:
period_stat, T_point, T_boot, confirm, reaches, version_ok; b5/stats.py: grade, cell). The b5 per-cell constant
tables are replaced by explicit `min_effect` / `predicted_sign` arguments.

A period is (a, h): vectors in member order; a_s is set to 0 where h_s = 0.
"""

from __future__ import annotations

import numpy as np

SEED = 20261005
N_BOOT = 2000
MIN_STOCKS = 10            # rule 1
MIN_INFO_BLOCKS = 10       # rule 4
MAX_UNDEF_FRAC = 0.05      # rule 3
TOL = 1e-9

CONSISTENT = "consistent"
INCONSISTENT = "inconsistent"
INCONCLUSIVE = "inconclusive"
AS_PREDICTED = "as_predicted"
OPPOSITE = "opposite"


def weight_matrix(m: int, n_boot: int, seed: int) -> np.ndarray:
    """default_rng(seed) draws an n_boot x m matrix of stock indices -> per row, how often each stock is drawn."""
    idx = np.random.default_rng(seed).integers(0, m, size=(n_boot, m))
    return np.stack([np.bincount(r, minlength=m) for r in idx]).astype(float)


def shift_p(T: float, Ts: np.ndarray) -> tuple[float, int]:
    """Zero-centred shift method: p = (1 + #{|T* - T| >= |T|}) / (B' + 1); p = 1 when T = 0. Returns (p, tail)."""
    Ts = Ts[np.isfinite(Ts)]
    if T == 0 or not np.isfinite(T):
        return 1.0, int(Ts.size)
    tail = int(np.sum(np.abs(Ts - T) >= abs(T)))
    return (1 + tail) / (Ts.size + 1), tail


def holm(p: np.ndarray) -> np.ndarray:
    """Holm step-down adjustment; monotone, capped at 1, in the original order."""
    p = np.asarray(p, dtype=float)
    m = p.size
    order = np.argsort(p, kind="stable")
    adj = np.maximum.accumulate((m - np.arange(m)) * p[order])
    out = np.empty(m)
    out[order] = np.minimum(adj, 1.0)
    return out


def period_stat(w: np.ndarray, a: np.ndarray, h: np.ndarray):
    """Delta_period(w) = sum(w h a) / sum(w h); w is 1-D or 2-D (B x m). Zero denominator -> NaN."""
    hm = h.astype(float)
    a0 = np.where(h, a, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return (w @ (hm * a0)) / (w @ hm)


def T_point(per: list[tuple[np.ndarray, np.ndarray]]) -> float:
    m = len(per[0][0])
    return float(np.mean([period_stat(np.ones(m), a, h) for a, h in per]))


def T_boot(per, W: np.ndarray) -> np.ndarray:
    return np.mean([period_stat(W, a, h) for a, h in per], axis=0)


def confirm(per, W: np.ndarray, n_boot: int, extra_rules: list[int] | None = None,
            n_rule1: list[int] | None = None, min_n: int = MIN_STOCKS) -> dict:
    """T, shift p and untestable rules 1 / 3 / 4 / 5 (+ caller-supplied rules). n_rule1 = n per period."""
    T = T_point(per)
    Ts = T_boot(per, W)
    ok = Ts[np.isfinite(Ts)]
    rules = set(extra_rules or [])
    if n_rule1 is not None and any(n < min_n for n in n_rule1):
        rules.add(1)
    if any(int(h.sum()) < MIN_INFO_BLOCKS for _, h in per):
        rules.add(4)
    if n_boot - ok.size > MAX_UNDEF_FRAC * n_boot:
        rules.add(3)
    if ok.size == 0 or ok.max() - ok.min() == 0:
        rules.add(5)
    p_raw, tail = shift_p(T, Ts)
    rules = sorted(rules, key=str)
    return {"T": T if np.isfinite(T) else None, "B_valid": int(ok.size), "tail": tail,
            "T_star_sd": float(ok.std()) if ok.size else None,
            "T_ci": [float(np.percentile(ok, 2.5)), float(np.percentile(ok, 97.5))] if ok.size else None,
            "untestable": rules, "p": 1.0 if rules else p_raw, "p_shift_raw": p_raw,
            "p_is_min": (not rules) and p_raw == 1 / (ok.size + 1)}


def reaches(x, thr: float) -> bool:
    return x is not None and np.isfinite(x) and abs(x) >= thr - TOL


def version_ok(d: list, wl: list, thr: float) -> tuple[bool, bool]:
    """(meets the consistent condition, meets the inconsistent condition). d / wl = per-period deltas."""
    if any(x is None or not np.isfinite(x) for x in d):
        return False, False
    big = all(reaches(x, thr) for x in d)
    same = np.sign(d[0]) == np.sign(d[1]) and np.sign(d[0]) != 0
    wl_ok = all(x is not None and np.isfinite(x) and np.sign(x) == np.sign(d[0]) for x in wl)
    return bool(big and same and wl_ok), bool(big and np.sign(d[0]) * np.sign(d[1]) < 0)


def grade(d: list, wl: list, untestable: list, min_effect: float, predicted_sign: int) -> tuple[str, str | None]:
    """Order: untestable first -> consistent -> inconsistent -> inconclusive."""
    if untestable:
        return INCONCLUSIVE, None
    ok, inc = version_ok(d, wl, min_effect)
    if ok:
        return CONSISTENT, (AS_PREDICTED if np.sign(d[0]) == predicted_sign else OPPOSITE)
    if inc:
        return INCONSISTENT, None
    return INCONCLUSIVE, None


def cell_stats(per: list[tuple[np.ndarray, np.ndarray]], wl_mask: np.ndarray | None, W: np.ndarray,
               min_effect: float, predicted_sign: int, min_n: int = MIN_STOCKS) -> dict:
    """per = [(g, h) for each period] in member order; g is set to 0 where h = 0.
    wl_mask = None skips the subset condition (the b5 `cell` with an explicit subset)."""
    per = [(np.where(h, g, 0.0).astype(float), np.asarray(h, dtype=bool)) for g, h in per]
    n1 = [int(h.sum()) for _, h in per]
    conf = confirm(per, W, W.shape[0], [], n1, min_n)
    ones = lambda a: np.ones(len(a))  # noqa: E731
    d = [float(period_stat(ones(a), a, h)) for a, h in per]
    if wl_mask is None:
        wl, wl_n = [], None
    else:
        wl = [float(period_stat(ones(a), a, h & wl_mask)) for a, h in per]
        wl_n = [int((h & wl_mask).sum()) for _, h in per]
    verdict, direction = grade(d, wl, conf["untestable"], min_effect, predicted_sign)
    return {"verdict": verdict, "direction": direction, "d": d, "wl": wl, "h_count": n1, "wl_h_count": wl_n, **conf}
