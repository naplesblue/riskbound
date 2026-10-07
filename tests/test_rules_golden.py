"""Bit-for-bit equality with the frozen backtest (fixture from scripts/gen_golden_fixture.py).

riskbound arrays are per close j; source window arrays carry a sentinel at index 0 and index t maps to
close j = i0 + t - 1. Compare riskbound[i0 : i0 + N] with source[1 : N + 1].
"""

import json
from pathlib import Path

import numpy as np
import pytest

from riskbound import rules

FIXTURE = Path(__file__).parent / "fixtures" / "golden_rules.json"
DOC = json.loads(FIXTURE.read_text())

# preset -> source array compared with `w` (the D1 mapping table)
PRESET_SRC = {"v": "wV", "f": "wF", "vf": "wVF", "vf75": "wVF75"}


def _dec(a):
    if a and isinstance(a[0], bool):
        return np.array(a, dtype=bool)
    return np.array([np.nan if x is None else x for x in a], dtype=float)


CASES = [(s["name"], w["i0"], preset)
         for s in DOC["series"] for w in s["windows"] for preset in PRESET_SRC]


def _get(name, i0):
    s = next(s for s in DOC["series"] if s["name"] == name)
    w = next(w for w in s["windows"] if w["i0"] == i0)
    return np.array(s["C"], dtype=float), w["N"], {k: _dec(v) for k, v in w["src"].items()}


def test_fixture_header():
    assert DOC["generated_with"] == "python -B"
    assert len(DOC["yn_commit"]) == 40
    assert DOC["n_series"] == len(DOC["series"]) >= 5
    assert all(len(s["C"]) >= 600 for s in DOC["series"])
    assert all(len(s["windows"]) >= 2 for s in DOC["series"])


@pytest.mark.parametrize("name,i0,preset", CASES)
def test_golden(name, i0, preset):
    C, N, src = _get(name, i0)
    got = rules.weights(C, **rules.PRESETS[preset])
    sl = slice(i0, i0 + N)
    assert np.array_equal(got["w"][sl], src[PRESET_SRC[preset]][1:N + 1], equal_nan=True)
    assert np.array_equal(got["up"][sl], src["up"][1:N + 1])
    assert np.array_equal(got["wV"][sl], src["wV"][1:N + 1], equal_nan=True)
    for k in ("sig", "tgt", "sma"):
        assert np.array_equal(got[k][sl], src[k][1:N + 1], equal_nan=True), k
    if preset == "vf75":
        assert np.array_equal(got["w_vol"][sl], src["wV75"][1:N + 1], equal_nan=True)
