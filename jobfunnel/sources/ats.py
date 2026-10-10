"""Tier 1 ATS fetchers: the company's own job board API.

Each fetcher takes the shared Http client plus a Company entry and returns
(jobs, error). Fetch failures are reported per-source and never raise:
one dead board must not take down a whole scan.
"""

from __future__ import annotations

from ..config import Company
from ..http import Http
from ..models import Job
from . import base


def fetch_greenhouse(http: Http, company: Company) -> tuple[list[Job], str | None]:
    token = company.token
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    try:
        payload = http.get_json(url, params={"content": "true"})
    except Exception as e:  # noqa: BLE001 - per-source failure must not raise
        return [], str(e)

    jobs = []
    for j in payload.get("jobs", []):
        title = base.get_first(j, "title")
        if not title:
            continue
        jobs.append(
            Job(
                id=base.stable_id("greenhouse", token, j.get("id")),
                title=title,
                company=company.name,
                location=base.get_first(j.get("location") or {}, "name"),
                url=base.get_first(j, "absolute_url"),
                source="greenhouse",
                tier=1,
                group=company.group,
                posted_at=base.to_iso(base.get_first(j, "first_published", "updated_at")),
            )
        )
    return jobs, None


def fetch_lever(http: Http, company: Company) -> tuple[list[Job], str | None]:
    token = company.token
    url = f"https://api.lever.co/v0/postings/{token}"
    try:
        payload = http.get_json(url, params={"mode": "json"})
    except Exception as e:  # noqa: BLE001
        return [], str(e)

    if not isinstance(payload, list):
        return [], "unexpected payload shape (expected a list)"

    jobs = []
    for j in payload:
        title = base.get_first(j, "text", "title")
        if not title:
            continue
        cats = j.get("categories") or {}
        location = base.get_first(cats, "location") or base.get_first(j, "workplaceType")
        salary = base.get_first(j.get("salary") or {}, "range", "description")
        jobs.append(
            Job(
                id=base.stable_id("lever", token, j.get("id")),
                title=title,
                company=company.name,
                location=location,
                url=base.get_first(j, "hostedUrl", "applyUrl"),
                source="lever",
                tier=1,
                group=company.group,
                posted_at=base.to_iso(base.get_first(j, "createdAt")),
                salary=str(salary) if salary else None,
                tags=base.clean_tags(cats.get("team") or cats.get("commitment")),
            )
        )
    return jobs, None


def fetch_ashby(http: Http, company: Company) -> tuple[list[Job], str | None]:
    token = company.token
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}"
    try:
        payload = http.get_json(url)
    except Exception as e:  # noqa: BLE001
        return [], str(e)

    if isinstance(payload, list):
        listings = payload
    elif isinstance(payload, dict):
        listings = payload.get("jobs") or []
    else:
        listings = []
    if not isinstance(listings, list):
        return [], "unexpected payload shape (expected a list of jobs)"

    jobs = []
    for j in listings:
        title = base.get_first(j, "title", "jobTitle")
        if not title:
            continue
        secondary = [s.get("location") for s in j.get("secondaryLocations", []) if isinstance(s, dict)]
        location = base.get_first(j, "location", "locationName") or (secondary[0] if secondary else None)
        if not location and str(j.get("workplaceType") or "").lower() == "remote":
            location = "Remote"
        tags = base.clean_tags(j.get("employmentType"))
        for extra in (j.get("department"), j.get("team")):
            if extra and str(extra) not in tags:
                tags.append(str(extra))
        jobs.append(
            Job(
                id=base.stable_id("ashby", token, base.get_first(j, "id", "jobId", default=title)),
                title=title,
                company=company.name,
                location=location,
                url=base.get_first(j, "jobUrl", "applyUrl", "postingUrl"),
                source="ashby",
                tier=1,
                group=company.group,
                posted_at=base.to_iso(base.get_first(j, "publishedAt", "publishedDate")),
                tags=tags,
            )
        )
    return jobs, None


def fetch_smartrecruiters(http: Http, company: Company) -> tuple[list[Job], str | None]:
    token = company.token
    url = f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
    try:
        payload = http.get_json(url)
    except Exception as e:  # noqa: BLE001
        return [], str(e)

    jobs = []
    for j in payload.get("content", []) or []:
        title = base.get_first(j, "name", "title")
        if not title:
            continue
        loc_obj = j.get("location") or {}
        loc = (
            ", ".join(filter(None, [loc_obj.get("city"), loc_obj.get("region"), loc_obj.get("country")]))
            or None
        )
        if loc_obj.get("remote"):
            loc = f"Remote ({loc})" if loc else "Remote"
        # the postings list carries no URL; SmartRecruiters links are canonical
        url = (
            base.get_first(j, "postingUrl", "applyUrl")
            or f"https://jobs.smartrecruiters.com/{token}/{j.get('id')}"
        )
        jobs.append(
            Job(
                id=base.stable_id("smartrecruiters", token, j.get("id")),
                title=title,
                company=company.name,
                location=loc,
                url=url,
                source="smartrecruiters",
                tier=1,
                group=company.group,
                posted_at=base.to_iso(base.get_first(j, "releasedDate", "createdOn")),
            )
        )
    return jobs, None


