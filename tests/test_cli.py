"""CLI end to end on synthetic cached bars (--offline; no network)."""

import json

import numpy as np
import pandas as pd
import pytest

from riskbound import cli, compute, data, evidence, rules

TODAY = "2026-10-06"


def _bars(n, seed):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(0.02 * rng.standard_normal(n)))
    idx = pd.bdate_range("2019-06-03", periods=n)
    return pd.DataFrame({"O": c, "H": c * 1.01, "L": c * 0.99, "C": c, "V": np.full(n, 1e6)}, index=idx)


@pytest.fixture
def cache(tmp_path):
    for sym, seed in (("NVDA", 1), ("AAA", 2), ("ZZZT", 3)):
        data._write(sym, _bars(650, seed), tmp_path)
    return tmp_path


def run(capsys, *argv):
    code = cli.main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def run_json(capsys, cache, *argv):
    code, out, err = run(capsys, *argv, "--offline", "--cache-dir", str(cache), "--today", TODAY, "--format", "json")
    return code, json.loads(out), err


def applies(ev):
    return {(f["cell"] or f"{f['component']}-drawdown") for f in ev["component_findings"]
            if f["applies_to_current_config"]}


def test_json_default(capsys, cache):
    code, doc, err = run_json(capsys, cache, "NVDA", "AAA", "ZZZT")
    assert code == 0 and err == ""
    assert list(doc) == ["generated_at", "today", "rule_version", "tickers", "evidence"]
    assert doc["today"] == TODAY and doc["rule_version"] == "0.1.0.dev0"
    t = {r["symbol"]: r for r in doc["tickers"]}
    assert list(t) == ["NVDA", "AAA", "ZZZT"]
    assert t["NVDA"]["universe_membership"] == "in_backtest_universe"
    assert t["ZZZT"]["universe_membership"] == "not_backtested"
    assert all(r["error"] is None and r["stale"] is False for r in doc["tickers"])
    ev = doc["evidence"]
    assert ev["universe_membership"] == "mixed"
    assert ev["config_status"] == "untested_parameters" and applies(ev) == set() and ev["deviations"]
    assert ev["data_asof"] == t["NVDA"]["date"]
    expect = compute.compute_symbol("NVDA", data.settled(data.read_cache("NVDA", cache)), rules.PRESETS["vf75"])
    assert {k: t["NVDA"][k] for k in expect} == expect


def test_single_symbol_membership(capsys, cache):
    _, doc, _ = run_json(capsys, cache, "ZZZT")
    assert doc["evidence"]["universe_membership"] == "not_backtested"
    _, doc, _ = run_json(capsys, cache, "nvda")
    assert doc["tickers"][0]["symbol"] == "NVDA"
    assert doc["evidence"]["universe_membership"] == "in_backtest_universe"


@pytest.mark.parametrize("args,status,cells,dev", [
    (["--preset", "v"], "tested_drawdown_only", {"V1", "V2"}, []),
    (["--preset", "f"], "tested_drawdown_only", {"F1", "F2"}, []),
    (["--preset", "vf"], "components_tested_separately", {"VF1", "VF-drawdown"}, []),
    (["--preset", "v", "--vol-floor", "0.75"], "untested_parameters", set(), ["vol_floor=0.75"]),
    (["--preset", "v", "--anchor", "2018-01-01"], "untested_parameters", set(), ["anchor=2018-01-01"]),
    (["--preset", "v", "--vol-floor", "none"], "tested_drawdown_only", {"V1", "V2"}, []),
])
def test_config_matrix(capsys, cache, args, status, cells, dev):
    _, doc, _ = run_json(capsys, cache, "NVDA", *args)
    ev = doc["evidence"]
    assert (ev["config_status"], applies(ev), ev["deviations"]) == (status, cells, dev)
    code, out, _ = run(capsys, "evidence", "--format", "json", *args)
    assert code == 0 and json.loads(out)["evidence"]["config_status"] == status


def test_weights_follow_effective_config(capsys, cache):
    _, doc, _ = run_json(capsys, cache, "NVDA", "--preset", "f", "--below-sma", "0.3")
    d = data.settled(data.read_cache("NVDA", cache))
    w = rules.weights(d["C"].to_numpy(dtype=float), vol=False, vol_floor=None, below_sma=0.3)
    assert doc["tickers"][0]["w"] == w["w"][-1]


def test_evidence_subcommand_json(capsys):
    code, out, _ = run(capsys, "evidence", "--format", "json")
    doc = json.loads(out)
    assert code == 0 and list(doc) == ["evidence"]
    ev = doc["evidence"]
    assert ev["config_status"] == "untested_parameters" and ev["deviations"]
    assert all(not f["applies_to_current_config"] for f in ev["component_findings"])
    assert ev["universe_membership"] is None and ev["data_asof"] is None and ev["forward_track_since"] is None


