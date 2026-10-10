"""Command line interface.

python -m jobfunnel scan      # full scan (default command)
python -m jobfunnel suggest   # rank what scan found, best first, with reasons
python -m jobfunnel validate  # check config/companies.json makes sense (offline)
python -m jobfunnel doctor    # probe every configured source, live
python -m jobfunnel clean     # clear the HTTP cache
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from . import __version__
from .config import Config, ConfigError
from .http import Http
from .models import Job
from .relevance import Profile, Verdict, rank
from .scan import run_scan
from .sources import ATS_FETCHERS, FEED_FETCHERS

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "companies.json"
DEFAULT_PROFILE = ROOT / "config" / "profile.json"
DEFAULT_DATA_DIR = ROOT / "docs" / "data"


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", default=str(DEFAULT_CONFIG), help="path to companies.json")
    p.add_argument(
        "--data-dir", default=str(DEFAULT_DATA_DIR), help="where jobs.json/seen.json/status.json live"
    )
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    p.add_argument("--no-cache", action="store_true", help="ignore and refresh the HTTP response cache")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobfunnel", description="cleanjobfunnel scanner")
    parser.add_argument("--version", action="version", version=f"jobfunnel {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_scan = sub.add_parser("scan", help="fetch, filter, dedupe and write the dashboard data")
    _add_common(p_scan)
    p_scan.add_argument(
        "--only", action="append", default=None, metavar="NAME", help="scan just this source (repeatable)"
    )
    p_scan.add_argument("--dry-run", action="store_true", help="do everything except writing files")
    p_scan.add_argument("--min-score", type=int, default=None, help="override minimum match score (0..100)")
    p_scan.add_argument(
        "--profile",
        default=str(DEFAULT_PROFILE),
        help="path to profile.json; the relevance verdict written to jobs.json",
    )
    p_scan.add_argument(
        "--no-profile", action="store_true", help="skip relevance scoring entirely (keyword match only)"
    )

    p_validate = sub.add_parser("validate", help="validate the config file (no network)")
    _add_common(p_validate)

    p_suggest = sub.add_parser("suggest", help="rank scanned jobs against the profile, best first")
    _add_common(p_suggest)
    p_suggest.add_argument(
        "--profile", default=str(DEFAULT_PROFILE), help="path to profile.json (the structured resume)"
    )
    p_suggest.add_argument("--limit", type=int, default=20, help="how many to print (default 20)")
    p_suggest.add_argument(
        "--min-score", type=int, default=0, help="hide anything scoring below this (0..100)"
    )
    p_suggest.add_argument("--json", action="store_true", help="emit JSON instead of the human listing")

    p_doctor = sub.add_parser("doctor", help="probe every configured source and report status")
    _add_common(p_doctor)

    sub.add_parser("clean", help="delete the HTTP response cache")
    return parser


def _load_config(args: argparse.Namespace) -> Config:
    return Config.load(args.config, known_ats=set(ATS_FETCHERS), known_feeds=set(FEED_FETCHERS))


def _cmd_scan(args: argparse.Namespace) -> int:
    config = _load_config(args)
    for w in config.warnings:
        print(f"warning: {w}")
    if args.min_score is not None:
        if not 0 <= args.min_score <= 100:
            print("error: --min-score must be 0..100", file=sys.stderr)
            return 1
        config.min_score = args.min_score

    data_dir = Path(args.data_dir)
    only = set(args.only) if args.only else None
    http = Http(cache_dir=None) if args.no_cache else Http()
    with http:
        result = run_scan(
            http,
            config,
            data_dir,
            only=only,
            dry_run=args.dry_run,
            profile_path=None if args.no_profile else args.profile,
        )

    if result.nothing_selected:
        print(
            f"error: no configured source matches --only {sorted(only)}; nothing fetched, nothing written",
            file=sys.stderr,
        )
        return 2

    if args.dry_run:
        target = f"would write {data_dir / 'jobs.json'}"
    elif result.persisted:
        target = f"wrote {data_dir / 'jobs.json'}"
    else:
        target = "partial scan -- nothing written (run without --only to refresh the dashboard data)"
    print(
        f"{result.fetched} jobs fetched from {result.ok_sources}/{result.total_sources} sources, "
        f"{len(result.jobs)} matched ({result.filtered_out} filtered out, {result.merged_duplicates} duplicates merged) "
        f"in {result.duration_s}s -- {target}"
    )
    for r in result.reports:
        if not r.ok:
            print(f"  ! {r.source}: {r.error}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        config = _load_config(args)
    except ConfigError as e:
        print(f"INVALID: {e}", file=sys.stderr)
        return 1
    verified = sum(1 for c in config.companies if c.verified)
    enabled_feeds = [f.name for f in config.enabled_feeds()]
    print(
        f"OK: {len(config.companies)} companies ({verified} marked verified), feeds enabled: {', '.join(enabled_feeds)}"
    )
    for w in config.warnings:
        print(f"warning: {w}")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    config = _load_config(args)
    print(f"probing {len(config.companies)} company boards and {len(config.enabled_feeds())} feeds...")
    from .scan import _fetch_companies, _fetch_feeds

    t0 = time.monotonic()
    with Http() as http:
        _, reports = _fetch_companies(http, config, None)
        _, feed_reports = _fetch_feeds(http, config, None)
    reports.extend(feed_reports)

    width = max(len(r.source) for r in reports)
    for r in reports:
        status = "ok  " if r.ok else "FAIL"
        line = f"{status} {r.source:<{width}}  {r.count:>5} jobs  {r.duration_ms:>5}ms"
        if r.error:
            line += f"  {r.error[:90]}"
        print(line)
    ok = sum(1 for r in reports if r.ok)
    print(f"\n{ok}/{len(reports)} sources healthy ({time.monotonic() - t0:.1f}s)")
    return 0 if ok else 1


def _cmd_clean(args: argparse.Namespace) -> int:
    removed = Http().clear_cache()
    print("cache cleared" if removed else "nothing to clear")
    return 0


def _cmd_suggest(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir)
    jobs_path = data_dir / "jobs.json"
    if not jobs_path.exists():
        print(
            f"error: no scanned data at {jobs_path}\n"
            "run `python -m jobfunnel scan` first -- suggest ranks what scan already fetched",
            file=sys.stderr,
        )
        return 2

    try:
        profile = Profile.from_dict(json.loads(Path(args.profile).read_text(encoding="utf-8")))
    except FileNotFoundError:
        print(f"error: profile not found: {args.profile}", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, ValueError) as e:
        print(f"error: profile is not usable ({args.profile}): {e}", file=sys.stderr)
        return 2

    try:
        raw = json.loads(jobs_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"error: {jobs_path} is not valid JSON: {e}", file=sys.stderr)
        return 2

    # tolerate both a bare list and the wrapped shape the dashboard writes
    records = raw if isinstance(raw, list) else raw.get("jobs", [])
    jobs = []
    for rec in records:
        rec = dict(rec)
        rec.pop("score", None)
        rec.pop("match_reasons", None)
        rec.pop("is_senior", None)
        try:
            jobs.append(Job(**rec))
        except TypeError:
            # a record missing a required field is not worth crashing over;
            # it is one posting, and scan writes the authoritative set
            continue

    ranked_pairs: list[tuple[Job, Verdict]] = []
    for j in jobs:
        # scan already scored this against the live posting text and persisted
        # the verdict. Recomputing from jobs.json would be scoring a summary of
        # the evidence, which is not the same thing -- so trust the stored
        # verdict and only score from scratch when there is none.
        stored = j.relevance or {}
        if stored:
            verdict = Verdict(
                keep=bool(stored.get("eligible", True)),
                score=int(stored.get("score", 0)),
                reasons=list(stored.get("reasons") or []),
                blockers=list(stored.get("blockers") or []),
                skill_hits=list(stored.get("skill_hits") or []),
                skill_gaps=list(stored.get("skill_gaps") or []),
            )
        else:
            verdict = rank(j, profile)
        if verdict.keep:
            ranked_pairs.append((j, verdict))

    ranked_pairs.sort(key=lambda pair: (-pair[1].score, pair[0].title or ""))
    shown = [pair for pair in ranked_pairs if pair[1].score >= args.min_score][: args.limit]

    if args.json:
        payload = [
            {
                "score": v.score,
                "title": j.title,
                "company": j.company,
                "location": j.location,
                "salary": j.salary,
                "posted_at": j.posted_at,
                "url": j.url,
                "reasons": v.reasons,
                "skill_hits": v.skill_hits,
                "skill_gaps": v.skill_gaps,
            }
            for j, v in shown
        ]
        print(json.dumps(payload, indent=2))
        return 0

    if not shown:
        print(f"{len(ranked_pairs)} eligible, none above --min-score {args.min_score}")
        return 0

    width = max(len(j.company) for j, _ in shown)
    print(f"{len(ranked_pairs)} eligible of {len(jobs)} scanned, showing {len(shown)} best\n")
    for i, (j, v) in enumerate(shown, 1):
        print(f"{i:>2}. {v.score:>3}  {j.company:<{width}}  {j.title}")
        tail = "  ".join(p for p in [j.location, j.salary] if p)
        if tail:
            print(f"        {tail}")
        if j.url:
            print(f"        {j.url}")
        for r in v.reasons:
            print(f"        - {r}")
        if v.skill_gaps:
            print(f"        ! required but not on the profile: {', '.join(sorted(v.skill_gaps))}")
        print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ["scan"]  # bare `python -m jobfunnel` means "scan"
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    handlers = {
        "scan": _cmd_scan,
        "suggest": _cmd_suggest,
        "validate": _cmd_validate,
        "doctor": _cmd_doctor,
        "clean": _cmd_clean,
    }
    handler = handlers.get(args.command or "scan")
    try:
        return handler(args)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
