"""Source fetchers - every one against a real captured payload."""

from __future__ import annotations

from pathlib import Path

from jobfunnel.config import Company
from jobfunnel.sources import ats, feeds
from jobfunnel.sources.base import to_iso

from .conftest import load_fixture

REMOTEOK_PAYLOAD = [
    {"legal": "notice, not a job"},
    {
        "id": "123",
        "position": "MLOps Engineer",
        "company": "Acme Remote",
        "location": "Worldwide",
        "url": "https://remoteok.com/remote-jobs/123",
        "date": "2026-08-15T00:00:00",
        "salary_min": 120000,
        "salary_max": 160000,
        "tags": ["python", "kubernetes"],
    },
    {
        "id": "124",
        "position": "Platform Engineer",
        "company": "Acme Remote",
        "location": "Worldwide",
        "url": "https://remoteok.com/remote-jobs/124",
        "date": "2026-08-15T00:00:00",
    },
]

RSS_XML = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>Acme Corp: Senior MLOps Engineer</title>
  <link>https://weworkremotely.com/remote-jobs/acme-senior-mlops-engineer</link>
  <pubDate>Fri, 14 Aug 2026 12:00:00 +0000</pubDate>
</item>
<item>
  <title>Untitled Listing With No Colon</title>
  <link>https://weworkremotely.com/remote-jobs/untitled</link>
  <pubDate>Fri, 14 Aug 2026 11:00:00 +0000</pubDate>
</item>
</channel></rss>"""

RSS_XML_DEVOPS = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>Globex: Site Reliability Engineer</title>
  <link>https://weworkremotely.com/remote-jobs/globex-sre</link>
  <pubDate>Fri, 14 Aug 2026 13:00:00 +0000</pubDate>
</item>
</channel></rss>"""


def company(name="Acme", a="greenhouse", token="acme", group=None):
    return Company(name=name, ats=a, token=token, group=group, verified=True)


# ------------------------------------------------------------------ Tier 1 --


def test_greenhouse_against_real_payload(fake_http_factory):
    http = fake_http_factory({"boards-api.greenhouse.io": load_fixture("greenhouse_jobs.json")})
    jobs, err = ats.fetch_greenhouse(http, company())
    assert err is None
    assert len(jobs) == 2
    j = jobs[0]
    assert j.source == "greenhouse" and j.tier == 1
    assert j.company == "Acme"
    assert j.url and j.url.startswith("https://job-boards.greenhouse.io/")
    assert j.posted_at and to_iso(j.posted_at) is not None


def test_greenhouse_error_does_not_raise(fake_http_factory):
    http = fake_http_factory({}, error=RuntimeError("boom"))
    jobs, err = ats.fetch_greenhouse(http, company())
    assert jobs == [] and "boom" in err


def test_ashby_against_real_payload(fake_http_factory):
    http = fake_http_factory({"api.ashbyhq.com": load_fixture("ashby_jobs.json")})
    jobs, err = ats.fetch_ashby(http, company(a="ashby", token="modal"))
    assert err is None
    assert len(jobs) == 2
    assert jobs[0].title == "Member of Technical Staff - Systems"
    assert jobs[0].location == "New York"
    assert jobs[0].url.startswith("https://jobs.ashbyhq.com/modal/")
    assert "Engineering" in jobs[0].tags
    assert jobs[0].posted_at is not None


def test_ashby_handles_plain_list_shape(fake_http_factory):
    http = fake_http_factory(
        {
            "api.ashbyhq.com": [
                {
                    "id": "j2",
                    "title": "ML Platform Engineer",
                    "location": "Remote",
                    "applyUrl": "https://jobs.ashbyhq.com/acme/j2",
                    "publishedAt": "2026-08-13T00:00:00.000Z",
                }
            ]
        }
    )
    jobs, err = ats.fetch_ashby(http, company(a="ashby"))
    assert err is None
    assert jobs[0].url.endswith("/j2")


def test_lever_against_real_payload(fake_http_factory):
    # mistral's token resolves but the board currently has no open postings
    http = fake_http_factory({"api.lever.co": load_fixture("lever_jobs.json")})
    jobs, err = ats.fetch_lever(http, company(a="lever", token="mistral"))
    assert err is None
    assert jobs == []


def test_lever_parses_postings(fake_http_factory):
    payload = [
        {
            "id": "a1b2c3",
            "text": "Associate AI Engineer",
            "categories": {"team": "Engineering", "location": "Remote"},
            "hostedUrl": "https://jobs.lever.co/acme/a1b2c3",
            "workplaceType": "remote",
            "createdAt": 1755000000000,
        }
    ]
    http = fake_http_factory({"api.lever.co": payload})
    jobs, err = ats.fetch_lever(http, company(a="lever", token="acme"))
    assert err is None
    assert jobs[0].title == "Associate AI Engineer"
    assert jobs[0].location == "Remote"
    assert jobs[0].posted_at is not None
    assert jobs[0].tier == 1


