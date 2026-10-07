"""Forward log: append-only, auditable, verifiable daily records of the default rule on the backtest universe.

- `track/daily.jsonl`: one row per (asof, symbol), fixed key order, sha256 hash chain (prev_hash / row_hash).
- `track/observations.jsonl`: transcripts of GitHub PushEvents (when a commit reached the public remote),
  same hash-chain rules, independent chain.

Integrity checks have two levels. In-file verification recomputes every row hash and the prev_hash chain; it
detects modified or deleted *inner* rows but not truncation of the tail. `--prefix-of` compares against a
reference version (a file or a git ref); it detects tail truncation and any rewrite.

Publication status of a row (only `forward` counts as forward evidence):
- unpublished: the commit introducing the row is not reachable from the public ref;
- published_unverified: reachable, but no recorded PushEvent covers that commit;
- late: the earliest covering PushEvent happened after the deadline;
- forward: the earliest covering PushEvent happened at or before the deadline.
deadline(asof) = 09:30 US/Eastern on the first Monday-Friday after asof (holidays are not modelled, which is
conservative). Commit timestamps are informational only.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from . import __version__, compute, data, evidence

DAILY_FILE = Path("track") / "daily.jsonl"
OBS_FILE = Path("track") / "observations.jsonl"
UNIVERSE = evidence.BACKTEST_UNIVERSE
ZERO_HASH = "0" * 64
DEFAULT_BUDGET_S = 600.0
PARAM_KEYS = ("preset", "vol", "vol_floor", "below_sma", "anchor", "sig_n", "med_min", "sma_n")
DAILY_KEYS = ("asof", "symbol", "close", "w", "w_vol", "above_sma200", "sma200", "sigma", "sigma_tgt", "vol_ratio",
              "params", "rule_version", "bars_through", "runner", "generated_at", "prev_hash", "row_hash")
OBS_KEYS = ("event_id", "push_time", "head_sha", "commits", "repo", "observed_at", "prev_hash", "row_hash")
STATES = ("unpublished", "published_unverified", "late", "forward")
UTC = datetime.timezone.utc
_SHA = re.compile(r"^[0-9a-f]{40}$")


class TrackError(Exception):
    """A track operation was refused (corrupt file, bad calendar, missing data, git failure)."""


# ---------------------------------------------------------------- canonical form and hash chain
def canonical(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def row_hash(row: dict) -> str:
    """sha256 of the canonical form of the row without its row_hash."""
    return hashlib.sha256(canonical({k: v for k, v in row.items() if k != "row_hash"}).encode("ascii")).hexdigest()


def seal(row: dict, prev: str) -> dict:
    """Return the row with prev_hash and row_hash set (appended last, in that order)."""
    out = {k: v for k, v in row.items() if k not in ("prev_hash", "row_hash")}
    out["prev_hash"] = prev
    out["row_hash"] = row_hash(out)
    return out


def dumps_line(row: dict) -> str:
    """File form of a row: fixed key order, ASCII, no NaN."""
    return json.dumps(row, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n"


def read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    text = path.read_bytes().decode("utf-8")
    return text.splitlines(keepends=True)


def verify_chain(lines: list[str], keys: tuple[str, ...], unique: tuple[str, ...]) -> tuple[list[dict], str | None]:
    """In-file verification. Returns (rows, error); error names the first failing 1-based line."""
    rows, prev, seen = [], ZERO_HASH, set()
    for n, line in enumerate(lines, 1):
        if not line.endswith("\n"):
            return rows, f"line {n}: missing trailing newline"
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            return rows, f"line {n}: not valid JSON ({e.msg})"
        if not isinstance(row, dict) or tuple(row) != keys:
            return rows, f"line {n}: unexpected keys or key order"
        if row["prev_hash"] != prev:
            return rows, f"line {n}: prev_hash does not match the previous row (chain broken)"
        if row_hash(row) != row["row_hash"]:
            return rows, f"line {n}: row_hash does not match the row content"
        key = tuple(row[k] for k in unique)
        if key in seen:
            return rows, f"line {n}: duplicate key {key}"
        seen.add(key)
        if keys == DAILY_KEYS and row["bars_through"] != row["asof"]:
            return rows, f"line {n}: bars_through {row['bars_through']} != asof {row['asof']}"
        rows.append(row)
        prev = row["row_hash"]
    return rows, None


def verify_daily(path: Path) -> tuple[list[dict], str | None]:
    return verify_chain(read_lines(path), DAILY_KEYS, ("asof", "symbol"))


def verify_observations(path: Path) -> tuple[list[dict], str | None]:
    return verify_chain(read_lines(path), OBS_KEYS, ("event_id",))


def coverage(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r["asof"]] = out.get(r["asof"], 0) + 1
    return dict(sorted(out.items()))


def write_appended(path: Path, lines: list[str], new_rows: list[dict]) -> None:
    """Read-all / append / atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(lines) + "".join(dumps_line(r) for r in new_rows))
    os.replace(tmp, path)


