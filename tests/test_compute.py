import numpy as np
import pandas as pd
import pytest

from riskbound import compute, rules


def frame(n=400, seed=0):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(0.02 * rng.standard_normal(n)))
    idx = pd.bdate_range("2019-06-03", periods=n)
    return pd.DataFrame({"O": c, "H": c * 1.01, "L": c * 0.99, "C": c, "V": np.full(n, 1e6)}, index=idx)


def test_default_is_vf75():
    d = frame()
    out = compute.compute_symbol("AAA", d)
    w = rules.weights(d["C"].to_numpy(dtype=float), **rules.PRESETS["vf75"])
    j = len(d) - 1
    assert out["w"] == w["w"][j] and out["w_vol"] == w["w_vol"][j] and out["wV"] == w["wV"][j]
    assert out["above_sma200"] is bool(w["up"][j])
    assert out["sigma"] == w["sig"][j] and out["sigma_tgt"] == w["tgt"][j] and out["sma200"] == w["sma"][j]
    assert out["vol_ratio"] == w["sig"][j] / w["tgt"][j]
    assert out["symbol"] == "AAA" and out["date"] == str(d.index[-1].date()) and out["close"] == d["C"].iloc[-1]
    assert out["bars"] == len(d) and out["first_bar"] == "2019-06-03"
    assert set(out) == {"symbol", "date", "close", "w", "w_vol", "wV", "above_sma200", "sma200", "sigma",
                        "sigma_tgt", "vol_ratio", "bars", "first_bar"}


@pytest.mark.parametrize("preset", sorted(rules.PRESETS))
def test_presets(preset):
    d = frame(seed=1)
    out = compute.compute_symbol("AAA", d, rules.PRESETS[preset])
    assert out["w"] == rules.weights(d["C"].to_numpy(dtype=float), **rules.PRESETS[preset])["w"][-1]


def test_partial_override():
    assert compute.resolve_params({"vol_floor": None}) == {"vol": True, "vol_floor": None, "below_sma": 0.75}


def test_undefined_indicators_are_none():
    d = frame(n=150)
    out = compute.compute_symbol("AAA", d)
    assert out["sma200"] is None and out["sigma_tgt"] is None and out["vol_ratio"] is None
    assert out["sigma"] is not None
    assert out["w"] == 1.0 and out["above_sma200"] is True
