"""One-stop entry points: evaluate an overlay rule against the exposure-matched control, or a two-level
segment rule against the segment shuffle, over two (or more) periods, with the frozen grading procedure.

Per stock and period (as in the frozen runner `stock_compute` / `shuffle_all` / `g_row` / `grid`):
- window = rows with start <= date <= end; d_0 = first window row; target for the open of day t reads close
  j = i0 + t - 1 only, and `target_fn` never receives closes after j = i0 + N - 1;
- A = buy-and-hold; strategy = simulate_w(target, rebalance);
- exposure_matched: C_dagger = buyhold_c(solve_c_dagger(avg_w(strategy)));
- segment_shuffle: segments of the strategy target, n_shuffle placements, rng = default_rng(seed + r) with r as
  the outer loop, then periods in order, then members in order (stocks without segments do not draw);
- g / h per cell (h = 0 for no strategy trade / no segment, unmatched C_dagger, or a non-finite metric);
- one bootstrap weight matrix shared by all cells and periods; Holm over the cells in the given order.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Callable, Literal

import numpy as np
import pandas as pd

from . import controls, ledger, stats

M_KEYS = ("ret", "cagr", "mdd", "vol", "sharpe", "calmar", "avg_w")
SINGLE_PERIOD = "not_applicable (single period)"
SUBSET_SKIPPED = "skipped"
SUBSET_EMPTY = "no informative members"
SUBSET_PASSED = "passed"
SUBSET_FAILED = "failed"


@dataclass(frozen=True)
class CellSpec:
    name: str                          # e.g. "MDD", "Sharpe", "Calmar"
    metric: Literal["mdd", "sharpe", "calmar"]
    control: Literal["exposure_matched", "segment_shuffle"]
    min_effect: float                  # mdd: percentage points; sharpe/calmar: absolute difference
    predicted_sign: int                # +1 means strategy better than control (g > 0)


B5_OVERLAY_CELLS = (CellSpec("MDD", "mdd", "exposure_matched", 1.0, +1),
                    CellSpec("Sharpe", "sharpe", "exposure_matched", 0.05, +1))
B5_SEGMENT_CELLS = (CellSpec("MDD", "mdd", "segment_shuffle", 1.0, +1),
                    CellSpec("Calmar", "calmar", "segment_shuffle", 0.05, +1))


@dataclass
class CellResult:
    name: str
    metric: str
    control: str
    predicted_sign: int
    min_effect: float
    d: dict[str, float]
    wl: dict[str, float] | None
    T: float | None
    T_ci: list[float] | None
    T_star_sd: float | None
    B_valid: int | None
    p: float | None
    p_holm: float | None
    holm_reject: bool
    untestable: list[int]
    verdict: str
    direction: str | None
    subset_check: str
    h_count: dict[str, int] = field(default_factory=dict)


@dataclass
class EvalResult:
    cells: list[CellResult]
    per_stock: dict[tuple[str, str], dict]
    members: list[str]
    periods: list[str]
    params: dict

    def to_dict(self) -> dict:
        return {"cells": [asdict(c) for c in self.cells],
                "per_stock": {f"{p}|{s}": _plain(v) for (p, s), v in self.per_stock.items()},
                "members": list(self.members), "periods": list(self.periods), "params": _plain(self.params)}

    def to_markdown(self) -> str:
        P = self.periods
        lines = ["## Grading", "",
                 "| cell | metric | control | " + " | ".join(f"delta {p}" for p in P)
                 + " | subset | T | 95% CI | p | Holm p | untestable | verdict | direction | subset_check |",
                 "|" + "---|" * (12 + len(P))]
        for c in self.cells:
            unit = "pp" if c.metric == "mdd" else ""
            wl = "-" if not c.wl else " / ".join(_f(c.wl[p]) for p in P)
            ci = "-" if not c.T_ci else f"[{_f(c.T_ci[0])}, {_f(c.T_ci[1])}]"
            lines.append(f"| {c.name} | {c.metric}{f' ({unit})' if unit else ''} | {c.control} | "
                         + " | ".join(_f(c.d[p]) for p in P)
                         + f" | {wl} | {_f(c.T)} | {ci} | {_f(c.p)} | {_f(c.p_holm)} | "
                         f"{','.join(map(str, c.untestable)) or '-'} | {c.verdict} | {c.direction or '-'} | "
                         f"{c.subset_check} |")
        lines += ["", "Informative stocks per period: "
                  + "; ".join(f"{c.name}: " + ", ".join(f"{p} {c.h_count.get(p, 0)}" for p in P) for c in self.cells),
                  "", "Params: " + ", ".join(f"{k}={v}" for k, v in self.params.items()),
                  "", "## Per stock", "",
                  "| period | symbol | strategy avg_w | trades | control | "
                  + " | ".join(f"g {c.name}" for c in self.cells) + " |",
                  "|" + "---|" * (5 + len(self.cells))]
        for (p, s), r in self.per_stock.items():
            if "c_dagger" in r:
                ctl = f"c={r['c_dagger']['c']:.4f}{'' if r['c_dagger']['matched'] else ' (unmatched)'}"
            else:
                ctl = f"segments={len(r['segments'])}"
            gs = " | ".join(_f(r["g"][c.name]) + ("" if r["h"][c.name] else " (h=0)") for c in self.cells)
            lines.append(f"| {p} | {s} | {r['strategy']['avg_w']:.4f} | {r['strategy']['trades']} | {ctl} | {gs} |")
        return "\n".join(lines)


def _f(x) -> str:
    if x is None:
        return "-"
    x = float(x)
    return "nan" if not math.isfinite(x) else f"{x:.4g}"


def _plain(x):
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, np.generic):
        return x.item()
    return x


def _fin(*xs) -> bool:
    return all(x is not None and np.isfinite(x) for x in xs)


# ---------------------------------------------------------------- windows
def period_label(period: tuple[str, str]) -> str:
    return f"{period[0]}/{period[1]}"


def window(df: pd.DataFrame, start: str, end: str) -> tuple[int, int]:
    """(i0, N): positions of the window start = first row with start <= date <= end; N = rows - 1."""
    idx = df.index
    inside = np.flatnonzero((idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end)))
    if inside.size < 2:
        raise ValueError(f"window {start}/{end} has fewer than 2 rows")
    i0 = int(inside[0])
    if inside[-1] - i0 != inside.size - 1:
        raise ValueError("bars index is not sorted")
    return i0, int(inside.size - 1)


def window_target(target_fn: Callable[[np.ndarray], np.ndarray], C_full: np.ndarray, i0: int, N: int) -> np.ndarray:
    """Targets for opens t = 1..N with a sentinel 1 at index 0 (same shape as the frozen `targets`).
    target_fn only sees closes up to j = i0 + N - 1 (the last close any target reads)."""
    C_in = np.asarray(C_full[:i0 + N], dtype=float)
    full = np.asarray(target_fn(C_in), dtype=float)
    if full.shape != C_in.shape:
        raise ValueError(f"target_fn returned shape {full.shape}, expected {C_in.shape}")
    return np.r_[1.0, full[i0:i0 + N]]


def _prepare(bars, target_fn, periods):
    if len(periods) < 1:
        raise ValueError("at least one period is required")
    members = list(bars)
    if not members:
        raise ValueError("bars is empty")
    seqs = {}
    for p in periods:
        lab = period_label(p)
        for s in members:
            df = bars[s].sort_index()
            i0, N = window(df, *p)
            O = df["O"].to_numpy(dtype=float)[i0:i0 + N + 1]
            C_full = df["C"].to_numpy(dtype=float)
            seqs[(lab, s)] = {"O": O, "C": C_full[i0:i0 + N + 1], "target": window_target(target_fn, C_full, i0, N),
                              "first_date": str(df.index[i0].date()), "last_date": str(df.index[i0 + N].date())}
    return members, [period_label(p) for p in periods], seqs


def _m(sim: dict, extra: dict | None = None) -> dict:
    out = {k: v for k, v in ledger.metrics(sim["V"], sim["w"]).items() if k in M_KEYS}
    out.update(cost=sim["cost"], trades=sim["trades"])
    if extra:
        out.update(extra)
    return out


def _check_cells(cells, control: str) -> None:
    if not cells:
        raise ValueError("at least one cell is required")
    for c in cells:
        if c.control != control:
            raise ValueError(f"cell {c.name}: control {c.control!r} is not available here (expected {control!r})")
        if c.metric not in ("mdd", "sharpe", "calmar"):
            raise ValueError(f"cell {c.name}: unknown metric {c.metric!r}")


# ---------------------------------------------------------------- per-stock g / h
def _g_overlay(cell: CellSpec, rec: dict) -> tuple[float, bool]:
    s, c, mt = rec["strategy"], rec["control"], rec["c_dagger"]
    if s["trades"] < 1 or not mt["matched"]:
        return 0.0, False
    if cell.metric == "mdd":
        return (c["mdd"] - s["mdd"]) * 100, True
    if not _fin(s[cell.metric], c[cell.metric]):
        return 0.0, False
    return s[cell.metric] - c[cell.metric], True


def _g_segments(cell: CellSpec, rec: dict) -> tuple[float, bool]:
    s = rec["strategy"]
    if len(rec["segments"]) < 1:
        return 0.0, False
    if cell.metric == "mdd":
        return (float(np.mean(rec["P"]["mdd"])) - s["mdd"]) * 100, True
    cp = np.array(rec["P"][cell.metric], dtype=float)
    if not (_fin(s[cell.metric]) and np.isfinite(cp).any()):
        return 0.0, False
    return float(s[cell.metric] - np.nanmean(cp)), True


# ---------------------------------------------------------------- grading
def _grade(cells, members, plabels, per_stock, subset, seed, n_boot) -> list[CellResult]:
    wl_mask = None if subset is None else np.array([s in set(subset) for s in members], dtype=bool)
    out = []
    W = stats.weight_matrix(len(members), n_boot, seed) if len(plabels) >= 2 else None
    for c in cells:
        per = []
        for p in plabels:
            g, h = zip(*[(per_stock[(p, s)]["g"][c.name], per_stock[(p, s)]["h"][c.name]) for s in members])
            per.append((np.array(g, dtype=float), np.array(h, dtype=bool)))
        if W is None:
            a, h = np.where(per[0][1], per[0][0], 0.0), per[0][1]
            d = [float(stats.period_stat(np.ones(len(a)), a, h))]
            wl = None if wl_mask is None else [float(stats.period_stat(np.ones(len(a)), a, h & wl_mask))]
            out.append(CellResult(c.name, c.metric, c.control, c.predicted_sign, c.min_effect, dict(zip(plabels, d)),
                                  None if wl is None else dict(zip(plabels, wl)), None, None, None, None, None, None,
                                  False, [], SINGLE_PERIOD, None,
                                  SUBSET_SKIPPED if subset is None else SUBSET_PASSED if wl and np.isfinite(wl[0])
                                  else SUBSET_EMPTY, {plabels[0]: int(h.sum())}))
            continue
        r = stats.cell_stats(per, wl_mask, W, c.min_effect, c.predicted_sign)
        verdict, direction = r["verdict"], r["direction"]
        if subset is None:
            check = SUBSET_SKIPPED
        elif any(n == 0 for n in r["wl_h_count"]):
            check, verdict, direction = SUBSET_EMPTY, stats.INCONCLUSIVE, None
        else:
            s0 = np.sign(r["d"][0])
            check = SUBSET_PASSED if s0 != 0 and all(np.isfinite(x) and np.sign(x) == s0 for x in r["wl"]) \
                else SUBSET_FAILED
        out.append(CellResult(c.name, c.metric, c.control, c.predicted_sign, c.min_effect, dict(zip(plabels, r["d"])),
                              None if subset is None else dict(zip(plabels, r["wl"])), r["T"], r["T_ci"],
                              r["T_star_sd"], r["B_valid"], r["p"], None, False, list(r["untestable"]), verdict,
                              direction, check, dict(zip(plabels, r["h_count"]))))
    if W is not None:
        adj = stats.holm(np.array([c.p for c in out]))
        for c, a in zip(out, adj):
            c.p_holm = float(a)
            c.holm_reject = bool(a <= 0.05)
    return out


def _finish(cells, members, plabels, per_stock, subset, seed, n_boot, params) -> EvalResult:
    graded = _grade(cells, members, plabels, per_stock, subset, seed, n_boot)
    for (p, s), rec in per_stock.items():
        rec.pop("_layout", None)
    return EvalResult(graded, per_stock, members, plabels, params)


def evaluate_overlay(bars: dict[str, pd.DataFrame], target_fn, periods: list[tuple[str, str]], *,
                     cells=B5_OVERLAY_CELLS, rebalance: Literal["threshold", "change"] = "threshold",
                     subset: list[str] | None = None, seed: int = stats.SEED, n_boot: int = stats.N_BOOT,
                     cost: float = ledger.COST) -> EvalResult:
    """Overlay rule vs the exposure-matched buy-and-hold control C_dagger."""
    _check_cells(cells, "exposure_matched")
    if rebalance not in ("threshold", "change"):
        raise ValueError(f"rebalance must be 'threshold' or 'change', got {rebalance!r}")
    members, plabels, seqs = _prepare(bars, target_fn, periods)
    per_stock = {}
    for p in plabels:
        for s in members:
            q = seqs[(p, s)]
            O, C, tg = q["O"], q["C"], q["target"]
            sim = ledger.simulate_w(O, C, tg, rebalance, cost)
            rec = {"first_date": q["first_date"], "last_date": q["last_date"],
                   "A": _m(ledger.simulate_w(O, C, np.ones(len(C)), "never", cost)),
                   "strategy": _m(sim, {"avg_target": float(np.mean(tg[1:]))})}
            goal = rec["strategy"]["avg_w"]
            mt = controls.solve_c_dagger(O, C, goal, cost)
            rec["c_dagger"] = {**mt, "goal": goal, "stop_reached": abs(mt["resid"]) <= controls.MATCH_STOP}
            rec["control"] = _m(controls.buyhold_c(O, C, mt["c"], cost), {"c": mt["c"]})
            gh = {c.name: _g_overlay(c, rec) for c in cells}
            rec["g"] = {k: v[0] for k, v in gh.items()}
            rec["h"] = {k: v[1] for k, v in gh.items()}
            per_stock[(p, s)] = rec
    params = {"control": "exposure_matched", "rebalance": rebalance, "seed": seed, "n_boot": n_boot, "cost": cost,
              "subset": None if subset is None else list(subset), "min_stocks": stats.MIN_STOCKS}
    return _finish(cells, members, plabels, per_stock, subset, seed, n_boot, params)


def _low(target: np.ndarray) -> float | None:
    lows = np.unique(target[1:][target[1:] < 1.0])
    if lows.size > 1:
        raise ValueError(f"segment rules need targets in {{1, low}}; found several low values {lows[:5].tolist()}")
    return float(lows[0]) if lows.size else None


def evaluate_segments(bars: dict[str, pd.DataFrame], target_fn, periods: list[tuple[str, str]], *,
                      cells=B5_SEGMENT_CELLS, subset: list[str] | None = None, seed: int = stats.SEED,
                      n_boot: int = stats.N_BOOT, n_shuffle: int = controls.N_SHUFFLE,
                      cost: float = ledger.COST) -> EvalResult:
    """Two-level segment rule (target in {1, low}) vs the segment shuffle P(F)."""
    _check_cells(cells, "segment_shuffle")
    members, plabels, seqs = _prepare(bars, target_fn, periods)
    per_stock = {}
    for p in plabels:
        for s in members:
            q = seqs[(p, s)]
            O, C, tg = q["O"], q["C"], q["target"]
            low = _low(tg)
            sim = ledger.simulate_w(O, C, tg, "change", cost)
            segs = controls.f_segments(tg)
            lay = controls.shuffle_layout(segs, len(C) - 1)
            L = [e - a for a, e in segs]
            per_stock[(p, s)] = {
                "first_date": q["first_date"], "last_date": q["last_date"],
                "A": _m(ledger.simulate_w(O, C, np.ones(len(C)), "never", cost)),
                "strategy": _m(sim, {"avg_target": float(np.mean(tg[1:])), "low": low, "low_days": int(sum(L)),
                                     "tail": any(e == len(C) for _, e in segs)}),
                "segments": segs,
                "layout": {"k": lay["k"], "F": lay["F"], "R": lay["R"], "unique": lay["unique"]},
                "_layout": lay,
                "P": {k: [] for k in M_KEYS + ("cost", "trades")},
            }
    for r in range(n_shuffle):
        rng = np.random.default_rng(seed + r)
        for p in plabels:
            for s in members:
                b = per_stock[(p, s)]
                if not b["segments"]:
                    continue
                q = seqs[(p, s)]
                sim = ledger.simulate_segments(q["O"], q["C"], controls.shuffle_once(b["_layout"], rng), cost,
                                               b["strategy"]["low"])
                mt = ledger.metrics(sim["V"], sim["w"])
                for k in M_KEYS:
                    b["P"][k].append(mt[k])
                b["P"]["cost"].append(sim["cost"])
                b["P"]["trades"].append(sim["trades"])
                if sim["trades"] != b["strategy"]["trades"]:
                    raise RuntimeError(f"{p} {s} r={r}: shuffled trade count differs from the strategy")
    for rec in per_stock.values():
        gh = {c.name: _g_segments(c, rec) for c in cells}
        rec["g"] = {k: v[0] for k, v in gh.items()}
        rec["h"] = {k: v[1] for k, v in gh.items()}
        P = rec.pop("P")
        rec["shuffle"] = {"n": len(P["mdd"]), "mdd_mean": float(np.mean(P["mdd"])) if P["mdd"] else None,
                          **{f"{k}_nanmean": (float(np.nanmean(P[k])) if np.isfinite(P[k]).any() else None)
                             for k in ("sharpe", "calmar") if P[k]}}
    params = {"control": "segment_shuffle", "seed": seed, "n_boot": n_boot, "n_shuffle": n_shuffle, "cost": cost,
              "subset": None if subset is None else list(subset), "min_stocks": stats.MIN_STOCKS}
    return _finish(cells, members, plabels, per_stock, subset, seed, n_boot, params)