@pytest.mark.parametrize("fmt,head", [("table", "EVIDENCE"), ("markdown", "### Evidence")])
def test_text_formats_carry_evidence(capsys, cache, fmt, head):
    code, out, _ = run(capsys, "NVDA", "ZZZT", "--offline", "--cache-dir", str(cache), "--today", TODAY,
                       "--format", fmt)
    assert code == 0 and head in out
    for s in ("config_status: untested_parameters", "[n/a] V2 V:", "disclaimer: Historical association only",
              "universe_membership: mixed", "NVDA", "not_backtested", "vol=true, vol_floor=0.75"):
        assert s in out, s
    if fmt == "markdown":
        assert "| symbol | date |" in out
    code, out, _ = run(capsys, "evidence", "--format", fmt, "--preset", "v")
    assert code == 0 and head in out and "[applies] V2 V:" in out and "universe_membership: null" in out


def test_exit_codes(capsys, cache, tmp_path):
    empty = tmp_path / "empty"
    code, out, err = run(capsys, "AAA", "BBB", "--offline", "--cache-dir", str(empty), "--format", "json")
    assert code == 1 and all(r["error"] for r in json.loads(out)["tickers"])
    code, out, err = run(capsys, "NVDA", "MISSING", "--offline", "--cache-dir", str(cache), "--today", TODAY,
                         "--format", "json")
    assert code == 0 and err.startswith("warning:") and "MISSING" in err and len(err.strip().splitlines()) == 1
    rows = {r["symbol"]: r for r in json.loads(out)["tickers"]}
    assert rows["MISSING"]["error"] == "no cached bars (offline)" and rows["MISSING"]["w"] is None
    for bad in (["--vol-floor", "1.5"], ["--vol-floor", "x"], ["--below-sma", "0"], ["--anchor", "2019-13-01"],
                ["--preset", "zz"], ["--format", "yaml"]):
        with pytest.raises(SystemExit) as e:
            cli.main(["NVDA", *bad])
        assert e.value.code == 2, bad
        with pytest.raises(SystemExit) as e:
            cli.main(["evidence", *bad])
        assert e.value.code == 2, bad
    with pytest.raises(SystemExit) as e:
        cli.main([])
    assert e.value.code == 2


def test_online_path_uses_update(monkeypatch, capsys, tmp_path):
    calls = []
    frame = _bars(650, 4)

    def fake_update(sym, today, cache_dir=None, fetch=None, now_et=None, *, deadline=None, anchor=rules.ANCHOR, **kw):
        calls.append((sym, today.isoformat(), cache_dir, anchor, deadline is not None))
        if sym == "BAD":
            raise data.BudgetExceeded("out of time")
        return frame, "incremental fetch failed, using cache (through x)", True

    monkeypatch.setattr(data, "update", fake_update)
    code, out, err = run(capsys, "AAPL", "BAD", "LATE", "--cache-dir", str(tmp_path), "--today", TODAY,
                         "--format", "json")
    rows = {r["symbol"]: r for r in json.loads(out)["tickers"]}
    assert code == 0 and calls == [("AAPL", TODAY, str(tmp_path), "2019-06-01", True),
                                   ("BAD", TODAY, str(tmp_path), "2019-06-01", True)]
    assert rows["AAPL"]["stale"] is True and rows["AAPL"]["note"].startswith("incremental fetch failed")
    assert rows["BAD"]["error"].startswith("fetch budget exhausted")
    assert rows["LATE"]["error"].startswith("fetch budget exhausted")


def test_no_trading_words_in_output(capsys, cache):
    for fmt in ("table", "markdown", "json"):
        _, out, _ = run(capsys, "NVDA", "--offline", "--cache-dir", str(cache), "--today", TODAY, "--format", fmt)
        low = out.lower().replace("buy-and-hold", "")
        for w in ("signal", " buy", " sell", "target price"):
            assert w not in low, (fmt, w)


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "1e309", "0", "-5"])
def test_budget_rejects_non_finite_or_non_positive(monkeypatch, capsys, cache, bad):
    def boom(*a, **k):
        raise AssertionError("cache read or fetch must not happen")

    monkeypatch.setattr(data, "update", boom)
    monkeypatch.setattr(data, "read_cache", boom)
    with pytest.raises(SystemExit) as e:
        cli.main(["NVDA", "--budget", bad, "--cache-dir", str(cache), "--today", TODAY])
    assert e.value.code == 2
    assert "budget" in capsys.readouterr().err
