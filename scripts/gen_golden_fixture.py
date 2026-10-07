"""Generate tests/fixtures/golden_rules.json from the frozen backtest code (read-only).

Usage:
    uv run python scripts/gen_golden_fixture.py --yn <path to the backtest repo> --out tests/fixtures/golden_rules.json

The synthetic close series are built here (fixed seeds). They are fed to the backtest's own interpreter
(`<yn>/.venv/bin/python -B`, no bytecode written) which imports only the pure-function modules
`pattern_backtest.b5.signals` and `pattern_backtest.b5v.sim`, runs
`b5.signals.targets(C_full, i0, N)` -> `b5v.sim.targets(tg)` and returns every output array.
NaN is encoded as null. The output is deterministic (no timestamps, no local paths).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

CHILD = r'''
import json, sys
sys.path.insert(0, "tools")
import _bootstrap  # noqa: F401  (puts src/ on sys.path)
import numpy as np
import pandas as pd
from pattern_backtest.b5 import signals as b5sig
from pattern_backtest.b5v import sim

def enc(a):
    a = np.asarray(a)
    if a.dtype == bool:
        return [bool(x) for x in a]
    return [None if np.isnan(x) else float(x) for x in a.astype(float)]

req = json.load(sys.stdin)
out = {"numpy": np.__version__, "pandas": pd.__version__, "series": []}
for s in req["series"]:
    C = np.array([float(x) for x in s["C"]], dtype=float)
    wins = []
    for i0 in s["i0"]:
        N = len(C) - i0
        tg = sim.targets(b5sig.targets(C, i0, N))
        wins.append({"i0": i0, "N": N, "src": {k: enc(tg[k]) for k in sorted(tg)}})
    out["series"].append(wins)
json.dump(out, sys.stdout)
'''


def _gbm(rng: np.random.Generator, vol: np.ndarray, drift: float = 0.0, c0: float = 100.0) -> np.ndarray:
    r = drift + vol * rng.standard_normal(len(vol))
    return c0 * np.exp(np.cumsum(r))


def build_series() -> list[dict]:
    out = []

    # 1. Plain random walk.
    rng = np.random.default_rng(1)
    out.append({"name": "random_walk", "C": _gbm(rng, np.full(650, 0.02)), "i0": [100, 300]})

    # 2. Random walk with 30 identical consecutive closes (sigma_hat = 0 -> wV = 1).
    rng = np.random.default_rng(2)
    C = _gbm(rng, np.full(650, 0.015))
    C[400:430] = C[399]
    out.append({"name": "flat_run", "C": C, "i0": [100, 300]})

    # 3. Warm-up inside the window: the first 300 closes are in the i0 = 0 window
    #    (sigma_tgt undefined before 250 defined sigma_hat values; SMA undefined for the first 199 closes).
    #    Low volatility first, higher volatility afterwards, so wV < 1 once sigma_tgt is defined.
    rng = np.random.default_rng(3)
    vol = np.r_[np.full(300, 0.008), np.full(320, 0.02)]
    out.append({"name": "warmup", "C": _gbm(rng, vol, drift=0.0005), "i0": [0, 100, 300]})

    # 4. sigma_tgt / sigma_hat oscillating around 0.75 (exercises the vol_floor branch): a long base regime
    #    sets the median, then alternating blocks with volatility just below / above base / 0.75.
    rng = np.random.default_rng(4)
    blocks = [np.full(25, 0.0125 if k % 2 == 0 else 0.0145) for k in range(10)]
    vol = np.r_[np.full(450, 0.01), *blocks]
    out.append({"name": "floor_boundary", "C": _gbm(rng, vol), "i0": [100, 300]})

    # 5. Repeated crossings of the 200-day SMA.
    rng = np.random.default_rng(5)
    t = np.arange(700)
    C = 100.0 * (1 + 0.15 * np.sin(2 * np.pi * t / 90)) * np.exp(np.cumsum(0.004 * rng.standard_normal(700)))
    out.append({"name": "sma_crossings", "C": C, "i0": [100, 300]})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--yn", required=True, type=Path, help="root of the frozen backtest repository (read-only)")
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)

    yn = a.yn.resolve()
    py = yn / ".venv" / "bin" / "python"
    commit = subprocess.run(["git", "-C", str(yn), "rev-parse", "HEAD"], check=True, capture_output=True,
                            text=True).stdout.strip()
    series = build_series()
    req = {"series": [{"C": [float(x) for x in s["C"]], "i0": s["i0"]} for s in series]}
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    res = subprocess.run([str(py), "-B", "-c", CHILD], cwd=yn, input=json.dumps(req), capture_output=True,
                         text=True, env=env)
    if res.returncode != 0:
        sys.stderr.write(res.stderr)
        return res.returncode
    got = json.loads(res.stdout)

    doc = {
        "generator": "scripts/gen_golden_fixture.py",
        "generated_with": "python -B",
        "yn_commit": commit,
        "source": ["tools/pattern_backtest/b5/signals.py:targets", "tools/pattern_backtest/b5v/sim.py:targets"],
        "source_numpy": got["numpy"],
        "source_pandas": got["pandas"],
        "index_note": "source arrays have a sentinel at index 0; index t (1..N) is close j = i0 + t - 1",
        "n_series": len(series),
        "windows": [{"name": s["name"], "len": len(s["C"]), "windows": [{"i0": w["i0"], "N": w["N"]} for w in ws]}
                    for s, ws in zip(series, got["series"])],
        "series": [{"name": s["name"], "C": req_s["C"], "windows": ws}
                   for s, req_s, ws in zip(series, req["series"], got["series"])],
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(doc, f)
        f.write("\n")
    print(f"wrote {a.out}: {len(series)} series, yn_commit {commit[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
