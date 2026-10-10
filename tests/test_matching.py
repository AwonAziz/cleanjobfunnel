"""Matching engine: keyword/synonym/location rules, scoring, explanations."""

from __future__ import annotations

from datetime import datetime, timezone

from jobfunnel.config import Config
from jobfunnel.matching import RuleSet, evaluate
from jobfunnel.models import Job


def make_rules(**overrides) -> RuleSet:
    cfg = Config.from_dict(
        {
            "companies": [{"name": "X", "ats": "greenhouse", "token": "x"}],
            "role_keywords": overrides.get("role_keywords", ["MLOps", "SRE", "Platform Engineer"]),
            "location_allow": overrides.get("location_allow", ["remote"]),
            "seniority_flags": overrides.get("seniority_flags", ["Senior", "Staff"]),
            "synonyms": overrides.get(
                "synonyms", {"SRE": ["Site Reliability Engineer", "Reliability Engineer"]}
            ),
            "exclude_keywords": overrides.get("exclude_keywords", ["intern", "unpaid"]),
            "location_exclude": overrides.get("location_exclude", ["united states", "usa"]),
        },
        known_ats={"greenhouse"},
        known_feeds=set(),
    )
    return RuleSet.from_config(cfg)


def job(**kwargs) -> Job:
    defaults = {
        "id": "j1",
        "title": "MLOps Engineer",
        "company": "Acme",
        "url": "https://example.com/1",
        "source": "greenhouse",
        "tier": 1,
        "location": "Remote",
    }
    defaults.update(kwargs)
    return Job(**defaults)  # type: ignore[arg-type]


NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


# ------------------------------------------------------------- keywords --


def test_word_boundary_avoids_substring_false_positive():
    rules = make_rules(role_keywords=["SRE"], seniority_flags=[])
    assert not evaluate(job(title="Job Searching Specialist"), rules, NOW).keep
    assert evaluate(job(title="Senior SRE, Platform"), rules, NOW).keep


def test_light_stemming_matches_engineering():
    rules = make_rules(role_keywords=["Platform Engineer"], seniority_flags=[])
    assert evaluate(job(title="Platform Engineering Manager"), rules, NOW).keep
    assert evaluate(job(title="Senior Platform Engineers"), rules, NOW).keep
    assert not evaluate(job(title="Platform Management Lead"), rules, NOW).keep


def test_synonym_hit_scores_same_canonical():
    rules = make_rules(
        role_keywords=["SRE"], synonyms={"SRE": ["Site Reliability Engineer"]}, seniority_flags=[]
    )
    v = evaluate(job(title="Site Reliability Engineer"), rules, NOW)
    assert v.keep
    assert any("SRE" in r for r in v.reasons)
    assert any("alias" in r for r in v.reasons)


def test_slash_keywords_still_match():
    rules = make_rules(role_keywords=["AI/ML Engineer"], seniority_flags=[])
    v = evaluate(job(title="AI/ML Engineer II"), rules, NOW)
    assert v.keep
    v2 = evaluate(job(title="AIML Engineer"), rules, NOW)
    assert not v2.keep


def test_no_keyword_match_drops():
    rules = make_rules()
    v = evaluate(job(title="Account Executive"), rules, NOW)
    assert not v.keep
    assert v.dropped_by == "keyword"


def test_multiple_keyword_hits_capped():
    rules = make_rules(role_keywords=["MLOps", "ML Platform", "Platform Engineer"], seniority_flags=[])
    v = evaluate(job(title="MLOps Engineer, ML Platform"), rules, NOW)
    # two title hits (30+30 capped at 50) + tier 15 + remote 10 = 75
    assert v.score == 75
    assert v.keep


# -------------------------------------------------------------- excludes --


def test_exclude_drops_internship_but_not_internal():
    rules = make_rules(exclude_keywords=["intern", "internship", "unpaid"])
    v = evaluate(job(title="Internal Tools MLOps Engineer"), rules, NOW)
    assert v.keep
    v2 = evaluate(job(title="MLOps Internship"), rules, NOW)
    assert not v2.keep
    assert v2.dropped_by == "excluded"


def test_exclude_beats_keyword():
    rules = make_rules(exclude_keywords=["unpaid"])
    assert not evaluate(job(title="MLOps Engineer (Unpaid)"), rules, NOW).keep


