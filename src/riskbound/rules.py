"""Target-weight rules, ported line by line from the frozen backtest (B5-spec-v1 and its 0.75 variant).

Every numeric operation is copied verbatim (same numpy / pandas calls, same order); the golden test
`tests/test_rules_golden.py` checks bit-for-bit equality against fixtures exported from the frozen code.

- sigma_hat / sigma_tgt / sma      <- b5/signals.py (sigma_hat, sigma_tgt, sma)
- weights: wV, up                  <- b5/signals.targets
- weights: vol_floor, below_sma    <- b5v/sim.targets (np.maximum(LOW, wV); np.where(up, 1.0, LOW))
- should_rebalance (0.10 threshold) <- b5/position.should_rebalance

Arrays returned here are indexed by close j (the target computed at close j, executed at the next open).
The backtest's window arrays carry a sentinel at index 0 and map index t to close j = i0 + t - 1.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

# B5-spec-v1 section 2 frozen parameters
SIG_N = 20
MED_MIN = 250
SMA_N = 200
THRESH = 0.10
TOL = 1e-9
HALF = 0.5
# 0.75 variant: floor on the volatility weight / weight below the 200-day SMA (untested compromise)
LOW = 0.75

# Anchor of the expanding sigma_tgt window: start of the late backtest period's cache.
# Fixed, so it does not drift with the run date.
ANCHOR = "2019-06-01"

# Preset name -> (vol, vol_floor, below_sma). Each preset reproduces one backtest target array.
PRESETS: dict[str, dict] = {
    "v": {"vol": True, "vol_floor": None, "below_sma": 1.0},      # B5 wV
    "f": {"vol": False, "vol_floor": None, "below_sma": HALF},    # B5 wF
    "vf": {"vol": True, "vol_floor": None, "below_sma": HALF},    # B5 wVF
    "vf75": {"vol": True, "vol_floor": LOW, "below_sma": LOW},    # b5v wVF75 (default)
}
DEFAULT_PRESET = "vf75"


def sigma_hat(C: np.ndarray, n: int = SIG_N) -> np.ndarray:
    """sigma_hat_j = std(r_{j-n+1..j}, ddof=1) * sqrt(252); undefined for j < n."""
    C = np.asarray(C, dtype=float)
    out = np.full(len(C), np.nan)
    r = np.diff(np.log(C))
    if len(r) >= n:
        out[n:] = sliding_window_view(r, n).std(axis=1, ddof=1) * math.sqrt(252)
    return out


def sigma_tgt(sig: np.ndarray, min_periods: int = MED_MIN) -> np.ndarray:
    """sigma_tgt_j = median{sigma_hat_i : i <= j, defined}; undefined while fewer than min_periods are defined."""
    return pd.Series(sig).expanding(min_periods=min_periods).median().to_numpy()


def sma(C: np.ndarray, n: int = SMA_N) -> np.ndarray:
    C = np.asarray(C, dtype=float)
    out = np.full(len(C), np.nan)
    if len(C) >= n:
        out[n - 1:] = sliding_window_view(C, n).mean(axis=1)
    return out


def weights(C: np.ndarray, *, vol: bool = True, vol_floor: float | None = LOW, below_sma: float = LOW,
            sig_n: int = SIG_N, med_min: int = MED_MIN, sma_n: int = SMA_N) -> dict[str, np.ndarray]:
    """Per-close target weights (for execution at the open after close j).

    Undefined indicators give wV = 1 and up = True (same as b5 targets).
    - vol=False: the volatility component is constantly 1 (sig / tgt / wV are still returned for display);
    - vol_floor=None: no floor; otherwise w_vol = np.maximum(vol_floor, wV) (same call as b5v);
    - w = w_vol * np.where(up, 1.0, below_sma).
    """
    C = np.asarray(C, dtype=float)
    s = sigma_hat(C, sig_n)
    tg = sigma_tgt(s, med_min)
    m = sma(C, sma_n)
    ok = np.isfinite(s) & np.isfinite(tg) & (s > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        wv = np.where(ok, np.minimum(1.0, tg / np.where(ok, s, 1.0)), 1.0)
    up = np.where(np.isfinite(m), C > m, True).astype(bool)
    if not vol:
        w_vol = np.ones(len(C))
    elif vol_floor is None:
        w_vol = wv
    else:
        w_vol = np.maximum(vol_floor, wv)
    w = w_vol * np.where(up, 1.0, below_sma)
    return {"sig": s, "tgt": tg, "sma": m, "wV": wv, "w_vol": w_vol, "up": up, "w": w}


def should_rebalance(w_new: float, w_last: float) -> bool:
    """Same inequality as the backtest rebalance threshold: |delta| >= 0.10 - 1e-9."""
    return abs(w_new - w_last) >= THRESH - TOL
