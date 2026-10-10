"""Scan orchestration: fetch everything, filter, dedupe, persist.

The order matters:
1. every source is tried, and per-source failures are reported but never abort;
2. the matching engine scores each surviving job and records why it matched;
3. cross-source duplicates are merged into one card with also_on links;
4. first_seen bookkeeping happens last, so a job that only exists as a
   duplicate still keeps its original first_seen timestamp.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import store
from .config import Config
from .dedupe import merge_duplicates
from .http import Http
from .matching import RuleSet, evaluate
from .models import Job, SourceReport
from .relevance import Profile, rank
from .sources import ATS_FETCHERS, FEED_FETCHERS
from .sources.base import coerce_location

log = logging.getLogger(__name__)


@dataclass
class ScanResult:
    jobs: list[Job] = field(default_factory=list)
    reports: list[SourceReport] = field(default_factory=list)
    fetched: int = 0
    filtered_out: int = 0
    merged_duplicates: int = 0
    all_failed: bool = False
    nothing_selected: bool = False
    dry_run: bool = False
    persisted: bool = True
    duration_s: float = 0.0

    @property
    def ok_sources(self) -> int:
        return sum(1 for r in self.reports if r.ok)

    @property
    def total_sources(self) -> int:
        return len(self.reports)


def _fetch_companies(
    http: Http, config: Config, only: set[str] | None
) -> tuple[list[Job], list[SourceReport]]:
    jobs: list[Job] = []
    reports: list[SourceReport] = []
    for company in config.companies:
        if only and company.name not in only:
            continue
        fetcher = ATS_FETCHERS.get(company.ats)
        if not fetcher:
            reports.append(SourceReport(company.name, False, 0, f"unknown ats '{company.ats}'"))
            continue
        t0 = time.monotonic()
        try:
            company_jobs, err = fetcher(http, company)
        except Exception as e:  # noqa: BLE001 - one board must never kill the scan
            company_jobs, err = [], f"unexpected error: {e}"
        reports.append(
            SourceReport(
                source=company.name,
                ok=err is None,
                count=len(company_jobs),
                error=err,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        )
        jobs.extend(company_jobs)
        if not err:
            log.debug("%s: %d jobs", company.name, len(company_jobs))
    return jobs, reports


def _fetch_feeds(http: Http, config: Config, only: set[str] | None) -> tuple[list[Job], list[SourceReport]]:
    jobs: list[Job] = []
    reports: list[SourceReport] = []
    for feed in config.enabled_feeds():
        if only and feed.name not in only:
            continue
        fetcher = FEED_FETCHERS.get(feed.name)
        if not fetcher:
            reports.append(SourceReport(feed.name, False, 0, f"unknown feed '{feed.name}'"))
            continue
        t0 = time.monotonic()
        try:
            feed_jobs, err = fetcher(http, feed.options)
        except Exception as e:  # noqa: BLE001
            feed_jobs, err = [], f"unexpected error: {e}"
        label = f"{feed.name}" + (f" [{feed.options}]" if feed.options else "")
        reports.append(
            SourceReport(
                source=label,
                ok=err is None,
                count=len(feed_jobs),
                error=err,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        )
        jobs.extend(feed_jobs)
    return jobs, reports


def run_scan(
    http: Http,
    config: Config,
    data_dir: Path,
    only: set[str] | None = None,
    dry_run: bool = False,
    profile_path: Path | str | None = None,
) -> ScanResult:
    t0 = time.monotonic()
    result = ScanResult(dry_run=dry_run)

    jobs, reports = _fetch_companies(http, config, only)
    result.reports.extend(reports)
    feed_jobs, feed_reports = _fetch_feeds(http, config, only)
    jobs.extend(feed_jobs)
    result.reports.extend(feed_reports)
    result.fetched = len(jobs)

    generated_at = store.now_iso()

    # --only with no matching source name is a user error, not a failed scan
    if only and result.total_sources == 0:
        result.nothing_selected = True
        result.duration_s = round(time.monotonic() - t0, 2)
        log.warning("no source matched --only %s; nothing fetched, nothing written", sorted(only))
        return result

    if not any(r.ok for r in result.reports):
        result.all_failed = True
        result.duration_s = round(time.monotonic() - t0, 2)
        if not dry_run:
            data_dir.mkdir(parents=True, exist_ok=True)
            store.write_status(
                data_dir / "status.json",
                result.reports,
                generated_at,
                filtered_out=0,
                merged_duplicates=0,
                note="Every source failed this run -- previous jobs.json left untouched.",
            )
        log.warning("All sources failed this run. Leaving previous jobs.json in place.")
        return result

    rules = RuleSet.from_config(config)
    now = datetime.now(timezone.utc)
    matched: list[Job] = []
    filtered_out = 0
    for job in jobs:
        # defensive: no source shape may crash the pipeline mid-scan
        job.location = coerce_location(job.location)
        verdict = evaluate(job, rules, now=now)
        if not verdict.keep:
            filtered_out += 1
            continue
        job.match_reasons = verdict.reasons
        job.score = verdict.score
        job.is_senior = verdict.is_senior
        matched.append(job)
    result.filtered_out = filtered_out

    merged, n_merged, id_groups = merge_duplicates(matched)
    result.merged_duplicates = n_merged

    # Relevance runs after keyword matching and dedupe, on the survivors.
    # Anything blocked here is recorded rather than dropped, so the dashboard
    # can say "this one is not eligible, and here is why" instead of silently
    # making it disappear -- a job you were rejected from for a stated reason is
    # information, and a job that vanished is not.
    if profile_path is not None:
        try:
            profile = Profile.from_dict(json.loads(Path(profile_path).read_text(encoding="utf-8")))
            for job in merged:
                # named apart from the keyword `verdict` above: these are two
                # different scoring systems, and `verdict =` for both would
                # read as though they were one
                fit = rank(job, profile, now)
                job.relevance = {
                    "score": fit.score,
                    "eligible": fit.keep,
                    "reasons": fit.reasons,
                    "blockers": fit.blockers,
                    "skill_hits": fit.skill_hits,
                    "skill_gaps": fit.skill_gaps,
                }
        except (OSError, ValueError) as e:
            log.warning("relevance profile unusable (%s); continuing without it", e)

    # first_seen bookkeeping. Every member of a merged group shares the
    # group's earliest stamp, so a job that only exists as a duplicate
    # (or flips between board and aggregator) keeps its original
    # first_seen instead of showing up as new again.
    seen = store.load_seen(data_dir / "seen.json")
    for group in id_groups:
        stamps = [seen[i] for i in group if i in seen]
        earliest = min(stamps) if stamps else generated_at
        for job_id in group:
            seen[job_id] = earliest
    for job in merged:
        job.first_seen = seen[job.id]
    live_ids = {job_id for group in id_groups for job_id in group}
    pruned = {k: v for k, v in seen.items() if k in live_ids}
    if len(pruned) != len(seen):
        log.info("pruned %d stale seen[] entries", len(seen) - len(pruned))

    # order: best match first; ties broken by most recently discovered
    merged.sort(key=lambda j: j.first_seen or "", reverse=True)
    merged.sort(key=lambda j: j.score, reverse=True)

    result.jobs = merged
    result.duration_s = round(time.monotonic() - t0, 2)

    # partial scans (--only) are previews: writing them would replace the
    # canonical data with one source's slice and prune everyone else's
    # first_seen history.
    persist = not dry_run and not only
    result.persisted = persist
    if persist:
        store.write_jobs(data_dir / "jobs.json", merged, generated_at)
        store.write_seen(data_dir / "seen.json", pruned)
        store.write_status(data_dir / "status.json", result.reports, generated_at, filtered_out, n_merged)

    return result
