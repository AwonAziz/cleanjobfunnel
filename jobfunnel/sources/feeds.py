"""Tier 2 aggregator fetchers: wide-net public APIs and feeds.

These catch roles at companies that are not on the Tier 1 list. They are
all public, documented-ish endpoints - no auth, no scraping behind
logins - and every one of them was verified against a live response
before being wired in (see tests/fixtures).
"""

from __future__ import annotations

import html
import re
from xml.etree import ElementTree

from ..defaults import FEED_DEFAULTS
from ..http import Http
from ..models import Job
from . import base

TIER = 2


def fetch_remoteok(http: Http) -> tuple[list[Job], str | None]:
    try:
        payload = http.get_json("https://remoteok.com/api")
    except Exception as e:  # noqa: BLE001
        return [], str(e)

    if not isinstance(payload, list):
        return [], "unexpected payload shape (expected a list)"

    jobs = []
    for j in payload:
        title = base.get_first(j, "position", "title")
        if not title:
            continue  # skips RemoteOK's leading legal/meta entry
        jobs.append(
            Job(
                id=base.stable_id("remoteok", base.get_first(j, "id", "slug", "url", default=title)),
                title=title,
                company=base.get_first(j, "company", "company_name", default="Unknown"),
                location=base.get_first(j, "location", default="Remote"),
                url=base.get_first(j, "url", "apply_url"),
                source="remoteok",
                tier=TIER,
                group=None,
                posted_at=base.to_iso(base.get_first(j, "date", "epoch")),
                salary=base.format_salary(j.get("salary_min"), j.get("salary_max")),
                tags=base.clean_tags(j.get("tags")),
            )
        )
    return jobs, None


def fetch_remotive(
    http: Http, category: str = FEED_DEFAULTS["remotive"]["category"]
) -> tuple[list[Job], str | None]:
    try:
        payload = http.get_json(
            "https://remotive.com/api/remote-jobs", params={"category": category, "limit": 200}
        )
    except Exception as e:  # noqa: BLE001
        return [], str(e)

    jobs = []
    for j in payload.get("jobs", []) or []:
        title = base.get_first(j, "title")
        if not title:
            continue
        tags = base.clean_tags(j.get("tags"))
        if j.get("job_type"):
            tags.append(str(j["job_type"]).replace("_", " "))
        jobs.append(
            Job(
                id=base.stable_id("remotive", j.get("id") or j.get("url")),
                title=title,
                company=base.get_first(j, "company_name", default="Unknown"),
                location=base.get_first(j, "candidate_required_location", default="Remote"),
                url=base.get_first(j, "url"),
                source="remotive",
                tier=TIER,
                group=None,
                posted_at=base.to_iso(base.get_first(j, "publication_date")),
                salary=base.get_first(j, "salary") or None,
                tags=tags,
            )
        )
    return jobs, None


def fetch_himalayas(http: Http, limit: int = 200) -> tuple[list[Job], str | None]:
    try:
        payload = http.get_json("https://himalayas.app/jobs/api", params={"limit": limit})
    except Exception as e:  # noqa: BLE001
        return [], str(e)

    jobs = []
    for j in payload.get("jobs", []) or []:
        title = base.get_first(j, "title")
        if not title:
            continue
        restrictions = j.get("locationRestrictions") or []
        location = ", ".join(str(r) for r in restrictions) if restrictions else "Worldwide"
        tags = base.clean_tags(j.get("categories"))
        if j.get("seniority"):
            tags.extend(base.clean_tags(j["seniority"]))
        jobs.append(
            Job(
                id=base.stable_id("himalayas", j.get("guid") or j.get("applicationLink") or title),
                title=title,
                company=base.get_first(j, "companyName", default="Unknown"),
                location=location,
                url=base.get_first(j, "applicationLink"),
                source="himalayas",
                tier=TIER,
                group=None,
                posted_at=base.to_iso(j.get("pubDate")),
                salary=base.format_salary(
                    j.get("minSalary"), j.get("maxSalary"), j.get("currency"), j.get("salaryPeriod")
                ),
                tags=tags,
            )
        )
    return jobs, None


def fetch_workingnomads(http: Http) -> tuple[list[Job], str | None]:
    try:
        payload = http.get_json("https://www.workingnomads.com/api/exposed_jobs/")
    except Exception as e:  # noqa: BLE001
        return [], str(e)

    if not isinstance(payload, list):
        return [], "unexpected payload shape (expected a list)"

    jobs = []
    for j in payload:
        title = base.get_first(j, "title")
        if not title:
            continue
        jobs.append(
            Job(
                id=base.stable_id("workingnomads", j.get("url") or title),
                title=title,
                company=base.get_first(j, "company_name", default="Unknown"),
                location=base.get_first(j, "location", default="Remote"),
                url=base.get_first(j, "url"),
                source="workingnomads",
                tier=TIER,
                group=None,
                posted_at=base.to_iso(j.get("pub_date")),
                tags=base.clean_tags(j.get("tags")) + base.clean_tags(j.get("category_name")),
            )
        )
    return jobs, None


