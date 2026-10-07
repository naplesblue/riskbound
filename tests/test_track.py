"""Forward log: append (cases 5a-5e), verify (5f-5g), publication states (5h), hash chain, deadline, canonical form.

No network: bars come from synthetic caches (offline) or an injected fetch; git cases use a temporary repository
with a bare repository acting as the public remote.
"""

import datetime
import json
import shutil
import subprocess

import numpy as np
import pandas as pd
import pytest

from riskbound import cli, data, track
from riskbound import evidence

U = list(evidence.BACKTEST_UNIVERSE)
AFTER_CLOSE = datetime.datetime(2026, 10, 7, 20, 0, tzinfo=data.ET)
GEN = "2026-10-06T23:00:00+00:00"


def _frame(sym, end="2026-10-06", n=320):
    rng = np.random.default_rng(U.index(sym))
    idx = pd.bdate_range(end=end, periods=n)
    c = 100 * np.exp(np.cumsum(0.015 * rng.standard_normal(n)))
    return pd.DataFrame({"O": c, "H": c * 1.01, "L": c * 0.99, "C": c, "V": np.full(n, 1e6)}, index=idx)


FULL = {s: _frame(s) for s in U}


def write_cache(cache, through, syms=U):
    for s in syms:
        data._write(s, FULL[s][FULL[s].index <= through], cache)


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def plan(path, cache, today="2026-10-06", **kw):
    return track.plan_append(path, today=datetime.date.fromisoformat(today), cache_dir=cache, offline=True,
                             now_et=AFTER_CLOSE, generated_at=GEN, **kw)


def do_append(path, cache, today="2026-10-06", **kw):
    p = plan(path, cache, today, **kw)
    if p["rows"]:
        track.write_appended(path, p["lines"], p["rows"])
    return p


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    cache = tmp_path / "cache"
    return tmp_path / "track" / "daily.jsonl", cache


# ---------------------------------------------------------------- canonical form / hash chain / deadline
def test_canonical_is_stable_and_sorted():
    row = {"b": 1.0000000000000002, "a": None, "c": [True, "x"], "d": {"z": 1, "y": 0.1}}
    assert track.canonical(row) == track.canonical(dict(reversed(list(row.items()))))
    assert track.canonical(row) == '{"a":null,"b":1.0000000000000002,"c":[true,"x"],"d":{"y":0.1,"z":1}}'
    assert track.canonical(row).encode() == track.canonical(json.loads(json.dumps(row))).encode()
    with pytest.raises(ValueError):
        track.canonical({"x": float("nan")})
    assert track.canonical({"s": "é"}) == '{"s":"\\u00e9"}'


def test_seal_chain_and_row_hash():
    r1 = track.seal({"asof": "x"}, track.ZERO_HASH)
    r2 = track.seal({"asof": "y"}, r1["row_hash"])
    assert list(r1)[-2:] == ["prev_hash", "row_hash"] and r1["prev_hash"] == "0" * 64
    assert r2["prev_hash"] == r1["row_hash"] and track.row_hash(r2) == r2["row_hash"]
    import hashlib
    assert r1["row_hash"] == hashlib.sha256(track.canonical({"asof": "x", "prev_hash": "0" * 64}).encode()).hexdigest()


@pytest.mark.parametrize("asof,expect", [
    ("2026-10-02", "2026-10-05T13:30:00+00:00"),       # Friday -> Monday 09:30 EDT
    ("2026-12-18", "2026-12-21T14:30:00+00:00"),       # Friday -> Monday 09:30 EST
    ("2026-10-06", "2026-10-07T13:30:00+00:00"),       # Tuesday -> Wednesday
])
def test_deadline_examples(asof, expect):
    assert track.deadline(asof).isoformat() == expect


