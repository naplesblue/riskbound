"""Evaluate a position-sizing rule with the frozen grading procedure (interface demo).

The data below is synthetic, so the result is NOT evidence of anything; the example only shows the interface.
Replace `bars` with your own daily O/H/L/C/V frames (including a warm-up before the first period) and
`target_fn` with your rule: it receives the closes up to the previous day and returns one target weight per close.
"""

import time

import numpy as np
import pandas as pd

from riskbound import rules
from riskbound.harness import evaluate_overlay


def synthetic_stock(rng: np.random.Generator, n: int, start: str = "2014-01-02") -> pd.DataFrame:
    """Geometric Brownian motion with volatility clustering (GARCH(1,1)-like variance)."""
    var, r = 0.015 ** 2, np.empty(n)
    for t in range(n):
        r[t] = 0.0003 + np.sqrt(var) * rng.standard_normal()
        var = 2e-6 + 0.08 * r[t] ** 2 + 0.9 * var
    C = 100 * np.exp(np.cumsum(r))
    O = np.r_[C[0], C[:-1]] * np.exp(0.002 * rng.standard_normal(n))
    idx = pd.bdate_range(start, periods=n)
    return pd.DataFrame({"O": O, "H": np.maximum(O, C), "L": np.minimum(O, C), "C": C, "V": 1e6}, index=idx)


rng = np.random.default_rng(7)
bars = {f"SYN{k:02d}": synthetic_stock(rng, 1600) for k in range(12)}
periods = [("2015-06-01", "2018-06-29"), ("2018-07-02", "2020-02-28")]   # two periods; same sign required


def target_fn(C: np.ndarray) -> np.ndarray:
    """Volatility targeting without floor or trend filter (the tested "v" preset)."""
    return rules.weights(C, vol=True, vol_floor=None, below_sma=1.0)["w"]


t0 = time.perf_counter()
# n_boot=300 keeps the demo fast; the default (and the frozen procedure) is 2000.
result = evaluate_overlay(bars, target_fn, periods, n_boot=300)
print(result.to_markdown())
print(f"\n(synthetic data: interface demo only, not evidence; {time.perf_counter() - t0:.2f}s)")
