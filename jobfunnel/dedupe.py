"""Cross-source duplicate merging.

The same role routinely shows up on the company's own board *and* on an
aggregator. Jobs are grouped when either
  a) the (company, title) fingerprint matches AND they are plausibly the
     same posting - cross-source always qualifies, same-source pairs must
     also share a location (otherwise "Platform Engineer (Remote - EMEA)"
     and "(Remote - APAC)" would collapse into one card), or
  b) the normalised apply URL matches,
whichever is true first - a small union-find handles both. The best copy
wins (Tier 1 over Tier 2, then higher match score), and the other sources
are recorded in `also_on` instead of being thrown away.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .models import Job

_COMPANY_SUFFIXES = (
    "inc",
    "llc",
    "ltd",
    "gmbh",
    "co",
    "corp",
    "corporation",
    "kg",
    "ag",
    "sa",
    "bv",
    "plc",
    "labs",
)


def normalize_company(name: str | None) -> str:
    if not name:
        return ""
    s = re.sub(r"[^a-z0-9 ]+", " ", name.lower())
    words = [w for w in s.split() if w and w not in _COMPANY_SUFFIXES]
    return " ".join(words)


def normalize_title(title: str | None) -> str:
    if not title:
        return ""
    s = title.lower()
    s = re.sub(r"\([^)]*\)", " ", s)  # drop "(Remote)", "(Berlin)", ...
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(s.split())


def normalize_location(location: str | None) -> str:
    if not location:
        return ""
    return re.sub(r"[^a-z0-9]+", "", location.lower())


def _normalize_url(url: str | None) -> str | None:
    if not url or not url.startswith(("http://", "https://")):
        return None
    p = urlparse(url)
    host = (p.netloc or "").lower()
    host = re.sub(r"^(www|apply|jobs|job-boards|boards)\.", "", host)
    path = re.sub(r"/+$", "", p.path or "")
    if not host or not path:
        return None
    return f"{host}{path}"


def fingerprint(job: Job) -> str:
    return f"{normalize_company(job.company)}|{normalize_title(job.title)}"


class _UF:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _primary_key(job: Job) -> tuple[int, int, str]:
    # lower tier first (company board beats aggregator), then higher score,
    # then a stable tiebreaker
    return (job.tier, -job.score, job.id or "")


def _plausibly_same_posting(a: Job, b: Job) -> bool:
    """Cross-source copies are assumed identical; same-source pairs must share a location."""
    if a.source != b.source:
        return True
    loc = normalize_location(a.location)
    return bool(loc) and loc == normalize_location(b.location)


def merge_duplicates(jobs: list[Job]) -> tuple[list[Job], int, list[list[str]]]:
    """Merge duplicates; returns (merged_jobs, n_merged_away, id_groups).

    id_groups lists every group (primary id first) so callers can preserve
    bookkeeping - e.g. the earliest first_seen across the whole group.
    """
    if not jobs:
        return [], 0, []

    uf = _UF()
    by_fp: dict[str, str] = {}
    by_url: dict[str, str] = {}
    nodes: dict[str, Job] = {}

    for i, job in enumerate(jobs):
        node = f"j{i}"
        nodes[node] = job
        uf.find(node)

        fp = fingerprint(job)
        if fp and fp != "|":
            if fp in by_fp:
                if _plausibly_same_posting(nodes[by_fp[fp]], job):
                    uf.union(by_fp[fp], node)
            else:
                by_fp[fp] = node

        url_key = _normalize_url(job.url)
        if url_key:
            if url_key in by_url:
                uf.union(by_url[url_key], node)
            else:
                by_url[url_key] = node

    groups: dict[str, list[Job]] = {}
    for node, job in nodes.items():
        groups.setdefault(uf.find(node), []).append(job)

    merged: list[Job] = []
    id_groups: list[list[str]] = []
    for group in groups.values():
        ordered = sorted(group, key=_primary_key)
        primary, others = ordered[0], ordered[1:]
        seen_sources = {primary.source}
        for o in others:
            if o.source not in seen_sources:
                primary.also_on.append(o.source)
                seen_sources.add(o.source)
            # enrich the primary copy when the duplicate knows more
            if not primary.salary and o.salary:
                primary.salary = o.salary
            if not primary.posted_at and o.posted_at:
                primary.posted_at = o.posted_at
            for tag in o.tags:
                if tag not in primary.tags:
                    primary.tags.append(tag)
        primary.also_on.sort()
        merged.append(primary)
        id_groups.append([o.id for o in ordered])

    n_merged = sum(len(g) - 1 for g in groups.values() if len(g) > 1)
    return merged, n_merged, id_groups
