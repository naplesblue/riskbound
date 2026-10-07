"""data.py: merge / degradation / settled-bar guard / budget. All fetches are injected; no network."""

import datetime

import numpy as np
import pandas as pd
import pytest

from riskbound import data, rules

ET = data.ET
TODAY = datetime.date(2026, 10, 6)  # a Tuesday
AFTER_CLOSE = datetime.datetime(2026, 10, 6, 17, 0, tzinfo=ET)


def bars(dates, seed=0, closes=None):
    idx = pd.DatetimeIndex(pd.to_datetime(list(dates)))
    rng = np.random.default_rng(seed)
    c = np.asarray(closes, dtype=float) if closes is not None else 100 * np.exp(np.cumsum(0.02 * rng.standard_normal(len(idx))))
    return pd.DataFrame({"O": c * 0.999, "H": c * 1.01, "L": c * 0.99, "C": c, "V": rng.integers(1e6, 1e7, len(idx)).astype(float)},
                        index=idx)


def bdays(start, end):
    return pd.bdate_range(start, end)


class Fetch:
    def __init__(self, *frames):
        self.frames = list(frames)
        self.calls = []

    def __call__(self, sym, start, end):
        self.calls.append((sym, start, end))
        return self.frames.pop(0)


def test_first_fetch_writes_and_round_trips(tmp_path):
    full = bars(bdays(rules.ANCHOR, TODAY), seed=1)
    full["C"] = full["C"] / 3.0  # values with long decimal expansions
    f = Fetch(full.copy())
    out, note, degraded = data.update("AAA", TODAY, tmp_path, fetch=f, now_et=AFTER_CLOSE)
    assert f.calls == [("AAA", rules.ANCHOR, "2026-10-07")]
    assert note == "initial full fetch" and degraded is False
    assert (tmp_path / "AAA.csv").exists()
    back = data.read_cache("AAA", tmp_path)
    for k in data.COLS:
        assert np.array_equal(back[k].to_numpy(dtype=float), full[k].to_numpy(dtype=float)), k
    assert back.index.equals(full.index)
    assert out.equals(back)


def test_incremental_merge_replaces_returned_rows_and_appends(tmp_path):
    old = bars(bdays("2026-07-01", "2026-10-01"), seed=2)
    data._write("AAA", old, tmp_path)
    old = data.read_cache("AAA", tmp_path)
    since = old.index[-1] - pd.Timedelta(days=data.OVERLAP_DAYS)
    resp = old[old.index >= since].copy()
    resp["H"] = resp["H"] * 1.001                       # refreshed final highs on returned dates
    new_rows = bars(bdays("2026-10-02", TODAY), seed=3)
    resp = pd.concat([resp, new_rows])
    f = Fetch(resp)
    out, note, degraded = data.update("AAA", TODAY, tmp_path, fetch=f, now_et=AFTER_CLOSE)
    assert f.calls == [("AAA", since.date().isoformat(), "2026-10-07")]
    assert degraded is False and note.startswith(f"appended {len(new_rows)} row(s)")
    assert out.index.equals(old.index.append(new_rows.index))
    kept = old.index[old.index < since]
    pd.testing.assert_frame_equal(out.loc[kept], old.loc[kept])   # untouched
    assert np.array_equal(out.loc[resp.index, "H"].to_numpy(), resp["H"].to_numpy())       # replaced
    pd.testing.assert_frame_equal(data.read_cache("AAA", tmp_path), out)


def test_response_missing_cached_date_is_degraded_and_keeps_old_row(tmp_path):
    old = data._write("AAA", bars(bdays("2026-07-01", "2026-10-01"), seed=4), tmp_path)
    since = old.index[-1] - pd.Timedelta(days=data.OVERLAP_DAYS)
    hole = old.index[-5]
    resp = old[(old.index >= since) & (old.index != hole)].copy()
    resp = pd.concat([resp, bars(bdays("2026-10-02", "2026-10-05"), seed=5)])
    out, note, degraded = data.update("AAA", TODAY, tmp_path, fetch=Fetch(resp), now_et=AFTER_CLOSE)
    assert degraded is True and "missing 1 cached trading day(s)" in note and str(hole.date()) in note
    pd.testing.assert_series_equal(out.loc[hole], old.loc[hole])
    assert hole in data.read_cache("AAA", tmp_path).index


