"""Command line: `riskbound SYM [SYM ...]`, `riskbound evidence` and `riskbound track append|verify|observe`.

Every output format carries the evidence-boundary block. Output is descriptive only: the target weight of
the rule at the latest settled close, no holdings, amounts or share counts.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import sys
import time
from pathlib import Path

from . import __version__, compute, data, evidence, rules, track

FORMATS = ("table", "json", "markdown")
DEFAULT_BUDGET_S = 120.0
TABLE_COLS = ("symbol", "date", "close", "w", "w_vol", "above_sma200", "vol_ratio", "universe", "stale")
_ROW_KEYS = ("symbol", "date", "close", "w", "w_vol", "wV", "above_sma200", "sma200", "sigma", "sigma_tgt",
             "vol_ratio", "bars", "first_bar")


# ---------------------------------------------------------------- argument parsing
def _vol_floor(s: str):
    if s.strip().lower() == "none":
        return None
    try:
        x = float(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number in [0, 1] or 'none', got {s!r}") from None
    if not 0.0 <= x <= 1.0:
        raise argparse.ArgumentTypeError(f"vol floor must be in [0, 1] or 'none', got {s}")
    return x


def _below_sma(s: str) -> float:
    try:
        x = float(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number in (0, 1], got {s!r}") from None
    if not 0.0 < x <= 1.0:
        raise argparse.ArgumentTypeError(f"below-sma weight must be in (0, 1], got {s}")
    return x


def _date(s: str) -> str:
    try:
        return datetime.date.fromisoformat(s).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a date YYYY-MM-DD, got {s!r}") from None


def _budget(s: str) -> float:
    try:
        x = float(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected seconds, got {s!r}") from None
    if not (math.isfinite(x) and x > 0):
        raise argparse.ArgumentTypeError(f"budget must be a finite positive number, got {s}")
    return x


def _add_config_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--format", choices=FORMATS, default="table")
    p.add_argument("--preset", choices=sorted(rules.PRESETS), default=rules.DEFAULT_PRESET,
                   help=f"rule preset (default {rules.DEFAULT_PRESET}: untested 0.75 compromise)")
    p.add_argument("--vol-floor", type=_vol_floor, default=..., metavar="X|none",
                   help="override the floor on the volatility weight; 'none' disables it")
    p.add_argument("--below-sma", type=_below_sma, default=..., metavar="X",
                   help="override the weight multiplier below the 200-day SMA")
    p.add_argument("--anchor", type=_date, default=None, metavar="YYYY-MM-DD",
                   help=f"start of the expanding sigma_tgt window (default {rules.ANCHOR})")


def _symbols_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="riskbound",
        description="Target weight of a volatility / 200-day SMA rule at the latest settled close, with an "
                    "evidence-boundary block. Use 'riskbound evidence' to print only the block.")
    p.add_argument("symbols", nargs="+", metavar="SYM")
    _add_config_args(p)
    p.add_argument("--cache-dir", default=None, metavar="DIR",
                   help=f"bar cache directory (default ${data.CACHE_ENV} or ~/.cache/riskbound/bars)")
    p.add_argument("--offline", action="store_true", help="read the cache only; never fetch")
    p.add_argument("--today", type=_date, default=None, metavar="YYYY-MM-DD",
                   help="US/Eastern date used for fetching (default: today in US/Eastern)")
    p.add_argument("--budget", type=_budget, default=DEFAULT_BUDGET_S, metavar="SECONDS",
                   help=f"total fetch budget in seconds (default {DEFAULT_BUDGET_S:g})")
    p.add_argument("--version", action="version", version=f"riskbound {__version__}")
    return p


def _evidence_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="riskbound evidence",
                                description="Print the evidence-boundary block for a configuration.")
    _add_config_args(p)
    return p


def _config(a: argparse.Namespace) -> dict:
    return evidence.effective_config(a.preset, vol_floor=a.vol_floor, below_sma=a.below_sma, anchor=a.anchor)


# ---------------------------------------------------------------- computation
def _empty_row(sym: str) -> dict:
    return {k: None for k in _ROW_KEYS} | {"symbol": sym}


def _run_symbol(sym: str, cfg: dict, a: argparse.Namespace, today: datetime.date, deadline: float) -> dict:
    stale, note = False, None
    if a.offline:
        df = data.read_cache(sym, a.cache_dir)
        if df is None:
            raise LookupError("no cached bars (offline)")
        note = f"offline: cache through {df.index[-1].date()}"
    else:
        df, note, stale = data.update(sym, today, cache_dir=a.cache_dir, deadline=deadline, anchor=cfg["anchor"])
        if df is None:
            raise LookupError(note)
    df = data.settled(df, anchor=cfg["anchor"])
    if df.empty:
        raise LookupError(f"no settled bars on or after {cfg['anchor']}")
    row = compute.compute_symbol(sym, df, evidence.weight_params(cfg))
    return row | {"stale": bool(stale), "note": note}


def run_symbols(a: argparse.Namespace) -> tuple[dict, int]:
    cfg = _config(a)
    today = (datetime.date.fromisoformat(a.today) if a.today else datetime.datetime.now(data.ET).date())
    deadline = time.monotonic() + a.budget
    symbols = [s.upper() for s in a.symbols]
    rows, budget_hit = [], False
    for sym in symbols:
        member = evidence.universe_membership([sym])
        try:
            if budget_hit:
                raise data.BudgetExceeded("fetch budget exhausted before this symbol")
            row = _run_symbol(sym, cfg, a, today, deadline) | {"error": None}
        except data.BudgetExceeded as e:
            budget_hit = True
            row = _empty_row(sym) | {"stale": False, "note": None, "error": f"fetch budget exhausted: {e}"}
        except Exception as e:  # one symbol failing must not hide the others
            row = _empty_row(sym) | {"stale": False, "note": None, "error": str(e) or type(e).__name__}
        rows.append(row | {"universe_membership": member})
    ok = [r for r in rows if r["error"] is None]
    asof = max((r["date"] for r in ok), default=None)
    out = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "rule_version": __version__,
        "tickers": rows,
        "evidence": evidence.build(cfg, symbols, data_asof=asof,
                                   forward_track_since=track.earliest_asof(track.DAILY_FILE)),
    }
    failed = [r["symbol"] for r in rows if r["error"] is not None]
    if not ok:
        return out, 1
    if failed:
        print(f"warning: no result for {', '.join(failed)} (see 'error' fields)", file=sys.stderr)
    return out, 0


# ---------------------------------------------------------------- rendering
def _num(x, nd: int = 4) -> str:
    if x is None:
        return "-"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _cells(r: dict) -> list[str]:
    return [r["symbol"], r["date"] or "-", _num(r["close"], 2), _num(r["w"]), _num(r["w_vol"]),
            _num(r["above_sma200"]), _num(r["vol_ratio"]), r["universe_membership"], _num(r["stale"])]


def _finding_line(f: dict) -> str:
    mark = "[applies]" if f["applies_to_current_config"] else "[n/a]"
    cell = f["cell"] or "-"
    return f"{mark} {cell} {f['component']}: {f['claim']} -> {f['result']} ({f['detail']})"


def _evidence_lines(ev: dict) -> list[str]:
    c = ev["config"]
    cfg = ", ".join(f"{k}={evidence.fmt_value(v)}" for k, v in c.items())
    lines = [
        f"config: {cfg}",
        f"config_status: {ev['config_status']}",
        f"deviations: {'; '.join(ev['deviations']) if ev['deviations'] else 'none'}",
        f"universe_membership: {ev['universe_membership'] if ev['universe_membership'] is not None else 'null'}",
        "component_findings:",
        *(f"  {_finding_line(f)}" for f in ev["component_findings"]),
        f"not_established: {'; '.join(ev['not_established'])}",
        f"evidence_window: {', '.join(ev['evidence_window'])}",
        f"evidence_universe: {ev['evidence_universe']}",
        f"forward_track_since: {ev['forward_track_since'] or 'null'}",
        f"data_asof: {ev['data_asof'] or 'null'}",
        f"disclaimer: {ev['disclaimer']}",
    ]
    return lines


def _row_notes(rows: list[dict]) -> list[str]:
    out = []
    for r in rows:
        if r["error"] is not None:
            out.append(f"{r['symbol']}: error: {r['error']}")
        elif r["note"]:
            out.append(f"{r['symbol']}: {r['note']}")
    return out


def render_table(out: dict | None, ev: dict) -> str:
    lines = []
    if out is not None:
        body = [_cells(r) for r in out["tickers"]]
        widths = [max(len(h), *(len(b[i]) for b in body)) for i, h in enumerate(TABLE_COLS)]
        fmt = lambda cells: "  ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()  # noqa: E731
        lines += [fmt(TABLE_COLS), fmt(["-" * w for w in widths]), *(fmt(b) for b in body)]
        notes = _row_notes(out["tickers"])
        if notes:
            lines += ["", "NOTES", *notes]
        lines.append("")
    lines += ["EVIDENCE", *_evidence_lines(ev)]
    return "\n".join(lines)


def render_markdown(out: dict | None, ev: dict) -> str:
    lines = []
    if out is not None:
        lines += ["| " + " | ".join(TABLE_COLS) + " |", "|" + "---|" * len(TABLE_COLS)]
        lines += ["| " + " | ".join(_cells(r)) + " |" for r in out["tickers"]]
        notes = _row_notes(out["tickers"])
        if notes:
            lines += ["", *(f"- {n}" for n in notes)]
        lines.append("")
    lines += ["### Evidence", ""]
    for ln in _evidence_lines(ev):
        lines.append(f"  - {ln.strip()}" if ln.startswith("  ") else f"- {ln}")
    return "\n".join(lines)


def _emit(fmt: str, out: dict | None, ev: dict) -> None:
    if fmt == "json":
        print(json.dumps(out if out is not None else {"evidence": ev}, ensure_ascii=False, indent=2))
    elif fmt == "markdown":
        print(render_markdown(out, ev))
    else:
        print(render_table(out, ev))


# ---------------------------------------------------------------- track
def _track_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="riskbound track", description="Append-only forward log of the default rule "
                                "on the backtest universe (see track/README.md).")
    sub = p.add_subparsers(dest="cmd", required=True)
    ap = sub.add_parser("append", help="append rows for the consensus asof (one row per symbol)")
    ap.add_argument("--file", type=Path, default=track.DAILY_FILE)
    ap.add_argument("--cache-dir", default=None, metavar="DIR")
    ap.add_argument("--offline", action="store_true", help="read the cache only; never fetch")
    ap.add_argument("--today", type=_date, default=None, metavar="YYYY-MM-DD",
                    help="US/Eastern date (default: today in US/Eastern)")
    ap.add_argument("--budget", type=_budget, default=track.DEFAULT_BUDGET_S, metavar="SECONDS",
                    help=f"total fetch budget in seconds (default {track.DEFAULT_BUDGET_S:g})")
    ap.add_argument("--dry-run", action="store_true", help="print the rows that would be written; write nothing")
    ap.add_argument("--asof", type=_date, default=None, metavar="YYYY-MM-DD",
                    help="demonstration date; only allowed with --dry-run")
    vp = sub.add_parser("verify", help="verify the hash chains; optionally against a reference or public ref")
    vp.add_argument("--file", type=Path, default=track.DAILY_FILE)
    vp.add_argument("--observations", type=Path, default=track.OBS_FILE)
    vp.add_argument("--prefix-of", default=None, metavar="PATH|REF:PATH",
                    help="require a reference version (file path or git REF:path) to be an exact prefix")
    vp.add_argument("--public-ref", default=None, metavar="REF", help="classify rows against a public git ref")
    vp.add_argument("--repo", type=Path, default=Path("."), metavar="DIR", help="git repository (default .)")
    vp.add_argument("--format", choices=("table", "json"), default="table")
    op = sub.add_parser("observe", help="record pushes from the GitHub repository activity API in the "
                        "observations log")
    op.add_argument("--github", required=True, metavar="OWNER/REPO", help="repository the activity belongs to")
    op.add_argument("--ref", default=track.DEFAULT_REF, help=f"ref to observe (default {track.DEFAULT_REF})")
    op.add_argument("--activity-file", type=Path, default=None,
                    help="read a JSON array of activity entries from a local file instead of the GitHub API")
    op.add_argument("--observations", type=Path, default=track.OBS_FILE)
    return p


def _track_append(a: argparse.Namespace, p: argparse.ArgumentParser) -> int:
    if a.asof is not None and not a.dry_run:
        p.error("--asof is only allowed with --dry-run; a real append always uses the consensus asof")
    today = datetime.date.fromisoformat(a.today) if a.today else datetime.datetime.now(data.ET).date()
    try:
        plan = track.plan_append(a.file, today=today, cache_dir=a.cache_dir, offline=a.offline, budget=a.budget,
                                 asof_override=a.asof)
    except track.TrackError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    for sym, (through, note) in plan["stale"].items():
        print(f"not written: {sym} bars_through={through or 'none'} ({note})", file=sys.stderr)
    if a.dry_run:
        print(f"dry run: asof={plan['asof']} deadline={plan['deadline']} rows={len(plan['rows'])} "
              f"stale={len(plan['stale'])} already_present={len(plan['skipped'])} (nothing written)")
        for r in plan["rows"]:
            print(track.dumps_line(r), end="")
        return 0
    if not plan["rows"]:
        print(f"no new rows for asof={plan['asof']}")
        return 0
    track.write_appended(a.file, plan["lines"], plan["rows"])
    print(f"appended {len(plan['rows'])} rows for asof={plan['asof']} to {a.file}")
    return 0


def _track_verify(a: argparse.Namespace) -> int:
    rows, err = track.verify_daily(a.file)
    obs, oerr = track.verify_observations(a.observations)
    report = {"file": str(a.file), "rows": len(rows), "error": err, "coverage": track.coverage(rows),
              "observations": len(obs), "observations_error": oerr}
    code = 1 if (err or oerr) else 0
    if a.prefix_of and not err:
        try:
            ref_text = (Path(a.prefix_of).read_text(encoding="utf-8") if Path(a.prefix_of).is_file()
                        else track.git_show(a.repo, a.prefix_of))
        except (track.TrackError, OSError) as e:
            report["prefix_error"] = str(e)
            code = 1
        else:
            cur = "".join(track.read_lines(a.file))
            ok = cur.startswith(ref_text)
            report["prefix_of"] = {"reference": a.prefix_of, "reference_rows": len(ref_text.splitlines()),
                                   "is_prefix": ok}
            code = code or (0 if ok else 1)
    if a.public_ref and not (err or oerr):
        try:
            st = track.public_status(rows, obs, repo=a.repo, relpath=track._repo_relpath(a.repo, a.file),
                                     ref=a.public_ref)
        except track.TrackError as e:
            report["public_error"] = str(e)
            code = 1
        else:
            report["public_ref"] = a.public_ref
            report["states"] = {s: sum(r["state"] == s for r in st) for s in track.STATES}
            report["rows_status"] = st
    if a.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return code
    print(f"{report['rows']} rows" + (f", {len(report['coverage'])} asof dates" if rows else ""))
    for d, n in report["coverage"].items():
        print(f"  {d}: {n}/{len(track.UNIVERSE)}")
    print(f"FAIL {a.file}: {err}" if err else f"OK {a.file}: hash chain intact")
    print(f"FAIL {a.observations}: {oerr}" if oerr else f"OK {a.observations}: {len(obs)} observations")
    if "prefix_error" in report:
        print(f"FAIL prefix-of: {report['prefix_error']}")
    elif "prefix_of" in report:
        pf = report["prefix_of"]
        print(f"{'OK' if pf['is_prefix'] else 'FAIL'} prefix-of {pf['reference']}: reference ({pf['reference_rows']} rows) "
              f"{'is' if pf['is_prefix'] else 'is NOT'} an exact prefix")
    if "public_error" in report:
        print(f"FAIL public-ref: {report['public_error']}")
    elif "states" in report:
        print(f"public ref {a.public_ref}: " + ", ".join(f"{s}={n}" for s, n in report["states"].items()))
        print("asof        symbol  state                 commit   committed_at               published_at               deadline")
        for r in report["rows_status"]:
            print(f"{r['asof']}  {r['symbol']:<6}  {r['state']:<20}  {(r['commit'] or '-')[:7]:<7}  "
                  f"{r['committed_at'] or '-':<25}  {r['published_at'] or '-':<25}  {r['deadline']}")
    return code


def _track_observe(a: argparse.Namespace) -> int:
    try:
        if a.activity_file is not None:
            activity = json.loads(a.activity_file.read_text(encoding="utf-8"))
        else:
            activity = track.fetch_github_activity(a.github, a.ref, os.environ.get("GITHUB_TOKEN"))
        new = track.observe(a.observations, activity, a.github, a.ref)
    except (track.TrackError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"recorded {len(new)} new push observation(s) of {a.ref} in {a.observations}")
    return 0


def track_main(argv: list[str]) -> int:
    p = _track_parser()
    a = p.parse_args(argv)
    if a.cmd == "append":
        return _track_append(a, p)
    if a.cmd == "verify":
        return _track_verify(a)
    return _track_observe(a)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "evidence":
        a = _evidence_parser().parse_args(argv[1:])
        ev = evidence.build(_config(a), forward_track_since=track.earliest_asof(track.DAILY_FILE))
        _emit(a.format, None, ev)
        return 0
    if argv and argv[0] == "track":
        return track_main(argv[1:])
    a = _symbols_parser().parse_args(argv)
    out, code = run_symbols(a)
    _emit(a.format, out, out["evidence"])
    return code


if __name__ == "__main__":
    sys.exit(main())
