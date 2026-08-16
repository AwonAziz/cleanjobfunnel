#!/usr/bin/env python3
"""
Integration smoke test for main(): runs the real orchestration (filter,
dedupe, first_seen tracking, seen.json pruning, the all-failed safety
net) against mocked network calls, in an isolated temp directory so it
never touches the real config/docs. Run with: python3 tests/test_integration.py
"""

import sys
import json
import shutil
import tempfile
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


def fake_response(json_body=None, content_bytes=None):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    if json_body is not None:
        resp.json.return_value = json_body
    if content_bytes is not None:
        resp.content = content_bytes
    return resp


GH_JOB = {"jobs": [{"id": 1, "title": "MLOps Engineer I", "location": {"name": "Remote"},
                     "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
                     "first_published": "2026-08-15T00:00:00Z"}]}
LEVER_EMPTY = []
ASHBY_EMPTY = {"jobs": []}
SR_EMPTY = {"content": []}
REMOTEOK_EMPTY = [{"legal": "n/a"}]
RSS_EMPTY = b"<?xml version='1.0'?><rss><channel></channel></rss>"


def router(url, **kwargs):
    if "greenhouse" in url:
        return fake_response(json_body=GH_JOB)
    if "lever" in url:
        return fake_response(json_body=LEVER_EMPTY)
    if "ashby" in url:
        return fake_response(json_body=ASHBY_EMPTY)
    if "smartrecruiters" in url:
        return fake_response(json_body=SR_EMPTY)
    if "remoteok" in url:
        return fake_response(json_body=REMOTEOK_EMPTY)
    return fake_response(content_bytes=RSS_EMPTY)


tmp = Path(tempfile.mkdtemp())
try:
    (tmp / "config").mkdir()
    (tmp / "docs" / "data").mkdir(parents=True)
    (tmp / "config" / "companies.json").write_text(json.dumps({
        "companies": [{"name": "Acme", "ats": "greenhouse", "token": "acme", "group": "aiops"}],
        "role_keywords": ["MLOps", "AI Engineer"],
        "location_allow": ["remote"],
        "seniority_flags": ["Senior", "Staff"],
    }))

    fj.CONFIG_PATH = tmp / "config" / "companies.json"
    fj.DATA_DIR = tmp / "docs" / "data"
    fj.JOBS_PATH = fj.DATA_DIR / "jobs.json"
    fj.SEEN_PATH = fj.DATA_DIR / "seen.json"
    fj.STATUS_PATH = fj.DATA_DIR / "status.json"
    fj.RSS_FEEDS = [{"name": "weworkremotely", "url": "https://weworkremotely.com/x.rss"}]

    print("run 1 (cold start)")
    with patch.object(fj.requests, "get", side_effect=router), patch.object(fj.time, "sleep"):
        fj.main()

    jobs1 = json.loads(fj.JOBS_PATH.read_text())
    seen1 = json.loads(fj.SEEN_PATH.read_text())
    check("jobs.json has 1 matching job", jobs1["count"] == 1)
    check("first_seen was stamped", jobs1["jobs"][0]["first_seen"] == seen1[jobs1["jobs"][0]["id"]])
    first_seen_run1 = jobs1["jobs"][0]["first_seen"]

    print("run 2 (same job reappears -- first_seen must NOT reset)")
    with patch.object(fj.requests, "get", side_effect=router), patch.object(fj.time, "sleep"):
        fj.main()
    jobs2 = json.loads(fj.JOBS_PATH.read_text())
    check("still 1 job", jobs2["count"] == 1)
    check("first_seen persisted across runs", jobs2["jobs"][0]["first_seen"] == first_seen_run1)

    print("run 3 (job disappears from source -- seen.json should prune it)")
    def router_empty(url, **kwargs):
        if "greenhouse" in url:
            return fake_response(json_body={"jobs": []})
        return router(url, **kwargs)
    with patch.object(fj.requests, "get", side_effect=router_empty), patch.object(fj.time, "sleep"):
        fj.main()
    jobs3 = json.loads(fj.JOBS_PATH.read_text())
    seen3 = json.loads(fj.SEEN_PATH.read_text())
    check("job dropped from jobs.json once no longer posted", jobs3["count"] == 0)
    check("seen.json pruned the stale id", len(seen3) == 0)

    print("run 4 (every single source fails -- must NOT wipe previous jobs.json)")
    # first restore a job so there's something to protect
    with patch.object(fj.requests, "get", side_effect=router), patch.object(fj.time, "sleep"):
        fj.main()
    before = fj.JOBS_PATH.read_text()
    with patch.object(fj.requests, "get", side_effect=fj.requests.RequestException("network down")), patch.object(fj.time, "sleep"):
        fj.main()
    after = fj.JOBS_PATH.read_text()
    check("jobs.json untouched when all sources fail", before == after)
    check("status.json still records the failure", "Every source failed" in fj.STATUS_PATH.read_text())

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
