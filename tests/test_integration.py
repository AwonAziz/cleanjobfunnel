"""Integration tests: the whole pipeline through scan.run_scan.

Runs the real orchestration (fetch -> match -> merge -> first_seen
bookkeeping -> files) against mocked network calls in a temp directory,
so the real config/docs are never touched.
"""

from __future__ import annotations

import json
from pathlib import Path

from jobfunnel.config import Config
from jobfunnel.scan import run_scan

from .conftest import FakeHttp, feeds_only

GH_JOB = {
    "jobs": [
        {
            "id": 1,
            "title": "MLOps Engineer",
            "location": {"name": "Remote"},
            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
            "first_published": "2026-10-01T00:00:00Z",
        }
    ]
}
LEVER_EMPTY = []
REMOTEOK = [
    {"legal": "n/a"},
    {
        "id": "rk1",
        "position": "MLOps Engineer",
        "company": "acme",
        "location": "Worldwide",
        "url": "https://remoteok.com/remote-jobs/acme-mlops-1",
        "date": "2026-10-02T00:00:00Z",
    },
]
REMOTEOK_EMPTY = [{"legal": "n/a"}]


def routes(gh=GH_JOB, remoteok=REMOTEOK):
    return {
        "boards-api.greenhouse.io": gh,
        "api.lever.co": LEVER_EMPTY,
        "remoteok.com/api": remoteok,
    }


CONFIG = {
    "companies": [
        {"name": "Acme", "ats": "greenhouse", "token": "acme", "group": "aiops", "verified": True},
        {"name": "Beta", "ats": "lever", "token": "beta", "group": None},
    ],
    "role_keywords": ["MLOps", "AI Engineer"],
    "location_allow": ["remote", "worldwide"],
    "location_exclude": [],
    "seniority_flags": ["Senior", "Staff"],
    "exclude_keywords": ["intern", "internship"],
    "synonyms": {"MLOps": ["Machine Learning Operations"]},
    "feeds": feeds_only("remoteok"),
}


def make_config(**overrides):
    raw = json.loads(json.dumps(CONFIG))
    raw.update(overrides)
    return Config.from_dict(
        raw,
        known_ats={"greenhouse", "lever"},
        known_feeds={"remoteok", "remotive", "himalayas", "workingnomads", "hn_whoshiring", "weworkremotely"},
    )


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_full_run_writes_expected_files(tmp_data_dir):
    result = run_scan(FakeHttp(routes()), make_config(), tmp_data_dir)
    assert result.fetched == 2  # 1 greenhouse + 1 remoteok (lever board is empty)
    # remoteok copy of the Acme job merges with the greenhouse copy
    assert result.merged_duplicates == 1
    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert job.source == "greenhouse" and job.also_on == ["remoteok"]
    assert job.score > 0 and job.match_reasons
    assert job.first_seen is not None

    data = read(tmp_data_dir / "jobs.json")
    assert data["count"] == 1
    assert data["jobs"][0]["title"] == "MLOps Engineer"
    assert "score" in data["jobs"][0] and "match_reasons" in data["jobs"][0]

    status = read(tmp_data_dir / "status.json")
    assert status["merged_duplicates"] == 1
    assert len(status["sources"]) == 3
    assert all(s["ok"] for s in status["sources"])

    seen = read(tmp_data_dir / "seen.json")
    # both ids of the merged group stay alive, sharing the group's earliest stamp
    assert len(seen) == 2
    assert set(seen.values()) == {job.first_seen}
    assert job.id in seen


def test_first_seen_persists_across_runs(tmp_data_dir):
    run_scan(FakeHttp(routes()), make_config(), tmp_data_dir)
    seen1 = read(tmp_data_dir / "seen.json")

    run_scan(FakeHttp(routes()), make_config(), tmp_data_dir)
    seen2 = read(tmp_data_dir / "seen.json")
    assert seen1 == seen2  # first_seen must NOT reset for a job that reappears


def test_stale_ids_pruned_from_seen(tmp_data_dir):
    run_scan(FakeHttp(routes()), make_config(), tmp_data_dir)
    assert len(read(tmp_data_dir / "seen.json")) == 2  # both ids of the merged group

    # job disappears from every source -> dropped from jobs.json + seen.json
    run_scan(FakeHttp(routes(gh={"jobs": []}, remoteok=REMOTEOK_EMPTY)), make_config(), tmp_data_dir)
    assert read(tmp_data_dir / "jobs.json")["count"] == 0
    assert read(tmp_data_dir / "seen.json") == {}


def test_all_sources_failed_keeps_previous_jobs(tmp_data_dir):
    run_scan(FakeHttp(routes()), make_config(), tmp_data_dir)
    before = (tmp_data_dir / "jobs.json").read_text(encoding="utf-8")

    dead = FakeHttp({}, error=RuntimeError("network down"))
    result = run_scan(dead, make_config(), tmp_data_dir)
    assert result.all_failed
    assert result.jobs == []
    assert (tmp_data_dir / "jobs.json").read_text(encoding="utf-8") == before
    assert "Every source failed" in read(tmp_data_dir / "status.json")["note"]


def test_dry_run_writes_nothing(tmp_data_dir):
    result = run_scan(FakeHttp(routes()), make_config(), tmp_data_dir, dry_run=True)
    assert len(result.jobs) == 1
    assert not (tmp_data_dir / "jobs.json").exists()
    assert not (tmp_data_dir / "status.json").exists()


