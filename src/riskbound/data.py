"""Daily bar cache `{cache_dir}/{SYM}.csv`, using the same conventions as the backtest
(auto_adjust=True, round-trip float parsing).

- First run: fetch the full history from the anchor (2019-06-01), write it, then **re-read it with round-trip parsing**.
- Incremental: fetch roughly the last 30 calendar days and compare the overlapping closes one by one. Any
  relative deviation > 1e-9 (dividend / split changing the adjustment factor) triggers a full refetch from the
  anchor. Otherwise **merge by date**: dates present in the response are replaced with the new OHLCV (refreshing
  final H/L/V); settled old rows that are not returned are kept as is. A response shorter than the cache or with
  missing days in the middle is recorded as degraded (visible in the note) and never causes old rows to be
  deleted. A full refetch that misses dates already cached is likewise treated as incomplete; the cache is kept.
- **The cache only stores settled bars**: before writing, an in-progress last bar is dropped
  (`drop_in_progress_bar`), so the cache never holds a provisional bar. When a fetch fails and the cache is
  reused, its last bar is already a settled close; an intraday price is never promoted to a close.
- **Fetch budget**: the caller passes an absolute deadline (`time.monotonic` clock). Each request uses
  timeout = min(20s, remaining budget); when the remaining budget cannot cover the next request or backoff,
  fetching stops and `BudgetExceeded` is raised. The same check runs before an adjustment-driven full refetch.
"""

from __future__ import annotations

import datetime
import logging
import os
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from . import rules

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
SESSION_CLOSE_BUFFER = datetime.time(16, 5)  # US close 16:00 plus 5 minutes for bars to settle

CACHE_ENV = "RISKBOUND_CACHE_DIR"
OVERLAP_DAYS = 30
REL_TOL = 1e-9
FETCH_TIMEOUT = 20                     # timeout of a single yfinance request (seconds)
COLS = ["O", "H", "L", "C", "V"]
MIN_REQUEST_S = 1.0                    # do not send another request with less budget than this


class BudgetExceeded(TimeoutError):
    """Fetch budget exhausted: this run is incomplete and the caller must not commit any state."""


def drop_in_progress_bar(bars: list[dict], now_et: datetime.datetime | None = None) -> list[dict]:
    """When run during the US session, the last yfinance daily bar is the unfinished current day -> drop it.

    Trims only when "last bar date == today in US/Eastern and Eastern time is before the close buffer".
    """
    if not bars:
        return bars
    now = now_et if now_et is not None else datetime.datetime.now(ET)
    if bars[-1]["date"] == now.date().isoformat() and now.time() < SESSION_CLOSE_BUFFER:
        return bars[:-1]
    return bars


def default_cache_dir() -> Path:
    """`$RISKBOUND_CACHE_DIR` if set, else `~/.cache/riskbound/bars`."""
    env = os.environ.get(CACHE_ENV)
    if env:
        return Path(env).expanduser()
    return Path.home() / ".cache" / "riskbound" / "bars"


def _remaining(deadline: float | None, clock) -> float:
    return float("inf") if deadline is None else deadline - clock()


def cache_path(sym: str, cache_dir: Path | str | None = None) -> Path:
    return Path(cache_dir or default_cache_dir()) / f"{sym}.csv"


def read_cache(sym: str, cache_dir: Path | str | None = None) -> pd.DataFrame | None:
    f = cache_path(sym, cache_dir)
    if not f.exists():
        return None
    df = pd.read_csv(f, index_col=0, parse_dates=True, float_precision="round_trip")
    if len(df) == 0:
        return None
    return df[COLS].dropna()


def _yf_ticker(sym: str):
    import yfinance as yf
    return yf.Ticker(sym)


def _download(sym: str, start: str, end: str, retries: int = 3, *, deadline: float | None = None,
              clock=time.monotonic, sleep=time.sleep, ticker=_yf_ticker) -> pd.DataFrame:
    """yfinance daily bars with the backtest's column handling. `end` is exclusive.

    Each request uses timeout = min(FETCH_TIMEOUT, remaining budget); raises BudgetExceeded when the remaining
    budget cannot cover another request or the backoff before it. `ticker` is injectable for tests.
    """
    df = pd.DataFrame()
    for attempt in range(retries):
        left = _remaining(deadline, clock)
        if left < MIN_REQUEST_S:
            raise BudgetExceeded(f"{sym}: remaining budget {max(left, 0):.1f}s, no further requests")
        try:
            df = ticker(sym).history(start=start, end=end, interval="1d", auto_adjust=True, actions=True,
                                     timeout=min(FETCH_TIMEOUT, left))
        except Exception as e:                       # rate limit / transient network error -> back off and retry
            log.warning("%s: fetch failed (%d/%d): %s", sym, attempt + 1, retries, str(e)[:80])
            df = pd.DataFrame()
        if not df.empty:
            break
        if attempt == retries - 1:
            break
        backoff = 3 * (attempt + 1)
        if _remaining(deadline, clock) < backoff + MIN_REQUEST_S:
            raise BudgetExceeded(f"{sym}: remaining budget cannot cover a {backoff}s backoff and retry")
        sleep(backoff)
    if df.empty:
        return df
    df = df.rename(columns={"Open": "O", "High": "H", "Low": "L", "Close": "C", "Volume": "V"})
    df = df[COLS].copy()
    df.index = pd.to_datetime(df.index.date)
    return df.dropna()


