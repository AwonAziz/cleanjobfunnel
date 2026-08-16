#!/usr/bin/env python3
"""
Offline sanity tests for fetch_jobs.py parsers, using sample payloads
shaped like each provider's documented schema. Mocks requests.get so
nothing touches the network. Run with: python3 tests/test_parsers.py
"""

import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_jobs as fj  # noqa: E402

passed = 0
failed = 0


def check(label, condition):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ok   {label}")
    else:
        failed += 1
        print(f"  FAIL {label}")


def fake_response(json_body=None, content_bytes=None, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status = MagicMock() if status < 400 else MagicMock(side_effect=Exception("http error"))
    if json_body is not None:
        resp.json.return_value = json_body
    if content_bytes is not None:
        resp.content = content_bytes
    return resp


print("helpers")
check("to_iso handles Lever epoch-ms", fj.to_iso(1750000000000).startswith("2025-06"))
check("to_iso handles Greenhouse ISO offset", fj.to_iso("2016-01-14T10:55:28-05:00") == "2016-01-14T15:55:28+00:00")
check("to_iso handles RSS RFC822", fj.to_iso("Thu, 14 Aug 2026 10:00:00 +0000") == "2026-08-14T10:00:00+00:00")
check("to_iso handles None", fj.to_iso(None) is None)
check("matches_keywords is case-insensitive", fj.matches_keywords("Senior mlops engineer", ["MLOps"]))
check("matches_keywords rejects unrelated title", not fj.matches_keywords("Account Executive", ["MLOps", "AI Engineer"]))
check("matches_location allows ambiguous/missing", fj.matches_location(None, ["remote"]))
check("matches_location matches Gulf city", fj.matches_location("Dubai, UAE", ["dubai", "remote"]))
check("matches_location rejects clear non-match", not fj.matches_location("New York, NY (Onsite)", ["remote", "dubai"]))
check("flag_senior catches Staff", fj.flag_senior("Staff MLOps Engineer", fj_flags := ["Staff", "Senior", "Principal"]))
check("flag_senior ignores entry-level", not fj.flag_senior("Associate ML Engineer I", fj_flags))
check("stable_id is deterministic", fj.stable_id("greenhouse", "x", 1) == fj.stable_id("greenhouse", "x", 1))

print("greenhouse")
gh_payload = {"jobs": [{
    "id": 127817, "title": "MLOps Engineer I",
    "location": {"name": "Remote - EMEA"},
    "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/127817",
    "first_published": "2026-08-15T09:00:00-00:00", "updated_at": "2026-08-15T09:00:00-00:00",
}]}
with patch.object(fj.requests, "get", return_value=fake_response(json_body=gh_payload)):
    jobs, err = fj.fetch_greenhouse({"name": "Acme", "token": "acme", "group": "aiops"})
check("greenhouse: no error", err is None)
check("greenhouse: one job parsed", len(jobs) == 1)
check("greenhouse: title correct", jobs and jobs[0]["title"] == "MLOps Engineer I")
check("greenhouse: location.name extracted", jobs and jobs[0]["location"] == "Remote - EMEA")
check("greenhouse: url extracted", jobs and jobs[0]["url"].endswith("/127817"))
check("greenhouse: tier is 1", jobs and jobs[0]["tier"] == 1)

print("lever")
lever_payload = [{
    "id": "a1b2c3", "text": "Associate AI Engineer",
    "categories": {"team": "Engineering", "location": "Remote"},
    "hostedUrl": "https://jobs.lever.co/acme/a1b2c3", "workplaceType": "remote",
    "createdAt": 1755000000000,
}]
with patch.object(fj.requests, "get", return_value=fake_response(json_body=lever_payload)):
    jobs, err = fj.fetch_lever({"name": "Acme", "token": "acme", "group": "ml-lifecycle"})
check("lever: no error", err is None)
check("lever: title from 'text'", jobs and jobs[0]["title"] == "Associate AI Engineer")
check("lever: location from categories", jobs and jobs[0]["location"] == "Remote")
check("lever: posted_at parsed from epoch ms", jobs and jobs[0]["posted_at"] is not None)

print("ashby (dict-wrapped shape)")
ashby_payload = {"jobs": [{
    "id": "j1", "title": "AIOps Engineer, Entry Level", "location": "Remote",
    "jobUrl": "https://jobs.ashbyhq.com/acme/j1", "publishedAt": "2026-08-14T00:00:00.000Z",
}]}
with patch.object(fj.requests, "get", return_value=fake_response(json_body=ashby_payload)):
    jobs, err = fj.fetch_ashby({"name": "Acme", "token": "acme", "group": "ai-native"})
check("ashby (dict shape): no error", err is None)
check("ashby (dict shape): title correct", jobs and jobs[0]["title"] == "AIOps Engineer, Entry Level")

print("ashby (bare list shape, in case the API returns one)")
ashby_list_payload = [{
    "id": "j2", "title": "ML Platform Engineer", "location": "Remote",
    "applyUrl": "https://jobs.ashbyhq.com/acme/j2", "publishedAt": "2026-08-13T00:00:00.000Z",
}]
with patch.object(fj.requests, "get", return_value=fake_response(json_body=ashby_list_payload)):
    jobs, err = fj.fetch_ashby({"name": "Acme", "token": "acme", "group": "ai-native"})
check("ashby (list shape): no error", err is None)
check("ashby (list shape): falls back to applyUrl", jobs and jobs[0]["url"].endswith("/j2"))

print("smartrecruiters")
sr_payload = {"content": [{
    "id": "sr1", "name": "Machine Learning Engineer",
    "location": {"city": "Riyadh", "country": "Saudi Arabia", "remote": False},
    "postingUrl": "https://jobs.smartrecruiters.com/Acme/sr1", "releasedDate": "2026-08-12T00:00:00.000Z",
}]}
with patch.object(fj.requests, "get", return_value=fake_response(json_body=sr_payload)):
    jobs, err = fj.fetch_smartrecruiters({"name": "Acme", "token": "acme", "group": "aiops"})
check("smartrecruiters: no error", err is None)
check("smartrecruiters: title from 'name'", jobs and jobs[0]["title"] == "Machine Learning Engineer")
check("smartrecruiters: location city+country joined", jobs and jobs[0]["location"] == "Riyadh, Saudi Arabia")

print("remoteok (leading legal object + real jobs)")
remoteok_payload = [
    {"legal": "notice, not a job"},
    {"id": "123", "position": "MLOps Engineer", "company": "Acme Remote",
     "location": "Worldwide", "url": "https://remoteok.com/remote-jobs/123", "date": "2026-08-15T00:00:00"},
]
with patch.object(fj.requests, "get", return_value=fake_response(json_body=remoteok_payload)):
    jobs, err = fj.fetch_remoteok()
check("remoteok: no error", err is None)
check("remoteok: leading legal object skipped", len(jobs) == 1)
check("remoteok: title from 'position'", jobs and jobs[0]["title"] == "MLOps Engineer")

print("rss (We Work Remotely / Remotive style)")
rss_xml = b"""<?xml version="1.0"?>
<rss><channel>
<item>
  <title>Acme Corp: Senior AIOps Engineer</title>
  <link>https://weworkremotely.com/remote-jobs/acme-senior-aiops-engineer</link>
  <pubDate>Fri, 14 Aug 2026 12:00:00 +0000</pubDate>
</item>
<item>
  <title>Untitled Listing With No Colon</title>
  <link>https://weworkremotely.com/remote-jobs/untitled</link>
  <pubDate>Fri, 14 Aug 2026 11:00:00 +0000</pubDate>
</item>
</channel></rss>"""
with patch.object(fj.requests, "get", return_value=fake_response(content_bytes=rss_xml)):
    jobs, err = fj.fetch_rss("https://weworkremotely.com/categories/remote-programming-jobs.rss", "weworkremotely")
check("rss: no error", err is None)
check("rss: two items parsed", len(jobs) == 2)
check("rss: company/title split on colon", jobs and jobs[0]["company"] == "Acme Corp" and jobs[0]["title"] == "Senior AIOps Engineer")
check("rss: falls back gracefully with no colon", jobs[1]["company"] == "Unknown" and jobs[1]["title"] == "Untitled Listing With No Colon")

print("failure handling")
with patch.object(fj.requests, "get", side_effect=fj.requests.RequestException("boom")):
    jobs, err = fj.fetch_greenhouse({"name": "Acme", "token": "doesnotexist", "group": None})
check("greenhouse: network error returns empty list + message, doesn't raise", jobs == [] and err == "boom")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
