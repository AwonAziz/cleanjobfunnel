"""Cross-source duplicate merging."""

from __future__ import annotations

from jobfunnel.dedupe import (
    fingerprint,
    merge_duplicates,
    normalize_company,
    normalize_location,
    normalize_title,
)
from jobfunnel.models import Job


def job(
    job_id: str, title: str, company: str, url: str, source: str, tier: int = 2, score: int = 0, **kw
) -> Job:
    return Job(
        id=job_id,
        title=title,
        company=company,
        url=url,
        source=source,
        tier=tier,
        score=score,
        **kw,
    )


def test_fingerprint_ignores_noise():
    assert fingerprint(job("1", "MLOps Engineer (Remote)", "Acme, Inc.", "https://a/1", "x")) == fingerprint(
        job("2", "mlops engineer", "ACME Inc", "https://b/2", "y")
    )


def test_fingerprint_keeps_distinct_roles_apart():
    a = fingerprint(job("1", "MLOps Engineer I", "Acme", "https://a/1", "x"))
    b = fingerprint(job("2", "MLOps Engineer II", "Acme", "https://a/2", "x"))
    assert a != b


def test_normalize_company_strips_suffixes():
    assert normalize_company("Grafana Labs, Inc.") == "grafana"
    assert normalize_company("Acme GmbH") == "acme"


def test_normalize_title_strips_parens():
    assert normalize_title("DevOps Engineer (Remote, EMEA)") == "devops engineer"


def test_normalize_location_ignores_case_and_spacing():
    assert normalize_location("Remote - EMEA") == normalize_location("REMOTE (emea)")
    assert normalize_location(None) == ""


def test_merge_tier1_wins_over_tier2():
    t1 = job(
        "t1",
        "MLOps Engineer",
        "Acme",
        "https://boards.greenhouse.io/acme/jobs/1",
        "greenhouse",
        tier=1,
        score=60,
    )
    t2 = job(
        "t2",
        "MLOps Engineer",
        "Acme",
        "https://remoteok.com/remote-jobs/acme-mlops",
        "remoteok",
        tier=2,
        score=70,
    )
    merged, n, groups = merge_duplicates([t2, t1])
    assert n == 1
    assert len(merged) == 1
    assert merged[0].source == "greenhouse"
    assert merged[0].also_on == ["remoteok"]
    assert merged[0].id == "t1"
    assert groups == [["t1", "t2"]]


def test_merge_by_url_even_when_titles_differ():
    a = job("a", "MLOps Engineer", "Acme", "https://jobs.ashbyhq.com/acme/abc", "ashby", tier=1, score=60)
    b = job(
        "b",
        "Acme: MLOps Engineer",
        "Acme",
        "https://jobs.ashbyhq.com/acme/abc?ref=rss",
        "himalayas",
        tier=2,
        score=40,
    )
    merged, n, groups = merge_duplicates([a, b])
    assert n == 1
    assert merged[0].source == "ashby"
    assert merged[0].also_on == ["himalayas"]


def test_same_source_same_title_different_locations_stay_separate():
    emea = job(
        "emea",
        "Platform Engineer (Remote - EMEA)",
        "Acme",
        "https://a/emea",
        "greenhouse",
        tier=1,
        location="Remote - EMEA",
    )
    apac = job(
        "apac",
        "Platform Engineer (Remote - APAC)",
        "Acme",
        "https://a/apac",
        "greenhouse",
        tier=1,
        location="Remote - APAC",
    )
    merged, n, groups = merge_duplicates([emea, apac])
    assert n == 0
    assert {m.id for m in merged} == {"emea", "apac"}


def test_same_source_true_duplicate_still_merges():
    a = job("a", "MLOps Engineer", "Acme", "https://a/1", "greenhouse", tier=1, location="Remote")
    b = job("b", "MLOps Engineer", "Acme", "https://a/2", "greenhouse", tier=1, location="REMOTE")
    merged, n, _ = merge_duplicates([a, b])
    assert n == 1
    assert len(merged) == 1
    assert merged[0].id == "a"


def test_merge_enriches_primary_from_duplicate():
    t1 = job("t1", "MLOps Engineer", "Acme", "https://a/1", "greenhouse", tier=1)
    t1.salary = None
    t2 = job("t2", "MLOps Engineer", "Acme", "https://b/2", "remoteok", tier=2, salary="$120k-140k")
    t2.tags = ["python", "kubernetes"]
    merged, _, _ = merge_duplicates([t1, t2])
    assert merged[0].salary == "$120k-140k"
    assert sorted(merged[0].tags) == ["kubernetes", "python"]


def test_distinct_jobs_untouched():
    a = job("a", "MLOps Engineer", "Acme", "https://a/1", "greenhouse", tier=1)
    b = job("b", "SRE", "Other Inc", "https://b/2", "greenhouse", tier=1)
    merged, n, _ = merge_duplicates([a, b])
    assert n == 0
    assert len(merged) == 2
    assert {m.id for m in merged} == {"a", "b"}


def test_merge_is_deterministic_regardless_of_input_order():
    t1 = job("t1", "MLOps Engineer", "Acme", "https://a/1", "greenhouse", tier=1)
    t2 = job("t2", "MLOps Engineer", "Acme", "https://b/2", "remoteok", tier=2)
    out1 = merge_duplicates([t1, t2])[0][0].id
    out2 = merge_duplicates([t2, t1])[0][0].id
    assert out1 == out2 == "t1"