def test_smartrecruiters_against_real_payload(fake_http_factory):
    http = fake_http_factory({"api.smartrecruiters.com": load_fixture("smartrecruiters_jobs.json")})
    jobs, err = ats.fetch_smartrecruiters(http, company(a="smartrecruiters", token="thales"))
    assert err is None
    assert len(jobs) == 2
    assert jobs[0].company == "Acme"
    assert jobs[0].url == "https://jobs.smartrecruiters.com/thales/743999690614888"
    assert jobs[0].tier == 1


def test_recruitee_salary_object_is_formatted(fake_http_factory):
    payload = {
        "offers": [
            {
                "id": 1,
                "title": "MLOps Engineer",
                "location": "Remote",
                "careers_url": "https://jobs.channable.com/o/mlops-engineer",
                "salary": {"min": "4250", "max": "5000", "period": "month", "currency": "EUR"},
            }
        ]
    }
    http = fake_http_factory({"recruitee.com": payload})
    jobs, err = ats.fetch_recruitee(http, company(a="recruitee", token="channable"))
    assert err is None
    # must be a string, never the raw dict - jobs.json is rendered by the dashboard
    assert jobs[0].salary == "EUR 4.2k-5k/month"


def test_recruitee_against_real_payload(fake_http_factory):
    http = fake_http_factory({"recruitee.com": load_fixture("recruitee_offers.json")})
    jobs, err = ats.fetch_recruitee(http, company(a="recruitee", token="channable"))
    assert err is None
    assert len(jobs) == 2
    j = jobs[0]
    assert j.title and j.company == "Acme"
    assert j.url.startswith("https://")
    assert j.source == "recruitee" and j.tier == 1


def test_breezy_against_real_payload(fake_http_factory):
    http = fake_http_factory({"breezy.hr": load_fixture("breezy_positions.json")})
    jobs, err = ats.fetch_breezy(http, company(a="breezy", token="onedome"))
    assert err is None
    assert len(jobs) == 2
    j = jobs[0]
    assert j.title and j.company == "Acme"
    assert j.url.startswith("https://")
    assert j.source == "breezy" and j.tier == 1


def test_breezy_structured_location(fake_http_factory):
    payload = [
        {
            "name": "Data Engineer",
            "friendly_id": "abc123",
            "url": "https://onedome.breezy.hr/p/abc123",
            "published_date": "2026-08-14",
            "location": {
                "name": "United Kingdom",
                "city": "Bournemouth",
                "country": {"name": "United Kingdom", "id": "GB"},
                "is_remote": True,
                "remote_details": {"label": "Fully remote, no location restrictions"},
            },
        },
        {
            "name": "Analyst",
            "friendly_id": "def456",
            "url": "https://onedome.breezy.hr/p/def456",
            "published_date": "2026-08-14",
            "location": {
                "name": "Cape Town",
                "city": "Cape Town",
                "country": {"name": "South Africa", "id": "ZA"},
            },
        },
    ]
    http = fake_http_factory({"breezy.hr": payload})
    jobs, err = ats.fetch_breezy(http, company(a="breezy", token="onedome"))
    assert err is None
    assert jobs[0].location == "Remote - United Kingdom"
    assert jobs[1].location == "Cape Town"


def test_personio_against_real_payload(fake_http_factory):
    xml = (Path(__file__).resolve().parent / "fixtures" / "personio_positions.xml").read_text(
        encoding="utf-8"
    )
    http = fake_http_factory({"personio.de": xml})
    jobs, err = ats.fetch_personio(http, company(a="personio", token="personio"))
    assert err is None
    assert len(jobs) == 1
    j = jobs[0]
    assert j.title == "Staff Software Engineer, Data Platform"
    assert j.company == "Personio SE & Co. KG"
    assert "Munich" in (j.location or "")
    assert j.url == "https://personio.jobs.personio.de/job/1834171"
    assert j.source == "personio" and j.tier == 1


# ------------------------------------------------------------------ Tier 2 --


def test_remoteok_skips_legal_entry_and_captures_salary(fake_http_factory):
    http = fake_http_factory({"remoteok.com/api": REMOTEOK_PAYLOAD})
    jobs, err = feeds.fetch_remoteok(http)
    assert err is None
    assert len(jobs) == 2
    assert jobs[0].title == "MLOps Engineer"
    assert jobs[0].salary is not None and "120" in jobs[0].salary
    assert jobs[0].tags == ["python", "kubernetes"]
    assert jobs[0].tier == 2


