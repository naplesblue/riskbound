"""Evidence-boundary block: what the historical backtest established, and whether it covers the current config.

The block separates two things:
- component findings: fixed historical results of the pre-registered backtest (B5 grading table, section 1);
- applicability: whether each finding applies to the *effective* configuration and symbol being shown.

Applicability is decided by value, never by preset name:
1. effective config = PRESETS[preset] overridden by explicit vol_floor / below_sma; anchor and indicator
   lengths are recorded separately;
2. any deviation from the frozen parameters (anchor, sig_n, med_min, sma_n) -> "untested_parameters",
   every finding applies_to_current_config = false, deviations listed;
3. within the frozen boundary, the (vol, vol_floor, below_sma) triple is matched against the tested rules:
   V -> V1, V2; F -> F1, F2; VF -> VF1 and "VF drawdown never tested"; anything else (including the
   vf75 default) -> "untested_parameters", all false.
"""

from __future__ import annotations

from . import rules

BACKTEST_UNIVERSE = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "AMD", "NFLX",
                     "CRM", "ORCL", "ADBE", "QCOM", "TXN", "INTU", "AMAT", "MU", "NOW", "PANW",
                     "GOOG", "TSM", "ASML", "IBM", "ISRG")

EVIDENCE_WINDOW = ["2016-09-20/2021-09-17", "2021-09-20/2025-12-31"]
EVIDENCE_UNIVERSE = "25 US large-cap tech stocks (survivorship-biased: today's leaders)"
NOT_ESTABLISHED = ["return improvement", "any directional forecast",
                   "any claim for the current config unless applies_to_current_config is true"]
DISCLAIMER = ("Historical association only, from a backtest on 25 US tech stocks (2016-2025). Reduces drawdown "
              "at the cost of return; likely underperforms buy-and-hold in sustained bull markets. Not investment "
              "advice; no live track record beyond the forward log. No directional forecast is implied.")

FROZEN = {"anchor": rules.ANCHOR, "sig_n": rules.SIG_N, "med_min": rules.MED_MIN, "sma_n": rules.SMA_N}

TESTED_DRAWDOWN_ONLY = "tested_drawdown_only"
COMPONENTS_TESTED_SEPARATELY = "components_tested_separately"
UNTESTED_PARAMETERS = "untested_parameters"

_EXPOSURE_MATCHED = "C_dagger (exposure-matched buy-and-hold: same realized average exposure, static)"
_SHUFFLE = "P(F) segment shuffle (same segment structure, random timing)"

# Order is fixed; `rule` is the tested source rule whose findings these are.
FINDINGS: tuple[dict, ...] = (
    {"cell": "V1", "component": "V", "rule": "v",
     "claim": "Sharpe improvement vs exposure-matched buy-and-hold", "control": _EXPOSURE_MATCHED,
     "result": "not established",
     "detail": ("+0.044 late / +0.063 early, late period below the 0.05 minimum effect; Holm p = 0.002 "
                "but graded 'not estimable'")},
    {"cell": "V2", "component": "V", "rule": "v",
     "claim": "max drawdown lower than exposure-matched buy-and-hold", "control": _EXPOSURE_MATCHED,
     "result": "passed",
     "detail": "+2.09pp late / +5.92pp early, same sign in both periods, Holm p = 0.002"},
    {"cell": "F1", "component": "F", "rule": "f",
     "claim": "Calmar improvement vs randomly-timed half-position segments", "control": _SHUFFLE,
     "result": "not established",
     "detail": "+0.028 late / -0.007 early, Holm p = 0.742"},
    {"cell": "F2", "component": "F", "rule": "f",
     "claim": "max drawdown lower than randomly-timed half-position segments", "control": _SHUFFLE,
     "result": "passed",
     "detail": "+5.72pp late / +3.27pp early, Holm p = 0.002"},
    {"cell": "VF1", "component": "VF", "rule": "vf",
     "claim": "Sharpe improvement vs exposure-matched buy-and-hold", "control": _EXPOSURE_MATCHED,
     "result": "not established",
     "detail": "+0.059 late / -0.007 early, Holm p = 0.491"},
    {"cell": None, "component": "VF", "rule": "vf",
     "claim": "drawdown reduction", "control": None,
     "result": "never tested",
     "detail": "the backtest has no drawdown cell for the combined VF rule"},
)

# Tested source rules: preset name -> (config_status). The vf75 default is not among them.
TESTED_RULES = {"v": TESTED_DRAWDOWN_ONLY, "f": TESTED_DRAWDOWN_ONLY, "vf": COMPONENTS_TESTED_SEPARATELY}
_DESCRIPTIVE_ONLY = "(descriptive comparison only)"


