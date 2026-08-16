#!/usr/bin/env python3
"""
Job funnel scanner.

Pulls open roles from direct ATS APIs (Greenhouse / Lever / Ashby /
SmartRecruiters) and aggregator feeds (RemoteOK / We Work Remotely /
Remotive), filters to the roles and locations in config/companies.json,
dedupes against the previous run, and writes docs/data/jobs.json for
the static dashboard.

Safe to run repeatedly: if every single source fails (e.g. no network),
the previous jobs.json is left untouched rather than wiped.
"""

import json
import time
import hashlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree

import requests

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "companies.json"
DATA_DIR = ROOT / "docs" / "data"
JOBS_PATH = DATA_DIR / "jobs.json"
SEEN_PATH = DATA_DIR / "seen.json"
STATUS_PATH = DATA_DIR / "status.json"

USER_AGENT = "job-funnel/1.0 (+https://github.com/AwonAziz; personal job search tool)"
TIMEOUT = 20
POLITE_DELAY = 0.3  # seconds between requests to the same-ish kind of host

RSS_FEEDS = [
    {"name": "weworkremotely", "url": "https://weworkremotely.com/categories/remote-programming-jobs.rss"},
    {"name": "weworkremotely", "url": "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss"},
    {"name": "remotive", "url": "https://remotive.com/feed"},
]


# ---------------------------------------------------------------- helpers --

def get_first(d, *keys, default=None):
    """Return the first present, non-empty value among keys in dict d."""
    if not isinstance(d, dict):
        return default
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return default


def stable_id(*parts):
    raw = "|".join(str(p) for p in parts if p is not None)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def to_iso(dt_value):
    """Best-effort normalize epoch/ISO/RFC-822 dates to an ISO 8601 UTC string."""
    if dt_value is None:
        return None

    if isinstance(dt_value, (int, float)):
        ts = dt_value / 1000 if dt_value > 10_000_000_000 else dt_value
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except (ValueError, OSError, OverflowError):
            return None

    if isinstance(dt_value, str):
        s = dt_value.strip()
        if not s:
            return None
        try:  # RFC 822, e.g. RSS <pubDate>
            parsed = parsedate_to_datetime(s)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            pass
        try:  # ISO 8601
            parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            return None

    return None


def matches_keywords(title, keywords):
    t = (title or "").lower()
    return any(k.lower() in t for k in keywords)


def matches_location(location_text, allow_list):
    if not location_text:
        return True  # ambiguous -> include; don't silently drop a possible match
    loc = location_text.lower()
    return any(a in loc for a in allow_list)


def flag_senior(title, flags):
    t = (title or "").lower()
    return any(f.lower().strip() in t for f in flags)


# --------------------------------------------------------- Tier 1: direct --

def fetch_greenhouse(company):
    token = company["token"]
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        r.raise_for_status()
        payload = r.json()
    except (requests.RequestException, ValueError) as e:
        return [], str(e)

    jobs = []
    for j in payload.get("jobs", []):
        title = get_first(j, "title")
        if not title:
            continue
        jobs.append({
            "id": stable_id("greenhouse", token, j.get("id")),
            "title": title,
            "company": company["name"],
            "location": get_first(j.get("location") or {}, "name"),
            "url": get_first(j, "absolute_url"),
            "source": "greenhouse",
            "tier": 1,
            "group": company.get("group"),
            "posted_at": to_iso(get_first(j, "first_published", "updated_at")),
        })
    return jobs, None


def fetch_lever(company):
    token = company["token"]
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        r.raise_for_status()
        payload = r.json()
    except (requests.RequestException, ValueError) as e:
        return [], str(e)

    if not isinstance(payload, list):
        return [], "unexpected payload shape (expected a list)"

    jobs = []
    for j in payload:
        title = get_first(j, "text", "title")
        if not title:
            continue
        cats = j.get("categories") or {}
        location = get_first(cats, "location") or get_first(j, "workplaceType")
        jobs.append({
            "id": stable_id("lever", token, j.get("id")),
            "title": title,
            "company": company["name"],
            "location": location,
            "url": get_first(j, "hostedUrl", "applyUrl"),
            "source": "lever",
            "tier": 1,
            "group": company.get("group"),
            "posted_at": to_iso(get_first(j, "createdAt")),
        })
    return jobs, None


def fetch_ashby(company):
    token = company["token"]
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=false"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        r.raise_for_status()
        payload = r.json()
    except (requests.RequestException, ValueError) as e:
        return [], str(e)

    if isinstance(payload, list):
        listings = payload
    elif isinstance(payload, dict):
        listings = payload.get("jobs") or payload.get("jobPostings") or []
    else:
        listings = []

    jobs = []
    for j in listings:
        title = get_first(j, "title", "jobTitle")
        if not title:
            continue
        jobs.append({
            "id": stable_id("ashby", token, get_first(j, "id", "jobId", default=title)),
            "title": title,
            "company": company["name"],
            "location": get_first(j, "location", "locationName", "addressLocality"),
            "url": get_first(j, "jobUrl", "applyUrl", "postingUrl"),
            "source": "ashby",
            "tier": 1,
            "group": company.get("group"),
            "posted_at": to_iso(get_first(j, "publishedAt", "publishedDate")),
        })
    return jobs, None