WWR_FEEDS = [
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
]


def fetch_rss(url: str, source_name: str, http: Http) -> tuple[list[Job], str | None]:
    try:
        text = http.get_text(url)
        root = ElementTree.fromstring(text)
    except Exception as e:  # noqa: BLE001
        return [], str(e)

    jobs = []
    for item in root.iter("item"):
        raw_title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = item.findtext("pubDate")
        if not raw_title or not link:
            continue
        company, sep, rest = raw_title.partition(":")
        company, title = (company.strip(), rest.strip()) if sep else ("Unknown", raw_title)
        jobs.append(
            Job(
                id=base.stable_id(source_name, link),
                title=title,
                company=company,
                location="Remote",
                url=link,
                source=source_name,
                tier=TIER,
                group=None,
                posted_at=base.to_iso(pub_date),
            )
        )
    return jobs, None


def fetch_weworkremotely(http: Http) -> tuple[list[Job], str | None]:
    jobs: list[Job] = []
    errors: list[str] = []
    for url in WWR_FEEDS:
        feed_jobs, err = fetch_rss(url, "weworkremotely", http)
        if err:
            errors.append(f"{url.rsplit('/', 1)[-1]}: {err}")
        jobs.extend(feed_jobs)
    # partial success is success - only report an error if we got nothing at all
    return jobs, ("; ".join(errors) if errors else None) if not jobs else None


# --- Hacker News "Who is hiring?" ------------------------------------------

_HN_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
_HN_ITEM = "https://hn.algolia.com/api/v1/items/{id}"
_HN_MONTHLY = re.compile(r"^Ask HN: Who is hiring\??\s*\((\w+)\s+(\d{4})\)", re.IGNORECASE)
# pipe-separated fields that are employment-type noise, not locations
_HN_NON_LOCATION = {
    "full time",
    "full-time",
    "fulltime",
    "part time",
    "part-time",
    "parttime",
    "contract",
    "contractor",
    "intern",
    "internship",
    "onsite",
    "on-site",
    "hybrid",
    "visa",
    "sponsorship",
    "email",
    "apply",
}


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def _hn_location(title: str, rest: list[str]) -> str:
    """Best-effort location extraction from 'Company | Role | Location | ...' posts."""
    m = re.search(
        r"\(([^)]*(?:remote|onsite|berlin|london|york|eu\b|usa?|apac|emea|worldwide)[^)]*)\)",
        title,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    for cand in rest:
        if cand.lower() not in _HN_NON_LOCATION:
            return cand
    return "Remote (HN)"


def fetch_hn_whoshiring(
    http: Http, threads: int = FEED_DEFAULTS["hn_whoshiring"]["threads"]
) -> tuple[list[Job], str | None]:
    """Latest monthly 'Ask HN: Who is hiring?' thread(s), via the Algolia API."""
    try:
        search = http.get_json(
            _HN_SEARCH, params={"query": "Ask HN: Who is hiring", "tags": "story", "hitsPerPage": 50}
        )
        story_ids: list[str] = []
        for hit in search.get("hits", []):
            if _HN_MONTHLY.match(hit.get("title") or ""):
                story_ids.append(hit["objectID"])
            if len(story_ids) >= max(1, threads):
                break
        if not story_ids:
            return [], "no monthly 'Who is hiring' thread found"

        jobs: list[Job] = []
        for story_id in story_ids:
            item = http.get_json(_HN_ITEM.format(id=story_id))
            for kid in item.get("children", []) or []:
                text = kid.get("text") or ""
                if not text or kid.get("type") != "comment":
                    continue
                first_line = _strip_html(text.split("\n", 1)[0])
                if not first_line or first_line.lower().startswith(("seeking", "seeker")):
                    continue  # job seekers post in the same thread
                parts = [p.strip() for p in first_line.split("|") if p.strip()]
                if len(parts) < 2:
                    continue  # not the conventional "Company | Role | ..." format
                company, title = parts[0], parts[1]
                location = _hn_location(title, parts[2:])
                jobs.append(
                    Job(
                        id=base.stable_id("hn_whoshiring", kid.get("id")),
                        title=title[:200],
                        company=company[:120],
                        location=location[:200],
                        url=f"https://news.ycombinator.com/item?id={kid.get('id')}",
                        source="hn_whoshiring",
                        tier=TIER,
                        group=None,
                        posted_at=base.to_iso(kid.get("created_at")),
                        tags=["hacker-news"],
                    )
                )
        return jobs, None
    except Exception as e:  # noqa: BLE001
        return [], str(e)
