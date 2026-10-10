"""Config loading and validation."""

from __future__ import annotations

import json

import pytest

from jobfunnel.config import Config, ConfigError

GOOD = {
    "companies": [
        {"name": "Acme", "ats": "greenhouse", "token": "acme", "group": "aiops", "verified": True},
        {"name": "Beta", "ats": "lever", "token": "beta"},
    ],
    "role_keywords": ["MLOps"],
    "location_allow": ["remote"],
    "seniority_flags": ["Senior"],
    "synonyms": {"MLOps": ["ML Ops", "Machine Learning Operations"]},
}


KNOWN_ATS = {"greenhouse", "lever", "ashby", "smartrecruiters", "recruitee", "breezy", "personio"}
KNOWN_FEEDS = {"remoteok", "remotive", "himalayas", "workingnomads", "hn_whoshiring", "weworkremotely"}


def load(raw=None, path=None):
    return Config.from_dict(raw or GOOD, path=path, known_ats=KNOWN_ATS, known_feeds=KNOWN_FEEDS)


def test_loads_minimal_config():
    cfg = load()
    assert len(cfg.companies) == 2
    assert cfg.companies[0].verified is True
    assert cfg.companies[1].group is None


def test_feeds_default_when_absent():
    cfg = load()
    names = {f.name for f in cfg.enabled_feeds()}
    assert {"remoteok", "remotive", "himalayas", "workingnomads", "hn_whoshiring", "weworkremotely"} <= names


def test_missing_required_key_raises():
    raw = dict(GOOD)
    del raw["role_keywords"]
    with pytest.raises(ConfigError, match="role_keywords"):
        load(raw)


def test_unknown_ats_raises():
    raw = json.loads(json.dumps(GOOD))
    raw["companies"][0]["ats"] = "workday"
    with pytest.raises(ConfigError, match="unknown ats"):
        load(raw)


def test_company_missing_token_raises():
    raw = json.loads(json.dumps(GOOD))
    del raw["companies"][1]["token"]
    with pytest.raises(ConfigError, match="token"):
        load(raw)


def test_synonyms_accept_string_shorthand():
    raw = json.loads(json.dumps(GOOD))
    raw["synonyms"] = {"MLOps": "ML Ops"}
    cfg = load(raw)
    assert cfg.synonyms["MLOps"] == ["ML Ops"]


def test_synonyms_reject_bad_shape():
    raw = json.loads(json.dumps(GOOD))
    raw["synonyms"] = {"MLOps": 42}
    with pytest.raises(ConfigError, match="must be a list of strings"):
        load(raw)


def test_feed_disable_and_unknown_feed():
    raw = json.loads(json.dumps(GOOD))
    raw["feeds"] = {"remoteok": {"enabled": False}, "remotive": True}
    cfg = load(raw)
    assert not cfg.feeds["remoteok"].enabled
    assert cfg.feeds["remotive"].enabled

    raw["feeds"]["nope"] = {}
    with pytest.raises(ConfigError, match="unknown feed"):
        load(raw)


def test_min_score_bounds():
    raw = json.loads(json.dumps(GOOD))
    raw["min_score"] = 101
    with pytest.raises(ConfigError, match="min_score"):
        load(raw)
    raw["min_score"] = 40
    assert load(raw).min_score == 40


def test_warnings_duplicate_board_and_empty_keywords():
    raw = json.loads(json.dumps(GOOD))
    raw["companies"].append({"name": "Acme Two", "ats": "greenhouse", "token": "acme"})
    cfg = load(raw)
    assert any("duplicate board" in w for w in cfg.warnings)

    raw = json.loads(json.dumps(GOOD))
    raw["role_keywords"] = []
    cfg = load(raw)
    assert any("role_keywords" in w for w in cfg.warnings)


def test_load_from_file(tmp_path):
    p = tmp_path / "companies.json"
    p.write_text(json.dumps(GOOD), encoding="utf-8")
    cfg = Config.load(p, known_ats=KNOWN_ATS, known_feeds=KNOWN_FEEDS)
    assert cfg.path == p
    assert len(cfg.companies) == 2


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="config not found"):
        Config.load(tmp_path / "nope.json", known_ats=set(), known_feeds=set())


def test_load_invalid_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        Config.load(p, known_ats=set(), known_feeds=set())
