"""Per-symbol computation: full settled history (from the anchor) -> target weight at the latest settled close.

Recomputed from the anchor every time (sigma_tgt is an expanding median; there is no rolling state to drift).
The target uses close t and is meant for execution at the next open.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import rules


def resolve_params(params: dict | None = None) -> dict:
    """Default preset (vf75) parameters overridden by any keys in `params`."""
    out = dict(rules.PRESETS[rules.DEFAULT_PRESET])
    out.update(params or {})
    return out


def series(d: pd.DataFrame, params: dict | None = None) -> dict[str, np.ndarray]:
    """Full per-close arrays sig / tgt / sma / wV / w_vol / up / w (index = close j)."""
    return rules.weights(d["C"].to_numpy(dtype=float), **resolve_params(params))


def _f(x) -> float | None:
    x = float(x)
    return x if math.isfinite(x) else None


def compute_symbol(sym: str, d: pd.DataFrame, params: dict | None = None) -> dict:
    """d: O/H/L/C/V daily bars from the anchor (settled closes only). Returns the target at the last bar."""
    s = series(d, params)
    j = len(d) - 1
    dates = [str(t.date()) for t in d.index]
    sig, tgt = s["sig"][j], s["tgt"][j]
    return {
        "symbol": sym,
        "date": dates[j],
        "close": float(d["C"].iloc[j]),
        "w": float(s["w"][j]),
        "w_vol": float(s["w_vol"][j]),
        "wV": float(s["wV"][j]),
        "above_sma200": bool(s["up"][j]),
        "sma200": _f(s["sma"][j]),
        "sigma": _f(sig),
        "sigma_tgt": _f(tgt),
        "vol_ratio": _f(sig / tgt) if np.isfinite(sig) and np.isfinite(tgt) and tgt > 0 else None,
        "bars": len(d),
        "first_bar": dates[0],
    }