def _write(sym: str, df: pd.DataFrame, cache_dir: Path | str | None) -> pd.DataFrame:
    f = cache_path(sym, cache_dir)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f".{f.name}.tmp")
    df.to_csv(tmp)
    tmp.replace(f)
    out = read_cache(sym, cache_dir)
    if out is None:
        raise RuntimeError(f"{sym}: cache is empty after write")
    return out


def overlap_ok(old: pd.DataFrame, new: pd.DataFrame) -> bool:
    common = old.index.intersection(new.index)
    if len(common) == 0:
        return False
    a, b = old.loc[common, "C"].astype(float), new.loc[common, "C"].astype(float)
    return bool((((a - b).abs() / a.abs()) <= REL_TOL).all())


def _drop_provisional(df: pd.DataFrame, now_et: datetime.datetime | None) -> pd.DataFrame:
    if df.empty:
        return df
    kept = drop_in_progress_bar([{"date": str(df.index[-1].date())}], now_et)
    return df if kept else df.iloc[:-1]


def merge_by_date(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Rows for returned dates are replaced by the new ones; old rows not returned are kept; sorted by date."""
    return pd.concat([old[~old.index.isin(new.index)], new]).sort_index()


def missing_cached(old: pd.DataFrame, new: pd.DataFrame, since: pd.Timestamp) -> pd.DatetimeIndex:
    """Settled cached dates >= since that the response did not return (short response / gaps)."""
    span = old.index[old.index >= since]
    return span[~span.isin(new.index)]


def update(sym: str, today: datetime.date, cache_dir: Path | str | None = None, fetch=None,
           now_et: datetime.datetime | None = None, *, deadline: float | None = None,
           clock=time.monotonic, sleep=time.sleep, anchor: str = rules.ANCHOR
           ) -> tuple[pd.DataFrame | None, str, bool]:
    """Update the cache; return (settled daily bars re-read with round-trip parsing, note, degraded).

    `fetch(sym, start, end)` is injectable (tests). Raises BudgetExceeded (never swallowed) when the fetch
    budget runs out, so the caller can mark the run as incomplete.
    """
    if fetch is None:
        def fetch(s, a, b):
            return _download(s, a, b, deadline=deadline, clock=clock, sleep=sleep)
    end = (today + datetime.timedelta(days=1)).isoformat()
    old = read_cache(sym, cache_dir)
    if old is not None:
        since = old.index[-1] - pd.Timedelta(days=OVERLAP_DAYS)
        new = fetch(sym, since.date().isoformat(), end)
        new = None if new is None else _drop_provisional(new, now_et)
        if new is None or new.empty:
            return old, f"incremental fetch failed, using cache (through {old.index[-1].date()})", True
        if overlap_ok(old, new):
            gap = missing_cached(old, new, since)
            merged = merge_by_date(old, new)
            n_add = int((new.index > old.index[-1]).sum())
            gap_note = (f"; response missing {len(gap)} cached trading day(s) ({gap[0].date()} ...), old rows kept"
                        if len(gap) else "")
            if merged.equals(old):
                return old, "no new data" + gap_note, bool(len(gap))
            return (_write(sym, merged, cache_dir), f"appended {n_add} row(s) (OHLCV of returned dates refreshed)"
                    f"{gap_note}", bool(len(gap)))
        why = "adjusted prices changed in overlap, full refetch"
        if _remaining(deadline, clock) < MIN_REQUEST_S:
            raise BudgetExceeded(f"{sym}: budget exhausted before adjustment-driven full refetch")
    else:
        why = "initial full fetch"
    full = fetch(sym, anchor, end)
    full = None if full is None else _drop_provisional(full, now_et)
    if full is None or full.empty:
        if old is None:
            return None, f"{why} failed", True
        return old, f"{why} failed, using cache (through {old.index[-1].date()})", True
    if old is not None:
        gap = missing_cached(old, full, pd.Timestamp(anchor))
        if len(gap):                                  # incomplete full refetch: never overwrite the settled cache
            return old, (f"{why} incomplete (missing {len(gap)} cached trading day(s)), using cache "
                         f"(through {old.index[-1].date()})"), True
    return _write(sym, full, cache_dir), why, False


def settled(df: pd.DataFrame, now_et: datetime.datetime | None = None, anchor: str = rules.ANCHOR) -> pd.DataFrame:
    """Drop rows before the anchor and apply the in-progress guard again (the cache already holds only settled
    bars; this is a second safeguard)."""
    return _drop_provisional(df[df.index >= pd.Timestamp(anchor)], now_et)