def test_no_new_data(tmp_path):
    old = data._write("AAA", bars(bdays("2026-07-01", "2026-10-01"), seed=6), tmp_path)
    since = old.index[-1] - pd.Timedelta(days=data.OVERLAP_DAYS)
    out, note, degraded = data.update("AAA", TODAY, tmp_path, fetch=Fetch(old[old.index >= since].copy()),
                                      now_et=AFTER_CLOSE)
    assert (note, degraded) == ("no new data", False) and out.equals(old)


def test_overlap_deviation_triggers_full_refetch(tmp_path):
    old = data._write("AAA", bars(bdays("2026-07-01", "2026-10-01"), seed=7), tmp_path)
    since = old.index[-1] - pd.Timedelta(days=data.OVERLAP_DAYS)
    resp = old[old.index >= since].copy()
    resp.iloc[3, resp.columns.get_loc("C")] *= 1 + 2e-9          # just above REL_TOL
    full = bars(bdays(rules.ANCHOR, TODAY), seed=8)
    f = Fetch(resp, full.copy())
    out, note, degraded = data.update("AAA", TODAY, tmp_path, fetch=f, now_et=AFTER_CLOSE)
    assert [c[1] for c in f.calls] == [since.date().isoformat(), rules.ANCHOR]
    assert note == "adjusted prices changed in overlap, full refetch" and degraded is False
    assert np.array_equal(out["C"].to_numpy(), full["C"].to_numpy())


def test_overlap_within_tolerance_merges(tmp_path):
    old = data._write("AAA", bars(bdays("2026-07-01", "2026-10-01"), seed=9), tmp_path)
    since = old.index[-1] - pd.Timedelta(days=data.OVERLAP_DAYS)
    resp = old[old.index >= since].copy()
    resp.iloc[3, resp.columns.get_loc("C")] *= 1 + 5e-10
    f = Fetch(pd.concat([resp, bars(["2026-10-02"], seed=10)]))
    out, note, degraded = data.update("AAA", TODAY, tmp_path, fetch=f, now_et=AFTER_CLOSE)
    assert len(f.calls) == 1 and note.startswith("appended 1 row(s)")


def test_incomplete_full_refetch_keeps_cache(tmp_path):
    old = data._write("AAA", bars(bdays("2026-07-01", "2026-10-01"), seed=11), tmp_path)
    since = old.index[-1] - pd.Timedelta(days=data.OVERLAP_DAYS)
    resp = old[old.index >= since].copy()
    resp["C"] *= 0.98
    full = bars(bdays(rules.ANCHOR, TODAY), seed=12)
    full = full.drop(old.index[10])
    out, note, degraded = data.update("AAA", TODAY, tmp_path, fetch=Fetch(resp, full), now_et=AFTER_CLOSE)
    assert degraded is True and "incomplete (missing 1 cached trading day(s))" in note
    assert out.equals(old) and data.read_cache("AAA", tmp_path).equals(old)


def test_fetch_failures(tmp_path):
    out, note, degraded = data.update("AAA", TODAY, tmp_path, fetch=Fetch(pd.DataFrame()), now_et=AFTER_CLOSE)
    assert (out, note, degraded) == (None, "initial full fetch failed", True)
    old = data._write("AAA", bars(bdays("2026-07-01", "2026-10-01"), seed=13), tmp_path)
    out, note, degraded = data.update("AAA", TODAY, tmp_path, fetch=Fetch(None), now_et=AFTER_CLOSE)
    assert degraded is True and note == "incremental fetch failed, using cache (through 2026-10-01)"
    assert out.equals(old)