def fetch_recruitee(http: Http, company: Company) -> tuple[list[Job], str | None]:
    """Recruitee Careers Site API - https://<company>.recruitee.com/api/offers/"""
    token = company.token
    url = f"https://{token}.recruitee.com/api/offers/"
    try:
        payload = http.get_json(url)
    except Exception as e:  # noqa: BLE001
        return [], str(e)

    offers = payload.get("offers", []) if isinstance(payload, dict) else []
    jobs = []
    for j in offers:
        title = base.get_first(j, "title")
        if not title:
            continue
        location = base.get_first(j, "location") or base.get_first(j, "locations")
        if isinstance(location, list):
            location = ", ".join(str(x) for x in location if x)
        if j.get("remote") and location and "remote" not in str(location).lower():
            location = f"Remote ({location})"
        tags = base.clean_tags(j.get("department"))
        if j.get("employment_type_code"):
            tags.append(str(j["employment_type_code"]))
        # Recruitee's salary is an object {min, max, currency, period} - normalize
        # it here so it never leaks as a raw dict into jobs.json / the dashboard
        salary = base.get_first(j, "salary")
        if isinstance(salary, dict):
            salary = base.format_salary(
                salary.get("min"), salary.get("max"), salary.get("currency"), salary.get("period")
            )
        elif salary:
            salary = str(salary)
        jobs.append(
            Job(
                id=base.stable_id("recruitee", token, j.get("id")),
                title=title,
                company=company.name,
                location=str(location) if location else None,
                url=base.get_first(j, "careers_url", "careers_apply_url"),
                source="recruitee",
                tier=1,
                group=company.group,
                posted_at=base.to_iso(base.get_first(j, "published_at", "created_at")),
                salary=salary,
                tags=tags,
            )
        )
    return jobs, None


def _breezy_location(j: dict) -> str | None:
    """Breezy's location field is either a string or a structured object.

    The generic object -> text conversion is delegated to base.coerce_location
    so every source normalizes structured locations the same way; this wrapper
    only adds breezy's list handling and its is_remote/remote_details flavor.
    """
    candidates = [j.get("location")]
    if isinstance(j.get("locations"), list):
        candidates.extend(j["locations"])
    for loc in candidates:
        if isinstance(loc, str) and loc.strip():
            return loc.strip()
        if isinstance(loc, dict):
            text = base.coerce_location(loc)
            if loc.get("is_remote"):
                remote = (loc.get("remote_details") or {}).get("label") or "Remote"
                return f"Remote - {text}" if text else str(remote)
            return text
    return None


def fetch_breezy(http: Http, company: Company) -> tuple[list[Job], str | None]:
    """Breezy HR board feed - https://<company>.breezy.hr/json (bare list)."""
    token = company.token
    url = f"https://{token}.breezy.hr/json"
    try:
        payload = http.get_json(url)
    except Exception as e:  # noqa: BLE001
        return [], str(e)

    if not isinstance(payload, list):
        return [], "unexpected payload shape (expected a list)"

    jobs = []
    for j in payload:
        title = base.get_first(j, "name", "title")
        if not title:
            continue
        jobs.append(
            Job(
                id=base.stable_id("breezy", token, base.get_first(j, "friendly_id", "id", default=title)),
                title=title,
                company=company.name,
                location=_breezy_location(j),
                url=base.get_first(j, "url", "apply_url"),
                source="breezy",
                tier=1,
                group=company.group,
                posted_at=base.to_iso(base.get_first(j, "published_date")),
                salary=base.get_first(j, "salary"),
                tags=base.clean_tags(base.get_first(j, "type"))
                + base.clean_tags(base.get_first(j, "department")),
            )
        )
    return jobs, None


def fetch_personio(http: Http, company: Company) -> tuple[list[Job], str | None]:
    """Personio public XML feed - https://<company>.jobs.personio.de/xml."""
    from xml.etree import ElementTree

    token = company.token
    url = f"https://{token}.jobs.personio.de/xml"
    try:
        text = http.get_text(url)
        root = ElementTree.fromstring(text)
    except Exception as e:  # noqa: BLE001
        return [], str(e)

    jobs = []
    for pos in root.findall("position"):
        title = (pos.findtext("name") or "").strip()
        if not title:
            continue
        pid = (pos.findtext("id") or title).strip()
        offices = [(o.text or "").strip() for o in pos.iter("office")]
        location = ", ".join(o for o in offices if o) or None
        tags = base.clean_tags(pos.findtext("employmentType")) + base.clean_tags(pos.findtext("seniority"))
        if pos.findtext("department"):
            tags.append(pos.findtext("department").strip())
        jobs.append(
            Job(
                id=base.stable_id("personio", token, pid),
                title=title,
                company=(pos.findtext("subcompany") or company.name).strip() or company.name,
                location=location,
                url=f"https://{token}.jobs.personio.de/job/{pid}",
                source="personio",
                tier=1,
                group=company.group,
                posted_at=base.to_iso(pos.findtext("createdAt")),
                tags=tags,
            )
        )
    return jobs, None