def test_runner_env(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert track.runner() == "github-actions"
    monkeypatch.setenv("GITHUB_ACTIONS", "false")
    assert track.runner() == "local"


# ---------------------------------------------------------------- 5a: all fresh -> 25 rows, idempotent
def test_5a_all_fresh_then_idempotent(env):
    path, cache = env
    write_cache(cache, "2026-10-05")
    p = do_append(path, cache)
    assert p["asof"] == "2026-10-05" and len(p["rows"]) == 25 and not p["stale"]
    rows, err = track.verify_daily(path)
    assert err is None and len(rows) == 25 and track.coverage(rows) == {"2026-10-05": 25}
    r = rows[0]
    assert tuple(r) == track.DAILY_KEYS and r["bars_through"] == r["asof"] and r["runner"] == "local"
    assert r["params"] == {"preset": "vf75", "vol": True, "vol_floor": 0.75, "below_sma": 0.75,
                           "anchor": "2019-06-01", "sig_n": 20, "med_min": 250, "sma_n": 200}
    assert r["generated_at"] == GEN and isinstance(r["close"], float)
    before = path.read_bytes()
    p2 = do_append(path, cache)
    assert p2["rows"] == [] and len(p2["skipped"]) == 25 and path.read_bytes() == before


def test_5a_rows_match_compute(env):
    path, cache = env
    write_cache(cache, "2026-10-05")
    do_append(path, cache)
    from riskbound import compute
    rows, _ = track.verify_daily(path)
    d = data.settled(data.read_cache("NVDA", cache), AFTER_CLOSE)
    c = compute.compute_symbol("NVDA", d, evidence.weight_params(evidence.effective_config()))
    r = next(x for x in rows if x["symbol"] == "NVDA")
    assert all(r[k] == c[k] for k in ("close", "w", "w_vol", "above_sma200", "sma200", "sigma", "sigma_tgt",
                                      "vol_ratio"))


# ---------------------------------------------------------------- 5b: 3 symbols one day behind -> 22, then +3
def test_5b_lagging_symbols_then_backfill_same_asof(env):
    path, cache = env
    lag = U[:3]
    write_cache(cache, "2026-10-05", U[3:])
    write_cache(cache, "2026-10-02", lag)
    p = do_append(path, cache)
    assert len(p["rows"]) == 22 and set(p["stale"]) == set(lag)
    assert all(v[0] == "2026-10-02" for v in p["stale"].values())
    write_cache(cache, "2026-10-05", lag)
    p2 = do_append(path, cache)
    assert sorted(r["symbol"] for r in p2["rows"]) == sorted(lag)
    rows, err = track.verify_daily(path)
    assert err is None and len(rows) == 25 and track.coverage(rows) == {"2026-10-05": 25}


# ---------------------------------------------------------------- 5c: fetch fails, cache reused -> 22
def test_5c_fetch_failure_uses_old_cache_not_written(env, capsys):
    path, cache = env
    write_cache(cache, "2026-10-02")
    fail = set(U[-3:])

    def fetch(sym, start, end):
        if sym in fail:
            return None
        f = FULL[sym]
        return f[(f.index >= start) & (f.index < end)]

    p = track.plan_append(path, today=datetime.date(2026, 10, 5), cache_dir=cache, fetch=fetch, now_et=AFTER_CLOSE,
                          generated_at=GEN)
    assert p["asof"] == "2026-10-05" and len(p["rows"]) == 22 and set(p["stale"]) == fail
    assert all("using cache" in v[1] for v in p["stale"].values())
    track.write_appended(path, p["lines"], p["rows"])
    code, out, _ = run(capsys, "track", "verify", "--file", str(path), "--observations", str(path.with_name("o.jsonl")))
    assert code == 0 and "2026-10-05: 22/25" in out


# ---------------------------------------------------------------- 5d: everything stale -> 0 new rows, exit 0
def test_5d_all_stale_writes_nothing(env, capsys):
    path, cache = env
    write_cache(cache, "2026-10-02")
    do_append(path, cache, today="2026-10-02")
    before = path.read_bytes()
    code, out, err = run(capsys, "track", "append", "--file", str(path), "--cache-dir", str(cache), "--offline",
                         "--today", "2026-10-06")
    assert code == 0 and out.strip() == "no new rows for asof=2026-10-02" and path.read_bytes() == before


# ---------------------------------------------------------------- 5e: --asof only with --dry-run
def test_5e_asof_rejected_and_dry_run_writes_nothing(env, capsys):
    path, cache = env
    write_cache(cache, "2026-10-05")
    with pytest.raises(SystemExit) as e:
        cli.main(["track", "append", "--file", str(path), "--cache-dir", str(cache), "--offline",
                  "--asof", "2020-01-01"])
    assert e.value.code != 0 and not path.exists()
    code, out, _ = run(capsys, "track", "append", "--file", str(path), "--cache-dir", str(cache), "--offline",
                       "--today", "2026-10-06", "--dry-run", "--asof", "2026-10-02")
    assert code == 0 and not path.exists()
    lines = out.splitlines()
    assert lines[0].startswith("dry run: asof=2026-10-02 deadline=2026-10-05T13:30:00+00:00 rows=25")
    assert all(json.loads(x)["asof"] == "2026-10-02" for x in lines[1:]) and len(lines) == 26
    code, out, _ = run(capsys, "track", "append", "--file", str(path), "--cache-dir", str(cache), "--offline",
                       "--today", "2026-10-06", "--dry-run")
    assert code == 0 and "asof=2026-10-05" in out and not path.exists()


def test_calendar_guards(env, capsys):
    path, cache = env
    write_cache(cache, "2026-10-05")
    code, _, err = run(capsys, "track", "append", "--file", str(path), "--cache-dir", str(cache), "--offline",
                       "--today", "2026-10-04")                     # asof after "today"
    assert code == 1 and "after today" in err and not path.exists()
    weekend = {s: FULL[s] for s in U}
    for s in U:                                                      # a bar dated on a Saturday
        f = weekend[s][weekend[s].index <= "2026-10-02"]
        f = pd.concat([f, f.iloc[[-1]].set_axis(pd.DatetimeIndex(["2026-10-03"]))])
        data._write(s, f, cache)
    code, _, err = run(capsys, "track", "append", "--file", str(path), "--cache-dir", str(cache), "--offline",
                       "--today", "2026-10-06")
    assert code == 1 and "not a Monday-Friday" in err and not path.exists()


def test_no_data_at_all_is_refused(env, capsys):
    path, cache = env
    code, _, err = run(capsys, "track", "append", "--file", str(path), "--cache-dir", str(cache), "--offline",
                       "--today", "2026-10-06")
    assert code == 1 and "cannot determine asof" in err


def test_budget_exhausted_writes_nothing(env):
    path, cache = env

    def fetch(sym, start, end):
        raise data.BudgetExceeded("out of time")

    with pytest.raises(track.TrackError, match="budget"):
        track.plan_append(path, today=datetime.date(2026, 10, 6), cache_dir=cache, fetch=fetch, now_et=AFTER_CLOSE)
    assert not path.exists()


# ---------------------------------------------------------------- 5f: in-file verify
@pytest.fixture
def two_days(env):
    path, cache = env
    write_cache(cache, "2026-10-02")
    do_append(path, cache, today="2026-10-02")
    write_cache(cache, "2026-10-05")
    do_append(path, cache)
    return path, cache


def _verify(capsys, path, *extra):
    return run(capsys, "track", "verify", "--file", str(path), "--observations", str(path.with_name("obs.jsonl")),
               *extra)


def test_5f_verify_ok_and_tamper_detection(two_days, capsys, tmp_path):
    path, cache = two_days
    code, out, _ = _verify(capsys, path)
    assert code == 0 and out.startswith("50 rows, 2 asof dates") and "2026-10-02: 25/25" in out
    lines = path.read_text().splitlines(keepends=True)

    mod = tmp_path / "mod" / "daily.jsonl"
    mod.parent.mkdir()
    row = json.loads(lines[10])
    row["w"] = row["w"] + 0.01
    mod.write_text("".join(lines[:10]) + json.dumps(row, separators=(",", ":")) + "\n" + "".join(lines[11:]))
    code, out, _ = _verify(capsys, mod)
    assert code == 1 and "line 11: row_hash does not match" in out
    code, _, err = run(capsys, "track", "append", "--file", str(mod), "--cache-dir", str(cache), "--offline",
                       "--today", "2026-10-06")
    assert code == 1 and "refusing to append" in err

    dele = tmp_path / "del" / "daily.jsonl"
    dele.parent.mkdir()
    dele.write_text("".join(lines[:20] + lines[21:]))
    code, out, _ = _verify(capsys, dele)
    assert code == 1 and "line 21: prev_hash does not match" in out
    code, _, err = run(capsys, "track", "append", "--file", str(dele), "--cache-dir", str(cache), "--offline",
                       "--today", "2026-10-06")
    assert code == 1 and "refusing to append" in err


def test_verify_other_failures(tmp_path):
    def chain(*rows):
        out, prev = [], track.ZERO_HASH
        for r in rows:
            r = track.seal(r, prev)
            prev = r["row_hash"]
            out.append(track.dumps_line(r))
        return out
    base = {k: None for k in track.DAILY_KEYS if k not in ("prev_hash", "row_hash")}
    a = base | {"asof": "2026-10-05", "symbol": "AAPL", "bars_through": "2026-10-05"}
    assert track.verify_chain(chain(a, a), track.DAILY_KEYS, ("asof", "symbol"))[1].startswith("line 2: duplicate")
    b = a | {"bars_through": "2026-10-02"}
    assert "bars_through" in track.verify_chain(chain(b), track.DAILY_KEYS, ("asof", "symbol"))[1]
    reordered = {"symbol": "AAPL", **{k: v for k, v in a.items() if k != "symbol"}}
    assert "key order" in track.verify_chain(chain(reordered), track.DAILY_KEYS, ("asof", "symbol"))[1]
    ok = chain(a)
    assert "trailing newline" in track.verify_chain([ok[0].rstrip("\n")], track.DAILY_KEYS, ("asof", "symbol"))[1]


# ---------------------------------------------------------------- 5g: --prefix-of
def test_5g_tail_truncation_passes_in_file_but_fails_prefix(two_days, capsys, tmp_path):
    path, _ = two_days
    ref = tmp_path / "ref.jsonl"
    shutil.copy(path, ref)
    lines = path.read_text().splitlines(keepends=True)
    cut = tmp_path / "cut" / "daily.jsonl"
    cut.parent.mkdir()
    cut.write_text("".join(lines[:25]))                       # last asof (25 rows) removed from the tail
    code, out, _ = _verify(capsys, cut)
    assert code == 0 and "25 rows" in out                      # known boundary: in-file verify cannot see this
    code, out, _ = _verify(capsys, cut, "--prefix-of", str(ref))
    assert code == 1 and "is NOT an exact prefix" in out


def test_5g_normal_append_is_prefix_of_old_reference(env, capsys, tmp_path):
    path, cache = env
    write_cache(cache, "2026-10-02")
    do_append(path, cache, today="2026-10-02")
    ref = tmp_path / "old.jsonl"
    shutil.copy(path, ref)
    write_cache(cache, "2026-10-05")
    do_append(path, cache)
    code, out, _ = _verify(capsys, path, "--prefix-of", str(ref))
    assert code == 0 and "reference (25 rows) is an exact prefix" in out
    rewritten = tmp_path / "rw.jsonl"
    rewritten.write_text(path.read_text().replace('"runner":"local"', '"runner":"other"', 1))
    code, _, _ = _verify(capsys, path, "--prefix-of", str(rewritten))
    assert code == 1


# ---------------------------------------------------------------- 5h: publication states in a temporary repo
def git(repo, *args, env=None):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def commit(repo, msg, when):
    import os
    e = {**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when}
    git(repo, "add", "track")
    git(repo, "commit", "-q", "-m", msg, env=e)
    return git(repo, "rev-parse", "HEAD")


def test_5h_publication_states(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    repo, remote, cache = tmp_path / "repo", tmp_path / "public.git", tmp_path / "cache"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "remote", "add", "origin", str(remote))
    daily, obs = repo / "track" / "daily.jsonl", repo / "track" / "observations.jsonl"

    def add_day(asof):
        write_cache(cache, asof)
        p = do_append(daily, cache, today=asof)
        assert len(p["rows"]) == 25 and p["rows"][0]["runner"] == "github-actions"

    add_day("2026-10-01")                                   # deadline 2026-10-02T13:30Z
    c1 = commit(repo, "day 1", "2026-10-01T22:00:00+00:00")
    git(repo, "push", "-q", "origin", "main")
    add_day("2026-10-02")                                   # deadline 2026-10-05T13:30Z
    c2 = commit(repo, "day 2", "2026-10-02T22:00:00+00:00")  # committed before the deadline ...
    git(repo, "push", "-q", "origin", "main")               # ... but the push is observed after it
    add_day("2026-10-05")
    c3 = commit(repo, "day 3", "2026-10-05T22:00:00+00:00")
    git(repo, "push", "-q", "origin", "main")               # pushed, never observed
    add_day("2026-10-06")
    commit(repo, "day 4", "2026-10-06T22:00:00+00:00")       # committed early, never pushed
    git(repo, "fetch", "-q", "origin")

    events = [
        {"id": "9002", "type": "PushEvent", "created_at": "2026-10-06T01:00:00Z",
         "payload": {"head": c2.upper(), "commits": [{"sha": c2, "author": {"email": "x@example.com"},
                                                      "message": "day 2", "url": "https://example.invalid"}]}},
        {"id": "9001", "type": "PushEvent", "created_at": "2026-10-02T02:00:00Z",
         "payload": {"head": c1, "commits": [{"sha": c1, "message": "day 1"}]}},
        {"id": "9003", "type": "WatchEvent", "created_at": "2026-10-06T02:00:00Z", "payload": {}},
    ]
    ev_file = tmp_path / "events.json"
    ev_file.write_text(json.dumps(events))
    code, out, _ = run(capsys, "track", "observe", "--github", "someone/riskbound", "--events-file", str(ev_file),
                       "--observations", str(obs))
    assert code == 0 and "recorded 2 new" in out
    code, out, _ = run(capsys, "track", "observe", "--github", "someone/riskbound", "--events-file", str(ev_file),
                       "--observations", str(obs))
    assert code == 0 and "recorded 0 new" in out           # dedup by event_id
    orows, oerr = track.verify_observations(obs)
    assert oerr is None and [o["event_id"] for o in orows] == ["9001", "9002"]
    assert all(tuple(o) == track.OBS_KEYS for o in orows)
    assert orows[1]["head_sha"] == c2 and orows[1]["commits"] == [c2] and orows[1]["repo"] == "someone/riskbound"
    assert "example" not in obs.read_text() and "message" not in obs.read_text()

    code, out, _ = run(capsys, "track", "verify", "--file", str(daily), "--observations", str(obs),
                       "--public-ref", "origin/main", "--repo", str(repo), "--format", "json")
    assert code == 0
    rep = json.loads(out)
    by_asof = {}
    for r in rep["rows_status"]:
        by_asof.setdefault(r["asof"], set()).add(r["state"])
    assert by_asof == {"2026-10-01": {"forward"}, "2026-10-02": {"late"}, "2026-10-05": {"published_unverified"},
                       "2026-10-06": {"unpublished"}}
    assert rep["states"] == {"unpublished": 25, "published_unverified": 25, "late": 25, "forward": 25}
    late = next(r for r in rep["rows_status"] if r["asof"] == "2026-10-02")
    assert late["commit"] == c2 and late["committed_at"].startswith("2026-10-02T22:00:00")
    assert late["published_at"] == "2026-10-06T01:00:00+00:00" and late["deadline"] == "2026-10-05T13:30:00+00:00"
    fwd = next(r for r in rep["rows_status"] if r["asof"] == "2026-10-01")
    assert fwd["published_at"] == "2026-10-02T02:00:00+00:00" and fwd["commit"] == c1
    assert next(r for r in rep["rows_status"] if r["asof"] == "2026-10-05")["commit"] == c3

    # an observation whose head descends from c3 covers it through ancestry
    later = [{"id": "9004", "type": "PushEvent", "created_at": "2026-10-06T12:00:00Z",
              "payload": {"head": git(repo, "rev-parse", "HEAD"), "commits": []}}]
    track.observe(obs, later, "someone/riskbound", observed_at="2026-10-06T12:05:00+00:00")
    st = track.public_status(track.verify_daily(daily)[0], track.verify_observations(obs)[0], repo=repo,
                             relpath="track/daily.jsonl", ref="origin/main")
    assert {r["state"] for r in st if r["asof"] == "2026-10-05"} == {"forward"}      # 12:00Z < 13:30Z deadline
    assert {r["state"] for r in st if r["asof"] == "2026-10-06"} == {"unpublished"}  # still not on origin/main

    # uncommitted rows are unpublished; prefix against a git ref
    code, out, _ = run(capsys, "track", "verify", "--file", str(daily), "--observations", str(obs),
                       "--prefix-of", "origin/main:track/daily.jsonl", "--repo", str(repo))
    assert code == 0 and "reference (75 rows) is an exact prefix" in out
    code, out, _ = run(capsys, "track", "verify", "--file", str(daily), "--observations", str(obs),
                       "--prefix-of", "nosuchref:track/daily.jsonl", "--repo", str(repo))
    assert code == 1 and "FAIL prefix-of: git show" in out
    code, out, _ = run(capsys, "track", "verify", "--file", str(daily), "--observations", str(obs),
                       "--public-ref", "nosuchref", "--repo", str(repo))
    assert code == 1 and "FAIL public-ref" in out


def test_observe_chain_tamper_refused(tmp_path):
    obs = tmp_path / "o.jsonl"
    ev = [{"id": "1", "type": "PushEvent", "created_at": "2026-10-02T02:00:00Z", "payload": {"head": "a" * 40}},
          {"id": "2", "type": "PushEvent", "created_at": "2026-10-03T02:00:00Z", "payload": {"head": "b" * 40}}]
    track.observe(obs, ev, "o/r", observed_at="2026-10-03T03:00:00+00:00")
    lines = obs.read_text().splitlines(keepends=True)
    obs.write_text(lines[1])
    with pytest.raises(track.TrackError, match="refusing"):
        track.observe(obs, ev, "o/r")


def test_parse_events_filters_and_normalizes():
    ev = [{"id": 5, "type": "PushEvent", "created_at": "2026-10-02T02:00:00Z",
           "payload": {"head": "A" * 40, "commits": [{"sha": "B" * 40}, {"sha": "not-a-sha"}],
                       "ref": "refs/heads/main"}},
          {"id": 6, "type": "PushEvent", "created_at": "2026-10-02T02:00:00Z", "payload": {"head": "zz"}},
          "garbage"]
    out = track.parse_events(ev, "o/r", "2026-10-02T03:00:00+00:00")
    assert out == [{"event_id": "5", "push_time": "2026-10-02T02:00:00+00:00", "head_sha": "a" * 40,
                    "commits": ["b" * 40], "repo": "o/r", "observed_at": "2026-10-02T03:00:00+00:00"}]


def test_fetch_github_events_with_injected_response():
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'[{"id": "1", "type": "PushEvent"}]'

    def opener(req, timeout):
        seen["url"], seen["auth"], seen["timeout"] = req.full_url, req.get_header("Authorization"), timeout
        return Resp()

    assert track.fetch_github_events("o/r", "tok", opener=opener) == [{"id": "1", "type": "PushEvent"}]
    assert seen == {"url": "https://api.github.com/repos/o/r/events?per_page=100", "auth": "Bearer tok",
                    "timeout": 30}
    track.fetch_github_events("o/r", None, opener=opener)
    assert seen["auth"] is None
    with pytest.raises(track.TrackError):
        track.fetch_github_events("not a repo", None, opener=opener)


# ---------------------------------------------------------------- cli wiring
def test_empty_verify_and_forward_track_since(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "track").mkdir()
    (tmp_path / "track" / "daily.jsonl").write_text("")
    (tmp_path / "track" / "observations.jsonl").write_text("")
    code, out, _ = run(capsys, "track", "verify")
    assert code == 0 and out.splitlines()[0] == "0 rows"
    code, out, _ = run(capsys, "evidence", "--format", "json")
    assert json.loads(out)["evidence"]["forward_track_since"] is None
    write_cache(tmp_path / "c", "2026-10-02")
    do_append(tmp_path / "track" / "daily.jsonl", tmp_path / "c", today="2026-10-02")
    write_cache(tmp_path / "c", "2026-10-05")
    do_append(tmp_path / "track" / "daily.jsonl", tmp_path / "c")
    code, out, _ = run(capsys, "evidence", "--format", "json")
    assert json.loads(out)["evidence"]["forward_track_since"] == "2026-10-02"
    code, out, _ = run(capsys, "NVDA", "--offline", "--cache-dir", str(tmp_path / "c"), "--format", "json")
    assert json.loads(out)["evidence"]["forward_track_since"] == "2026-10-02"


def test_track_requires_subcommand():
    with pytest.raises(SystemExit) as e:
        cli.main(["track"])
    assert e.value.code == 2