def _triple(p: dict) -> tuple:
    return (bool(p["vol"]), p["vol_floor"], float(p["below_sma"]))


def fmt_value(x) -> str:
    if x is None:
        return "none"
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, float):
        return f"{x:g}"
    return str(x)


def effective_config(preset: str = rules.DEFAULT_PRESET, *, vol_floor=..., below_sma=..., anchor: str | None = None,
                     sig_n: int | None = None, med_min: int | None = None, sma_n: int | None = None) -> dict:
    """PRESETS[preset] overridden by explicitly given values (`...` = not given; vol_floor=None means no floor)."""
    if preset not in rules.PRESETS:
        raise ValueError(f"unknown preset {preset!r}; expected one of {sorted(rules.PRESETS)}")
    cfg = {"preset": preset, **rules.PRESETS[preset]}
    if vol_floor is not ...:
        cfg["vol_floor"] = None if vol_floor is None else float(vol_floor)
    if below_sma is not ...:
        cfg["below_sma"] = float(below_sma)
    cfg["anchor"] = anchor if anchor is not None else FROZEN["anchor"]
    cfg["sig_n"] = sig_n if sig_n is not None else FROZEN["sig_n"]
    cfg["med_min"] = med_min if med_min is not None else FROZEN["med_min"]
    cfg["sma_n"] = sma_n if sma_n is not None else FROZEN["sma_n"]
    cfg["rebalance_threshold"] = rules.THRESH
    return cfg


def weight_params(cfg: dict) -> dict:
    """Keyword arguments for rules.weights / compute.compute_symbol from an effective config."""
    return {k: cfg[k] for k in ("vol", "vol_floor", "below_sma", "sig_n", "med_min", "sma_n")}


def matched_rule(cfg: dict) -> str | None:
    """Tested source rule whose (vol, vol_floor, below_sma) equals the effective triple, by value."""
    t = _triple(cfg)
    for name in TESTED_RULES:
        if _triple(rules.PRESETS[name]) == t:
            return name
    return None


def classify(cfg: dict) -> tuple[str, list[str], str | None]:
    """Return (config_status, deviations, matched tested rule or None)."""
    frozen_dev = [f"{k}={fmt_value(cfg[k])}" for k in FROZEN if cfg[k] != FROZEN[k]]
    rule = matched_rule(cfg)
    triple_dev: list[str] = []
    if rule is None:
        if _triple(cfg) == _triple(rules.PRESETS["vf75"]):
            triple_dev = [f"vol_floor={fmt_value(cfg['vol_floor'])} {_DESCRIPTIVE_ONLY}",
                          f"below_sma={fmt_value(cfg['below_sma'])} {_DESCRIPTIVE_ONLY}"]
        else:
            # values that differ from the chosen preset (the preset itself is a tested rule here)
            base = rules.PRESETS[cfg["preset"]]
            triple_dev = [f"{k}={fmt_value(cfg[k])}" for k in ("vol", "vol_floor", "below_sma") if cfg[k] != base[k]]
    deviations = frozen_dev + triple_dev
    if frozen_dev or rule is None:
        return UNTESTED_PARAMETERS, deviations, None
    return TESTED_RULES[rule], deviations, rule


def universe_membership(symbols: list[str] | None) -> str | None:
    """None without symbols; the common value for all symbols; "mixed" otherwise."""
    if not symbols:
        return None
    vals = {"in_backtest_universe" if s.upper() in BACKTEST_UNIVERSE else "not_backtested" for s in symbols}
    return vals.pop() if len(vals) == 1 else "mixed"


def build(cfg: dict, symbols: list[str] | None = None, data_asof: str | None = None,
          forward_track_since: str | None = None) -> dict:
    """The evidence block for an effective config (see `effective_config`). Key names are a fixed contract."""
    status, deviations, rule = classify(cfg)
    findings = [{"cell": f["cell"], "component": f["component"], "claim": f["claim"], "control": f["control"],
                 "result": f["result"], "detail": f["detail"],
                 "applies_to_current_config": rule is not None and f["rule"] == rule}
                for f in FINDINGS]
    return {
        "config": dict(cfg),
        "config_status": status,
        "deviations": deviations,
        "universe_membership": universe_membership(symbols),
        "component_findings": findings,
        "not_established": list(NOT_ESTABLISHED),
        "evidence_window": list(EVIDENCE_WINDOW),
        "evidence_universe": EVIDENCE_UNIVERSE,
        "forward_track_since": forward_track_since,
        "data_asof": data_asof,
        "disclaimer": DISCLAIMER,
    }
