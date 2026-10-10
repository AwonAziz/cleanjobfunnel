"""CLI behaviour: scan / validate / doctor / clean."""

from __future__ import annotations

import json

import pytest

from jobfunnel import cli

from .conftest import FakeHttp, feeds_only

CONFIG = {
    "companies": [
        {"name": "Acme", "ats": "greenhouse", "token": "acme", "group": "aiops", "verified": True},
    ],
    "role_keywords": ["MLOps"],
    "location_allow": ["remote"],
    "seniority_flags": ["Senior"],
    "feeds": feeds_only("remoteok"),
}

GH = {
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
REMOTEOK = [
    {"legal": "x"},
    {
        "id": "r1",
        "position": "Other Role",
        "company": "c",
        "location": "Remote",
        "url": "https://remoteok.com/1",
        "date": "2026-10-01T00:00:00Z",
    },
]


@pytest.fixture
def config_file(tmp_path):
    p = tmp_path / "companies.json"
    p.write_text(json.dumps(CONFIG), encoding="utf-8")
    return p


def test_validate_ok(config_file, capsys):
    code = cli.main(["validate", "--config", str(config_file)])
    out = capsys.readouterr().out
    assert code == 0
    assert "OK" in out and "1 companies" in out


def test_validate_bad_ats(config_file, capsys):
    raw = json.loads(config_file.read_text(encoding="utf-8"))
    raw["companies"][0]["ats"] = "workday"
    config_file.write_text(json.dumps(raw), encoding="utf-8")
    assert cli.main(["validate", "--config", str(config_file)]) == 1
    assert "INVALID" in capsys.readouterr().err


def test_scan_writes_files(tmp_path, config_file, monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "Http", lambda **kw: FakeHttp({"boards-api.greenhouse.io": GH, "remoteok.com/api": REMOTEOK})
    )
    data_dir = tmp_path / "out"
    code = cli.main(["scan", "--config", str(config_file), "--data-dir", str(data_dir)])
    out = capsys.readouterr().out
    assert code == 0
    assert "1 matched" in out
    jobs = json.loads((data_dir / "jobs.json").read_text(encoding="utf-8"))
    assert jobs["count"] == 1


def test_scan_dry_run(tmp_path, config_file, monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "Http", lambda **kw: FakeHttp({"boards-api.greenhouse.io": GH, "remoteok.com/api": REMOTEOK})
    )
    data_dir = tmp_path / "out"
    code = cli.main(["scan", "--config", str(config_file), "--data-dir", str(data_dir), "--dry-run"])
    assert code == 0
    assert "would write" in capsys.readouterr().out
    assert not (data_dir / "jobs.json").exists()


def test_scan_only_writes_nothing_and_says_so(tmp_path, config_file, monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "Http", lambda **kw: FakeHttp({"boards-api.greenhouse.io": GH, "remoteok.com/api": REMOTEOK})
    )
    data_dir = tmp_path / "out"
    code = cli.main(["scan", "--config", str(config_file), "--data-dir", str(data_dir), "--only", "remoteok"])
    assert code == 0
    out = capsys.readouterr().out
    assert "partial scan" in out and "nothing written" in out
    assert not (data_dir / "jobs.json").exists()


def test_scan_only_typo_exits_two(tmp_path, config_file, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Http", lambda **kw: FakeHttp({"boards-api.greenhouse.io": GH}))
    data_dir = tmp_path / "out"
    code = cli.main(["scan", "--config", str(config_file), "--data-dir", str(data_dir), "--only", "nope"])
    assert code == 2
    assert "no configured source matches" in capsys.readouterr().err


def test_scan_bad_min_score(config_file, capsys):
    assert cli.main(["scan", "--config", str(config_file), "--min-score", "101"]) == 1
    assert "min-score" in capsys.readouterr().err


def test_doctor_all_healthy(tmp_path, config_file, monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "Http", lambda **kw: FakeHttp({"boards-api.greenhouse.io": GH, "remoteok.com/api": REMOTEOK})
    )
    assert cli.main(["doctor", "--config", str(config_file)]) == 0
    out = capsys.readouterr().out
    assert "2/2 sources healthy" in out
    assert "ok  " in out


def test_doctor_reports_failures(tmp_path, config_file, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Http", lambda **kw: FakeHttp({}, error=RuntimeError("dead")))
    assert cli.main(["doctor", "--config", str(config_file)]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "0/2 sources healthy" in out


def test_clean_cache(tmp_path, monkeypatch, capsys):
    cache = tmp_path / ".cache" / "http"
    cache.mkdir(parents=True)
    (cache / "responses.json").write_text("{}", encoding="utf-8")
    original = cli.Http
    monkeypatch.setattr(cli, "Http", lambda **kw: original(cache_dir=cache, polite_delay=0))
    assert cli.main(["clean"]) == 0
    assert "cache cleared" in capsys.readouterr().out


def test_bare_invocation_runs_scan_with_defaults(tmp_path, config_file, monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "Http", lambda **kw: FakeHttp({"boards-api.greenhouse.io": GH, "remoteok.com/api": REMOTEOK})
    )
    monkeypatch.setattr(cli, "DEFAULT_CONFIG", config_file)
    monkeypatch.setattr(cli, "DEFAULT_DATA_DIR", tmp_path / "default-out")
    assert cli.main([]) == 0
    assert (tmp_path / "default-out" / "jobs.json").exists()
