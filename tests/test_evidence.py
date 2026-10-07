import json

import pytest

from riskbound import evidence, rules

KEYS = ["config", "config_status", "deviations", "universe_membership", "component_findings", "not_established",
        "evidence_window", "evidence_universe", "forward_track_since", "data_asof", "disclaimer"]


def block(preset="vf75", symbols=None, **kw):
    return evidence.build(evidence.effective_config(preset, **kw), symbols)


def applies(ev):
    return {(f["cell"] or f"{f['component']}-drawdown") for f in ev["component_findings"]
            if f["applies_to_current_config"]}


def test_block_keys_and_constants():
    ev = block()
    assert list(ev) == KEYS
    assert ev["config"] == {"preset": "vf75", "vol": True, "vol_floor": 0.75, "below_sma": 0.75,
                            "anchor": "2019-06-01", "sig_n": 20, "med_min": 250, "sma_n": 200,
                            "rebalance_threshold": 0.10}
    assert ev["evidence_window"] == ["2016-09-20/2021-09-17", "2021-09-20/2025-12-31"]
    assert ev["evidence_universe"] == "25 US large-cap tech stocks (survivorship-biased: today's leaders)"
    assert ev["forward_track_since"] is None and ev["data_asof"] is None and ev["universe_membership"] is None
    assert len(evidence.BACKTEST_UNIVERSE) == 25 == len(set(evidence.BACKTEST_UNIVERSE))
    assert [f["cell"] for f in ev["component_findings"]] == ["V1", "V2", "F1", "F2", "VF1", None]
    assert [f["result"] for f in ev["component_findings"]] == [
        "not established", "passed", "not established", "passed", "not established", "never tested"]
    for f in ev["component_findings"]:
        assert list(f) == ["cell", "component", "claim", "control", "result", "detail", "applies_to_current_config"]
    json.dumps(ev)


def test_default_vf75_untested():
    ev = block()
    assert ev["config_status"] == "untested_parameters" and applies(ev) == set()
    assert ev["deviations"] == ["vol_floor=0.75 (descriptive comparison only)",
                                "below_sma=0.75 (descriptive comparison only)"]


@pytest.mark.parametrize("preset,status,cells", [
    ("v", "tested_drawdown_only", {"V1", "V2"}),
    ("f", "tested_drawdown_only", {"F1", "F2"}),
    ("vf", "components_tested_separately", {"VF1", "VF-drawdown"}),
])
def test_tested_presets(preset, status, cells):
    ev = block(preset)
    assert ev["config_status"] == status and applies(ev) == cells and ev["deviations"] == []


def test_override_breaks_match():
    ev = block("v", vol_floor=0.75)
    assert ev["config_status"] == "untested_parameters" and applies(ev) == set()
    assert ev["deviations"] == ["vol_floor=0.75"]
    assert ev["config"]["preset"] == "v" and ev["config"]["vol_floor"] == 0.75


def test_vol_floor_zero_is_not_none():
    ev = block("v", vol_floor=0.0)
    assert ev["config_status"] == "untested_parameters" and ev["deviations"] == ["vol_floor=0"]


def test_explicit_none_floor_keeps_v():
    ev = block("v", vol_floor=None)
    assert ev["config_status"] == "tested_drawdown_only" and applies(ev) == {"V1", "V2"}


def test_match_by_value_not_name():
    ev = block("vf75", vol_floor=None, below_sma=0.5)
    assert ev["config_status"] == "components_tested_separately" and applies(ev) == {"VF1", "VF-drawdown"}
    ev = block("vf", below_sma=1.0)
    assert ev["config_status"] == "tested_drawdown_only" and applies(ev) == {"V1", "V2"}


def test_other_combo_lists_values():
    ev = block("vf75", below_sma=0.5)
    assert ev["config_status"] == "untested_parameters" and ev["deviations"] == ["below_sma=0.5"]


@pytest.mark.parametrize("kw,dev", [
    ({"anchor": "2018-01-01"}, "anchor=2018-01-01"),
    ({"sig_n": 21}, "sig_n=21"),
    ({"med_min": 100}, "med_min=100"),
    ({"sma_n": 150}, "sma_n=150"),
])
def test_frozen_parameter_deviation(kw, dev):
    ev = block("v", **kw)
    assert ev["config_status"] == "untested_parameters" and applies(ev) == set()
    assert ev["deviations"] == [dev]


def test_frozen_and_triple_deviation_together():
    ev = block("vf75", anchor="2018-01-01")
    assert ev["deviations"][0] == "anchor=2018-01-01" and len(ev["deviations"]) == 3


def test_universe_membership():
    assert evidence.universe_membership(["NVDA"]) == "in_backtest_universe"
    assert evidence.universe_membership(["nvda", "AAPL"]) == "in_backtest_universe"
    assert evidence.universe_membership(["NBIS"]) == "not_backtested"
    assert evidence.universe_membership(["NVDA", "NBIS"]) == "mixed"
    assert evidence.universe_membership([]) is None
    # membership never changes applicability
    assert applies(block("v", symbols=["NBIS"])) == {"V1", "V2"}


def test_unknown_preset():
    with pytest.raises(ValueError):
        evidence.effective_config("xx")


def test_weight_params_feed_rules():
    cfg = evidence.effective_config("f")
    assert evidence.weight_params(cfg) == {**rules.PRESETS["f"], "sig_n": 20, "med_min": 250, "sma_n": 200}


_EM = 'C_dagger (exposure-matched buy-and-hold: same realized average exposure, static)'
_SH = 'P(F) segment shuffle (same segment structure, random timing)'
_EXPECTED_FINDINGS = [
    ("V1", "Sharpe improvement vs exposure-matched buy-and-hold", _EM, "not established",
     "+0.044 late / +0.063 early, late period below the 0.05 minimum effect; Holm p = 0.002 "
     "but graded 'not estimable'"),
    ("V2", "max drawdown lower than exposure-matched buy-and-hold", _EM, "passed",
     "+2.09pp late / +5.92pp early, same sign in both periods, Holm p = 0.002"),
    ("F1", "Calmar improvement vs randomly-timed half-position segments", _SH, "not established",
     "+0.028 late / -0.007 early, Holm p = 0.742"),
    ("F2", "max drawdown lower than randomly-timed half-position segments", _SH, "passed",
     "+5.72pp late / +3.27pp early, Holm p = 0.002"),
    ("VF1", "Sharpe improvement vs exposure-matched buy-and-hold", _EM, "not established",
     "+0.059 late / -0.007 early, Holm p = 0.491"),
    (None, "drawdown reduction", None, "never tested",
     "the backtest has no drawdown cell for the combined VF rule"),
]


def test_finding_literals_frozen():
    got = [(f["cell"], f["claim"], f["control"], f["result"], f["detail"]) for f in block()["component_findings"]]
    assert got == _EXPECTED_FINDINGS