@pytest.mark.parametrize("now,kept", [
    (datetime.datetime(2026, 10, 6, 10, 0, tzinfo=ET), False),   # in session: today's bar dropped
    (datetime.datetime(2026, 10, 6, 16, 4, tzinfo=ET), False),   # before the 16:05 buffer
    (datetime.datetime(2026, 10, 6, 16, 5, tzinfo=ET), True),
    (datetime.datetime(2026, 10, 7, 9, 0, tzinfo=ET), True),     # bar is from a previous day
])
def test_in_progress_last_bar(tmp_path, now, kept):
    full = bars(bdays(rules.ANCHOR, TODAY), seed=14)
    out, _, _ = data.update("AAA", TODAY, tmp_path, fetch=Fetch(full.copy()), now_et=now)
    assert (out.index[-1] == pd.Timestamp(TODAY)) is kept
    assert len(out) == len(full) - (0 if kept else 1)


def test_drop_in_progress_bar_plain():
    now = datetime.datetime(2026, 10, 6, 12, 0, tzinfo=ET)
    b = [{"date": "2026-10-05"}, {"date": "2026-10-06"}]
    assert data.drop_in_progress_bar(b, now) == b[:1]
    assert data.drop_in_progress_bar(b[:1], now) == b[:1]
    assert data.drop_in_progress_bar([], now) == []
    assert data.SESSION_CLOSE_BUFFER == datetime.time(16, 5)
    assert str(data.ET) == "America/New_York"


def test_settled_cuts_before_anchor_and_in_progress():
    d = bars(bdays("2019-05-01", TODAY), seed=15)
    s = data.settled(d, datetime.datetime(2026, 10, 6, 11, 0, tzinfo=ET))
    assert s.index[0] >= pd.Timestamp(rules.ANCHOR) and s.index[-1] == pd.Timestamp("2026-10-05")


class Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def test_budget_exhausted_before_request():
    def ticker(sym):
        raise AssertionError("must not request")
    with pytest.raises(data.BudgetExceeded):
        data._download("AAA", "2026-01-01", "2026-02-01", deadline=0.5, clock=Clock(), ticker=ticker)


def test_budget_cannot_cover_backoff():
    class T:
        def history(self, **kw):
            raise RuntimeError("rate limited")
    slept = []
    with pytest.raises(data.BudgetExceeded):
        data._download("AAA", "2026-01-01", "2026-02-01", deadline=3.5, clock=Clock(), sleep=slept.append,
                       ticker=lambda s: T())
    assert slept == []


def test_retry_with_backoff_then_success():
    raw = bars(bdays("2026-09-01", "2026-09-10"), seed=16).rename(
        columns={"O": "Open", "H": "High", "L": "Low", "C": "Close", "V": "Volume"})
    raw.index = raw.index.tz_localize("America/New_York")
    raw["Dividends"] = 0.0
    seen = []

    class T:
        def history(self, **kw):
            seen.append(kw)
            if len(seen) == 1:
                raise RuntimeError("transient")
            return raw
    slept = []
    df = data._download("AAA", "2026-09-01", "2026-09-11", deadline=100.0, clock=Clock(), sleep=slept.append,
                        ticker=lambda s: T())
    assert slept == [3] and len(seen) == 2
    assert seen[0] == {"start": "2026-09-01", "end": "2026-09-11", "interval": "1d", "auto_adjust": True,
                       "actions": True, "timeout": 20}
    assert list(df.columns) == data.COLS and df.index.tz is None
    assert df.index[0] == pd.Timestamp("2026-09-01")


def test_budget_checked_before_adjustment_refetch(tmp_path):
    old = data._write("AAA", bars(bdays("2026-07-01", "2026-10-01"), seed=17), tmp_path)
    since = old.index[-1] - pd.Timedelta(days=data.OVERLAP_DAYS)
    resp = old[old.index >= since].copy()
    resp["C"] *= 0.98
    f = Fetch(resp, bars(bdays(rules.ANCHOR, TODAY)))
    with pytest.raises(data.BudgetExceeded):
        data.update("AAA", TODAY, tmp_path, fetch=f, now_et=AFTER_CLOSE, deadline=0.5, clock=Clock())
    assert len(f.calls) == 1


def test_default_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setenv(data.CACHE_ENV, str(tmp_path / "x"))
    assert data.default_cache_dir() == tmp_path / "x"
    assert data.cache_path("AAA") == tmp_path / "x" / "AAA.csv"
    monkeypatch.delenv(data.CACHE_ENV)
    assert data.default_cache_dir().parts[-3:] == (".cache", "riskbound", "bars")