# ---------------------------------------------------------------- calendar
def deadline(asof: str) -> datetime.datetime:
    """09:30 US/Eastern on the first Monday-Friday after asof, in UTC."""
    d = datetime.date.fromisoformat(asof) + datetime.timedelta(days=1)
    while d.weekday() >= 5:
        d += datetime.timedelta(days=1)
    return datetime.datetime.combine(d, datetime.time(9, 30), tzinfo=data.ET).astimezone(UTC)


def _parse_time(s: str) -> datetime.datetime:
    t = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError(f"timestamp without timezone: {s!r}")
    return t.astimezone(UTC)


def _now_utc() -> str:
    return datetime.datetime.now(UTC).isoformat(timespec="seconds")


def runner() -> str:
    return "github-actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "local"


# ---------------------------------------------------------------- append
def default_config() -> dict:
    return evidence.effective_config()


def _load(sym: str, *, today: datetime.date, cache_dir, offline: bool, deadline_s: float, fetch, now_et, anchor):
    if offline:
        df, note = data.read_cache(sym, cache_dir), "offline"
        if df is None:
            return None, "no cached bars (offline)"
    else:
        df, note, degraded = data.update(sym, today, cache_dir=cache_dir, fetch=fetch, now_et=now_et,
                                         deadline=deadline_s, anchor=anchor)
        if df is None:
            return None, note
    df = data.settled(df, now_et, anchor)
    if df.empty:
        return None, f"no settled bars on or after {anchor}"
    return df, note


def plan_append(path: Path, *, today: datetime.date, cache_dir=None, offline: bool = False,
                budget: float = DEFAULT_BUDGET_S, asof_override: str | None = None, fetch=None, now_et=None,
                generated_at: str | None = None, symbols=UNIVERSE) -> dict:
    """Compute the rows an append would write. Raises TrackError if the run must be refused."""
    lines = read_lines(path)
    existing, err = verify_daily(path)
    if err:
        raise TrackError(f"{path}: verification failed ({err}); refusing to append. Restore the file from git "
                         "history before appending.")
    cfg = default_config()
    params = {k: cfg[k] for k in PARAM_KEYS}
    stop = time.monotonic() + budget
    loaded, notes = {}, {}
    for sym in symbols:
        try:
            df, note = _load(sym, today=today, cache_dir=cache_dir, offline=offline, deadline_s=stop, fetch=fetch,
                             now_et=now_et, anchor=cfg["anchor"])
        except data.BudgetExceeded as e:
            raise TrackError(f"fetch budget exhausted at {sym}: {e}; nothing written") from e
        if df is not None and asof_override is not None:
            df = df[df.index <= asof_override]
            df = None if df.empty else df
        if df is None:
            notes[sym] = note
        else:
            loaded[sym] = df
        notes.setdefault(sym, note)
    through = {s: str(df.index[-1].date()) for s, df in loaded.items()}
    if not through:
        raise TrackError("no symbol has settled bars; cannot determine asof")
    asof = asof_override or max(through.values())
    a = datetime.date.fromisoformat(asof)
    if a > today:
        raise TrackError(f"asof {asof} is after today {today} (US/Eastern); refusing")
    if a.weekday() >= 5:
        raise TrackError(f"asof {asof} is not a Monday-Friday; refusing")
    done = {(r["asof"], r["symbol"]) for r in existing}
    stale = {s: (through.get(s), notes.get(s)) for s in symbols if through.get(s) != asof}
    gen = generated_at or _now_utc()
    prev = existing[-1]["row_hash"] if existing else ZERO_HASH
    new_rows, skipped = [], []
    for sym in symbols:
        if sym in stale:
            continue
        if (asof, sym) in done:
            skipped.append(sym)
            continue
        c = compute.compute_symbol(sym, loaded[sym], evidence.weight_params(cfg))
        row = {"asof": asof, "symbol": sym, "close": c["close"], "w": c["w"], "w_vol": c["w_vol"],
               "above_sma200": c["above_sma200"], "sma200": c["sma200"], "sigma": c["sigma"],
               "sigma_tgt": c["sigma_tgt"], "vol_ratio": c["vol_ratio"], "params": params,
               "rule_version": __version__, "bars_through": through[sym], "runner": runner(), "generated_at": gen}
        row = seal(row, prev)
        prev = row["row_hash"]
        new_rows.append(row)
    return {"asof": asof, "deadline": deadline(asof).isoformat(), "rows": new_rows, "stale": stale,
            "skipped": skipped, "lines": lines}


# ---------------------------------------------------------------- git helpers
def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise TrackError(f"git {' '.join(args)} failed: {r.stderr.strip() or r.returncode}")
    return r