def test_only_filters_sources(tmp_data_dir):
    result = run_scan(FakeHttp(routes()), make_config(), tmp_data_dir, only={"remoteok"})
    # remoteok alone: the single job still matches keywords
    assert result.total_sources == 1
    assert len(result.jobs) == 1
    assert result.jobs[0].source == "remoteok"


def test_keyword_filtering_and_reasons(tmp_data_dir):
    gh = {
        "jobs": [
            {
                "id": 1,
                "title": "MLOps Engineer",
                "location": {"name": "Remote"},
                "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
                "first_published": "2026-10-01T00:00:00Z",
            },
            {
                "id": 2,
                "title": "Account Executive",
                "location": {"name": "Remote"},
                "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/2",
                "first_published": "2026-10-01T00:00:00Z",
            },
            {
                "id": 3,
                "title": "MLOps Internship",
                "location": {"name": "Remote"},
                "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/3",
                "first_published": "2026-10-01T00:00:00Z",
            },
        ]
    }
    result = run_scan(FakeHttp(routes(gh=gh, remoteok=REMOTEOK_EMPTY)), make_config(), tmp_data_dir)
    titles = [j.title for j in result.jobs]
    assert titles == ["MLOps Engineer"]
    assert result.filtered_out == 2  # keyword miss + excluded internship
    assert any("direct from company board" in r for r in result.jobs[0].match_reasons)


def test_score_orders_results(tmp_data_dir):
    gh = {
        "jobs": [
            {
                "id": 1,
                "title": "MLOps Engineer",
                "location": {"name": "New York"},
                "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
                "first_published": "2026-10-01T00:00:00Z",
            },
            {
                "id": 2,
                "title": "Machine Learning Operations Engineer",
                "location": {"name": "Remote - EMEA"},
                "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/2",
                "first_published": "2026-10-09T00:00:00Z",
            },
        ]
    }
    # widen the config so the New York job survives: allow everything, exclude nothing
    cfg = make_config(location_allow=[], location_exclude=[])
    result = run_scan(FakeHttp(routes(gh=gh, remoteok=REMOTEOK_EMPTY)), cfg, tmp_data_dir)
    assert len(result.jobs) == 2
    # fresh + remote + synonym title scores highest
    assert result.jobs[0].title == "Machine Learning Operations Engineer"
    assert result.jobs[0].score > result.jobs[1].score
    assert result.jobs[0].score >= result.jobs[1].score


def test_one_bad_board_does_not_kill_scan(tmp_data_dir):
    class MixedHttp(FakeHttp):
        def get_json(self, url, params=None):
            self.calls.append(url)
            if "lever" in url:
                raise RuntimeError("lever board moved")
            return super().get_json(url, params)

    result = run_scan(MixedHttp(routes()), make_config(), tmp_data_dir)
    assert any(not s.ok for s in result.reports)
    assert len(result.jobs) == 1
    assert result.merged_duplicates == 1


def test_first_seen_survives_aggregator_to_board_flip(tmp_data_dir):
    """A job that flips between aggregator-only and board+aggregator must
    keep its original first_seen (the merged group's earliest stamp)."""
    # run 1: aggregator only -> id A stamped
    result1 = run_scan(FakeHttp(routes(gh={"jobs": []})), make_config(), tmp_data_dir)
    assert len(result1.jobs) == 1 and result1.jobs[0].source == "remoteok"
    stamp = result1.jobs[0].first_seen

    # run 2: the company board appears, the two copies merge, board wins
    result2 = run_scan(FakeHttp(routes()), make_config(), tmp_data_dir)
    assert len(result2.jobs) == 1 and result2.jobs[0].source == "greenhouse"
    assert result2.jobs[0].first_seen == stamp

    # run 3: the board disappears again -> the aggregator id must NOT be new
    seen_between = read(tmp_data_dir / "seen.json")
    assert list(seen_between.values()) == [stamp] * 2  # both group members kept
    result3 = run_scan(FakeHttp(routes(gh={"jobs": []})), make_config(), tmp_data_dir)
    assert result3.jobs[0].first_seen == stamp


def test_only_scan_writes_nothing(tmp_data_dir):
    # seed the canonical data first
    run_scan(FakeHttp(routes()), make_config(), tmp_data_dir)
    before = (tmp_data_dir / "jobs.json").read_text(encoding="utf-8")
    seen_before = (tmp_data_dir / "seen.json").read_text(encoding="utf-8")

    result = run_scan(FakeHttp(routes()), make_config(), tmp_data_dir, only={"remoteok"})
    assert result.persisted is False
    assert len(result.jobs) == 1
    # canonical data untouched - including everyone's first_seen history
    assert (tmp_data_dir / "jobs.json").read_text(encoding="utf-8") == before
    assert (tmp_data_dir / "seen.json").read_text(encoding="utf-8") == seen_before


def test_only_typo_reports_nothing_selected(tmp_data_dir):
    result = run_scan(FakeHttp(routes()), make_config(), tmp_data_dir, only={"nope"})
    assert result.nothing_selected
    assert result.total_sources == 0
    assert not (tmp_data_dir / "jobs.json").exists()