def fetch_smartrecruiters(company):
    token = company["token"]
    url = f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        r.raise_for_status()
        payload = r.json()
    except (requests.RequestException, ValueError) as e:
        return [], str(e)

    jobs = []
    for j in payload.get("content", []) or []:
        title = get_first(j, "name", "title")
        if not title:
            continue
        loc_obj = j.get("location") or {}
        loc = ", ".join(filter(None, [loc_obj.get("city"), loc_obj.get("country")])) or None
        if loc_obj.get("remote"):
            loc = f"Remote ({loc})" if loc else "Remote"
        jobs.append({
            "id": stable_id("smartrecruiters", token, j.get("id")),
            "title": title,
            "company": company["name"],
            "location": loc,
            "url": get_first(j, "postingUrl", "applyUrl"),
            "source": "smartrecruiters",
            "tier": 1,
            "group": company.get("group"),
            "posted_at": to_iso(get_first(j, "releasedDate", "createdOn")),
        })
    return jobs, None


ATS_FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
}


# ----------------------------------------------------- Tier 2: aggregators --

def fetch_remoteok():
    try:
        r = requests.get("https://remoteok.com/api", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        r.raise_for_status()
        payload = r.json()
    except (requests.RequestException, ValueError) as e:
        return [], str(e)

    jobs = []
    for j in payload:
        title = get_first(j, "position", "title")
        if not title:
            continue  # skips RemoteOK's leading legal/meta entry
        jobs.append({
            "id": stable_id("remoteok", get_first(j, "id", "slug", default=title)),
            "title": title,
            "company": get_first(j, "company", default="Unknown"),
            "location": get_first(j, "location", default="Remote"),
            "url": get_first(j, "url", "apply_url"),
            "source": "remoteok",
            "tier": 2,
            "group": None,
            "posted_at": to_iso(get_first(j, "date")),
        })
    return jobs, None


def fetch_rss(url, source_name):
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        r.raise_for_status()
        root = ElementTree.fromstring(r.content)
    except (requests.RequestException, ElementTree.ParseError) as e:
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
        jobs.append({
            "id": stable_id(source_name, link),
            "title": title,
            "company": company,
            "location": "Remote",
            "url": link,
            "source": source_name,
            "tier": 2,
            "group": None,
            "posted_at": to_iso(pub_date),
        })
    return jobs, None


# -------------------------------------------------------------- orchestrate --

def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return default
    return default


def main():
    config = json.loads(CONFIG_PATH.read_text())
    seen = load_json(SEEN_PATH, {})  # id -> first_seen iso string

    all_jobs = []
    source_report = []

    for company in config["companies"]:
        fetcher = ATS_FETCHERS.get(company["ats"])
        if not fetcher:
            source_report.append({"source": company["name"], "ok": False, "count": 0, "error": f"unknown ats '{company['ats']}'"})
            continue
        jobs, err = fetcher(company)
        source_report.append({"source": company["name"], "ok": err is None, "count": len(jobs), "error": err})
        all_jobs.extend(jobs)
        time.sleep(POLITE_DELAY)

    ok_jobs, err = fetch_remoteok()
    source_report.append({"source": "RemoteOK", "ok": err is None, "count": len(ok_jobs), "error": err})
    all_jobs.extend(ok_jobs)
    time.sleep(POLITE_DELAY)

    for feed in RSS_FEEDS:
        jobs, err = fetch_rss(feed["url"], feed["name"])
        label = f"{feed['name']} ({feed['url'].rsplit('/', 1)[-1]})"
        source_report.append({"source": label, "ok": err is None, "count": len(jobs), "error": err})
        all_jobs.extend(jobs)
        time.sleep(POLITE_DELAY)

    now_iso = datetime.now(timezone.utc).isoformat()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    successes = sum(1 for s in source_report if s["ok"])
    if successes == 0:
        STATUS_PATH.write_text(json.dumps({
            "generated_at": now_iso,
            "sources": source_report,
            "note": "Every source failed this run -- previous jobs.json left untouched.",
        }, indent=2))
        print("All sources failed this run. Leaving previous jobs.json in place.")
        for s in source_report:
            print(f"  - {s['source']}: {s['error']}")
        return

    keywords = config["role_keywords"]
    location_allow = config["location_allow"]
    seniority_flags = config["seniority_flags"]

    filtered = []
    for job in all_jobs:
        if not matches_keywords(job["title"], keywords):
            continue
        if not matches_location(job.get("location"), location_allow):
            continue
        job["is_senior"] = flag_senior(job["title"], seniority_flags)
        job_id = job["id"]
        job["first_seen"] = seen.get(job_id, now_iso)
        seen[job_id] = job["first_seen"]
        filtered.append(job)

    # prune seen[] entries for jobs no longer present (closed / filled / expired)
    live_ids = {j["id"] for j in filtered}
    seen = {k: v for k, v in seen.items() if k in live_ids}

    filtered.sort(key=lambda j: j["first_seen"], reverse=True)

    JOBS_PATH.write_text(json.dumps({"generated_at": now_iso, "count": len(filtered), "jobs": filtered}, indent=2))
    SEEN_PATH.write_text(json.dumps(seen, indent=2))
    STATUS_PATH.write_text(json.dumps({"generated_at": now_iso, "sources": source_report}, indent=2))

    failed = [s for s in source_report if not s["ok"]]
    print(f"Wrote {len(filtered)} matching jobs from {successes}/{len(source_report)} sources.")
    if failed:
        print(f"{len(failed)} source(s) need attention (see README > Finding a company's token):")
        for s in failed:
            print(f"  - {s['source']}: {s['error']}")


if __name__ == "__main__":
    main()