def git_show(repo: Path, spec: str) -> str:
    """Content of `REF:path`."""
    r = subprocess.run(["git", "-C", str(repo), "show", spec], capture_output=True)
    if r.returncode != 0:
        raise TrackError(f"git show {spec} failed: {r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout.decode("utf-8")


def is_ancestor(repo: Path, a: str, b: str) -> bool:
    r = _git(repo, "merge-base", "--is-ancestor", a, b, check=False)
    if r.returncode in (0, 1):
        return r.returncode == 0
    return False                                        # unknown object (e.g. a SHA not present locally)


def _repo_relpath(repo: Path, path: Path) -> str:
    top = Path(_git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    return Path(os.path.relpath(path.resolve(), top)).as_posix()


def introducing_commits(repo: Path, relpath: str) -> dict[str, tuple[str, str]]:
    """row_hash -> (sha, committer ISO time) of the first commit whose version of the file contains the row."""
    log = _git(repo, "log", "--reverse", "--format=%H %cI", "--", relpath, check=False)
    out: dict[str, tuple[str, str]] = {}
    for entry in log.stdout.split("\n"):
        if not entry.strip():
            continue
        sha, when = entry.split(" ", 1)
        r = subprocess.run(["git", "-C", str(repo), "show", f"{sha}:{relpath}"], capture_output=True)
        if r.returncode != 0:
            continue                                     # file deleted in this commit
        for line in r.stdout.decode("utf-8", "replace").splitlines():
            try:
                h = json.loads(line).get("row_hash")
            except (json.JSONDecodeError, AttributeError):
                continue
            if isinstance(h, str) and h not in out:
                out[h] = (sha, when)
    return out


def public_status(rows: list[dict], observations: list[dict], *, repo: Path, relpath: str, ref: str) -> list[dict]:
    """Per-row publication state (see module docstring)."""
    _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    intro = introducing_commits(repo, relpath)
    out, cache = [], {}
    for r in rows:
        dl = deadline(r["asof"])
        rec = {"asof": r["asof"], "symbol": r["symbol"], "row_hash": r["row_hash"], "commit": None,
               "committed_at": None, "published_at": None, "deadline": dl.isoformat(), "state": "unpublished"}
        hit = intro.get(r["row_hash"])
        if hit is not None:
            sha, when = hit
            rec["commit"], rec["committed_at"] = sha, when
            if is_ancestor(repo, sha, ref):
                if sha not in cache:
                    times = [_parse_time(o["push_time"]) for o in observations
                             if sha in o["commits"] or sha == o["head_sha"] or is_ancestor(repo, sha, o["head_sha"])]
                    cache[sha] = min(times) if times else None
                pub = cache[sha]
                if pub is None:
                    rec["state"] = "published_unverified"
                else:
                    rec["published_at"] = pub.isoformat()
                    rec["state"] = "late" if pub > dl else "forward"
        out.append(rec)
    return out


# ---------------------------------------------------------------- observe
def _sha(x) -> str | None:
    s = str(x or "").strip().lower()
    return s if _SHA.match(s) else None


def parse_events(events: list, repo: str, observed_at: str) -> list[dict]:
    """PushEvents only; keeps id, created_at, head and commit SHAs. Nothing else from the event is stored."""
    out = []
    for e in events if isinstance(events, list) else []:
        if not isinstance(e, dict) or e.get("type") != "PushEvent":
            continue
        payload = e.get("payload") or {}
        head = _sha(payload.get("head"))
        if head is None or not e.get("id") or not e.get("created_at"):
            continue
        commits = [s for s in (_sha(c.get("sha")) for c in payload.get("commits") or [] if isinstance(c, dict)) if s]
        out.append({"event_id": str(e["id"]), "push_time": _parse_time(e["created_at"]).isoformat(),
                    "head_sha": head, "commits": commits, "repo": repo, "observed_at": observed_at})
    return out


def fetch_github_events(repo: str, token: str | None = None, opener=urllib.request.urlopen, timeout: float = 30):
    """GET /repos/{owner}/{repo}/events (first page, up to 100 events)."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise TrackError(f"expected OWNER/REPO, got {repo!r}")
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/events?per_page=100",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "riskbound-track"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with opener(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def observe(path: Path, events: list, repo: str, observed_at: str | None = None) -> list[dict]:
    """Append new PushEvent observations (dedup by event_id, oldest first). Returns the appended rows."""
    lines = read_lines(path)
    existing, err = verify_observations(path)
    if err:
        raise TrackError(f"{path}: verification failed ({err}); refusing to append. Restore the file from git "
                         "history before appending.")
    seen = {r["event_id"] for r in existing}
    fresh = {}
    for o in parse_events(events, repo, observed_at or _now_utc()):
        if o["event_id"] not in seen and o["event_id"] not in fresh:
            fresh[o["event_id"]] = o
    prev = existing[-1]["row_hash"] if existing else ZERO_HASH
    new_rows = []
    for o in sorted(fresh.values(), key=lambda o: (o["push_time"], o["event_id"])):
        row = seal(o, prev)
        prev = row["row_hash"]
        new_rows.append(row)
    if new_rows:
        write_appended(path, lines, new_rows)
    return new_rows


def earliest_asof(path: Path) -> str | None:
    """Earliest asof in a daily log (None for a missing / empty / unreadable file)."""
    try:
        asofs = [json.loads(line)["asof"] for line in read_lines(path) if line.strip()]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return min(asofs) if asofs else None