def test_remotive_against_real_payload(fake_http_factory):
    http = fake_http_factory({"remotive.com": load_fixture("remotive_jobs.json")})
    jobs, err = feeds.fetch_remotive(http)
    assert err is None
    # fixture captured with limit=2 but the API returned 17 - frozen payload
    assert len(jobs) == 17
    assert jobs[0].title and jobs[0].company
    assert jobs[0].tier == 2


def test_himalayas_against_real_payload(fake_http_factory):
    http = fake_http_factory({"himalayas.app": load_fixture("himalayas_jobs.json")})
    jobs, err = feeds.fetch_himalayas(http)
    assert err is None
    assert len(jobs) == 2
    j = jobs[0]
    assert j.title and j.company
    assert j.location
    assert j.salary and "USD" in j.salary


def test_workingnomads_against_real_payload(fake_http_factory):
    http = fake_http_factory({"workingnomads.com": load_fixture("workingnomads_jobs.json")})
    jobs, err = feeds.fetch_workingnomads(http)
    assert err is None
    assert len(jobs) == 2
    assert jobs[0].title and jobs[0].company
    assert "Development" in jobs[0].tags


def test_rss_parses_company_title_split(fake_http_factory):
    http = fake_http_factory(
        {
            "remote-programming-jobs.rss": RSS_XML,
            "remote-devops-sysadmin-jobs.rss": RSS_XML_DEVOPS,
        }
    )
    jobs, err = feeds.fetch_weworkremotely(http)
    assert err is None
    assert len(jobs) == 3  # two from programming feed, one from devops feed
    prog, untitled, devops = jobs
    assert prog.company == "Acme Corp"
    assert prog.title == "Senior MLOps Engineer"
    assert prog.location == "Remote"
    assert prog.posted_at is not None
    # no colon -> company Unknown, whole string as title
    assert untitled.company == "Unknown"
    assert untitled.title == "Untitled Listing With No Colon"
    assert devops.title == "Site Reliability Engineer"
    assert devops.company == "Globex"


def test_rss_reports_error_only_when_all_feeds_fail(fake_http_factory):
    http = fake_http_factory({}, error=RuntimeError("rss down"))
    jobs, err = feeds.fetch_weworkremotely(http)
    assert jobs == [] and "rss down" in err


def test_hn_whoshiring_against_real_payload(fake_http_factory):
    http = fake_http_factory(
        {
            "hn.algolia.com/api/v1/search_by_date": load_fixture("hn_story_search.json"),
            "hn.algolia.com/api/v1/items": load_fixture("hn_items.json"),
        }
    )
    jobs, err = feeds.fetch_hn_whoshiring(http)
    assert err is None
    assert len(jobs) >= 1
    j = jobs[0]
    assert j.company == "Informal Systems"
    assert "Senior Distributed System Engineer" in j.title
    assert "Remote" in (j.location or "") or "Berlin" in (j.location or "")
    assert j.url and "news.ycombinator.com" in j.url
    assert j.source == "hn_whoshiring" and j.tier == 2


def test_hn_whoshiring_skips_seekers_and_short_lines(fake_http_factory):
    search = {"hits": [{"title": "Ask HN: Who is hiring? (September 2026)", "objectID": "42"}]}
    item = {
        "children": [
            {"type": "comment", "id": 1, "text": "Seeking work | Remote | backend"},
            {"type": "comment", "id": 2, "text": "just one line"},
            {"type": "comment", "id": 3, "text": "Acme | MLOps Engineer | Remote | Full Time"},
            {"type": "comment", "id": 4, "text": ""},
            {
                "type": "comment",
                "created_at": "2026-09-01T10:00:00Z",
                "id": 5,
                "text": "Globex | Site Reliability Engineer | Worldwide",
            },
        ]
    }
    http = fake_http_factory({"search_by_date": search, "items": item})
    jobs, err = feeds.fetch_hn_whoshiring(http)
    assert err is None
    titles = sorted(j.title for j in jobs)
    assert titles == ["MLOps Engineer", "Site Reliability Engineer"]
    locations = sorted(j.location for j in jobs)
    assert locations == ["Remote", "Worldwide"]


def test_hn_whoshiring_no_thread_found(fake_http_factory):
    http = fake_http_factory(
        {"search_by_date": {"hits": [{"title": "Ask HN: Who is hiring right now?", "objectID": "9"}]}}
    )
    jobs, err = feeds.fetch_hn_whoshiring(http)
    assert jobs == []
    assert "no monthly" in err


def test_hn_whoshiring_handles_error(fake_http_factory):
    http = fake_http_factory({}, error=RuntimeError("algolia down"))
    jobs, err = feeds.fetch_hn_whoshiring(http)
    assert jobs == [] and "algolia down" in err


def test_remoteok_handles_error(fake_http_factory):
    http = fake_http_factory({}, error=RuntimeError("503"))
    jobs, err = feeds.fetch_remoteok(http)
    assert jobs == [] and "503" in err