# -------------------------------------------------------------- location --


def test_missing_location_is_kept_as_ambiguous():
    rules = make_rules()
    v = evaluate(job(location=None), rules, NOW)
    assert v.keep
    assert any("remote-friendly" in r for r in v.reasons)


def test_location_allow_and_exclude():
    rules = make_rules()
    assert evaluate(job(location="Remote - EMEA"), rules, NOW).keep
    assert not evaluate(job(location="New York, NY"), rules, NOW).keep
    assert not evaluate(job(location="Remote (United States)"), rules, NOW).keep
    # "us and worldwide" doesn't contain an excluded term, so it survives
    assert evaluate(job(location="Remote (US and worldwide)"), rules, NOW).keep


def test_location_exclude_only_after_allow():
    rules = make_rules(location_allow=["remote", "worldwide"], location_exclude=["usa"])
    v = evaluate(job(location="Remote (USA only)"), rules, NOW)
    assert not v.keep
    assert v.dropped_by == "location"
    assert any("excluded by" in r for r in v.reasons)


def test_short_location_codes_use_word_boundaries():
    rules = make_rules(location_allow=["remote"], location_exclude=["us", "uk"])
    assert not evaluate(job(location="Remote - US"), rules, NOW).keep
    assert not evaluate(job(location="Remote (UK)"), rules, NOW).keep
    # "us"/"uk" inside longer words must not trigger
    assert evaluate(job(location="Remote (focus on usage data)"), rules, NOW).keep


def test_state_locked_remote_excluded():
    rules = make_rules(location_allow=["remote"], location_exclude=["virginia", "california"])
    assert not evaluate(job(location="Remote - Virginia"), rules, NOW).keep
    assert evaluate(job(location="Remote - EMEA"), rules, NOW).keep


def test_remote_words_get_bonus():
    rules = make_rules(location_allow=["remote", "worldwide"], seniority_flags=[])
    assert evaluate(job(location="Remote"), rules, NOW).score == 55  # 30 + 15 + 10
    assert evaluate(job(location="Worldwide"), rules, NOW).score == 55
    # Berlin is not in the allow list -> dropped regardless of bonus
    assert not evaluate(job(location="Berlin, Germany"), rules, NOW).keep


# ---------------------------------------------------------------- scoring --


def test_recency_bonus():
    rules = make_rules(seniority_flags=[])
    fresh = evaluate(job(posted_at="2026-10-08T00:00:00+00:00"), rules, NOW)
    stale = evaluate(job(posted_at="2025-01-08T00:00:00+00:00"), rules, NOW)
    assert fresh.score == stale.score + 10
    assert any("posted" in r for r in fresh.reasons)


def test_title_bonus_capped():
    rules = make_rules(seniority_flags=[])
    one = evaluate(job(title="MLOps Engineer"), rules, NOW)
    three = evaluate(
        job(title="MLOps Engineer, SRE, Platform Engineer", posted_at="2026-10-09T00:00:00+00:00"), rules, NOW
    )
    assert one.score == 55
    # 3 title hits would be 90, capped at 50; +15 tier +10 remote +10 recency = 85
    assert three.score == 85
    assert three.score <= 100


def test_min_score_threshold():
    cfg = Config.from_dict(
        {
            "companies": [{"name": "X", "ats": "greenhouse", "token": "x"}],
            "role_keywords": ["MLOps"],
            "location_allow": [],
            "seniority_flags": [],
            "min_score": 56,
        },
        known_ats={"greenhouse"},
    )
    rules = RuleSet.from_config(cfg)
    v = evaluate(job(location="Remote"), rules, NOW)  # 55 -> below threshold
    assert not v.keep
    assert v.dropped_by == "score"
    v2 = evaluate(job(location="Remote", posted_at="2026-10-09T00:00:00+00:00"), rules, NOW)  # 65
    assert v2.keep


def test_seniority_flag_is_flag_only():
    rules = make_rules()
    v = evaluate(job(title="Staff MLOps Engineer"), rules, NOW)
    assert v.keep  # flagged, never dropped
    assert v.is_senior
    assert any("senior-leaning" in r for r in v.reasons)
