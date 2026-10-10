"""Command line interface.

python -m jobfunnel scan      # full scan (default command)
python -m jobfunnel validate  # check config/companies.json makes sense (offline)
python -m jobfunnel doctor    # probe every configured source, live
python -m jobfunnel clean     # clear the HTTP cache
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from . import __version__
from .config import Config, ConfigError
from .http import Http
from .scan import run_scan
from .sources import ATS_FETCHERS, FEED_FETCHERS

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "companies.json"
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

    p_validate = sub.add_parser("validate", help="validate the config file (no network)")
    _add_common(p_validate)

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
        result = run_scan(http, config, data_dir, only=only, dry_run=args.dry_run)

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
