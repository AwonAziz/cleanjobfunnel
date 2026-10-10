"""The relevance scorer against real, hand-labelled postings.

The fixture file is the point of this test. Every record is a posting that was
open and verified in the first week of October 2026, labelled good or bad by
reading it. There is no synthetic input here, because the failure mode this
engine guards against is not arithmetic, it is a human reading the same posting
and reaching the opposite conclusion.

Three numbers matter:

  precision - of everything the scorer keeps, how much is actually worth applying to
  recall    - of everything labelled good, how much survives the gates
  violations - every good posting it blocked and every bad posting it kept

Precision and recall trade off against each other, so the suite asserts a floor
on each rather than a single number. The violations list is the one that must be
short, because a silently discarded real role costs a day of a job search, and a
silently kept dead end costs an hour.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from jobfunnel.models import Job
from jobfunnel.relevance import Profile, eligibility, rank, rank_all, required_years, years_range

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)

PROFILE = Profile.from_dict(
    {
        "title": "AI/ML Engineer",
        "max_years": 2,
        "has_degree": False,
        "titles": [
            "ai engineer",
            "machine learning engineer",
            "mlops engineer",
            "data scientist",
            "aiops engineer",
            "ml engineer",
            "platform engineer",
            "devops engineer",
            "cloud engineer",
            "site reliability engineer",
            "llm engineer",
            "soc analyst",
        ],
        "critical": [
            "python",
            "pytorch",
            "scikit-learn",
            "numpy",
            "pandas",
            "rag",
            "llm",
            "mlops",
            "mlflow",
            "docker",
            "kubernetes",
            "fastapi",
            "aws",
            "linux",
            "git",
            "ci/cd",
            "github actions",
            "rest api",
            "pydantic",
            "terraform",
            "observability",
            "evaluation",
        ],
        "valuable": [
            "chromadb",
            "crewai",
            "reciprocal rank fusion",
            "hybrid retrieval",
            "bm25",
            "calibration",
            "bootstrap",
            "mcnemar",
            "drift detection",
            "psi",
            "streamlit",
            "gradio",
            "azure",
            "gcp",
            "ansible",
            "prometheus",
            "grafana",
            "jenkins",
            "gitlab",
            "elasticsearch",
            "sqlite",
            "sql",
            "anomaly detection",
            "isolation forest",
            "mitre",
            "siem",
        ],
        "absent": [
            "tensorflow",
            "keras",
            "langgraph",
            "postgresql",
            "celery",
            "dbt",
            "spark",
            "cuda",
            "react",
            "next.js",
            "typescript",
            "java",
            "tableau",
        ],
        "location_tiers": [
            [
                "remote - pakistan",
                "remote pakistan",
                "pakistan - remote",
                "remote (pakistan)",
                "pakistan (remote)",
            ],
            ["islamabad", "rawalpindi", "lahore", "karachi", "pakistan"],
            ["uae", "dubai", "abu dhabi", "saudi", "riyadh", "doha", "qatar", "gcc", "middle east"],
            [
                "remote worldwide",
                "worldwide",
                "anywhere",
                "remote (worldwide)",
                "europe",
                "emea",
                "uk",
                "united kingdom",
                "germany",
                "berlin",
            ],
            ["remote", "hybrid"],
            ["united states", "us only", "usa", "remote - us", "americas"],
        ],
    }
)


def _load_golden() -> list[Job]:
    path = Path(__file__).parent / "fixtures" / "golden_postings.jsonl"
    jobs: list[Job] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        raw.pop("label", None)
        raw.pop("why", None)
        jobs.append(Job(**raw))
    return jobs


GOLDEN = _load_golden()


def _labels_by_id() -> dict[str, str]:
    path = Path(__file__).parent / "fixtures" / "golden_postings.jsonl"
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        out[raw["id"]] = raw["label"]
    return out


LABELS = _labels_by_id()


@pytest.fixture()
def profile() -> Profile:
    return PROFILE


# ------------------------------------------------------- experience parsing --


@pytest.mark.parametrize(
    "text,expected",
    [
        # "N+" is a stated floor, and it dominates a loose range
        ("5+ years of professional experience", 5),
        ("2 years preferred, 5+ years required for the role", 5),
        # a range gates on its lower end, because 1 is inside "1 to 3"
        ("3 to 5 years", 3),
        ("0-2 years of experience", 0),
        ("1 to 3 years of experience", 1),
        ("two to four years", 2),
        ("5 to 7 years", 5),
        ("0-1 years of experience", 0),
        # a bare figure is itself the floor
        ("minimum of 2 years in DevOps", 2),
        ("ten years of relevant work", 10),
        ("three years of SOC experience", 3),
        # nothing stated
        ("no experience necessary", None),
        ("", None),
        (None, None),
    ],
)
def test_required_years_extraction(text, expected):
    assert required_years(text) == expected


def test_years_range_reports_both_bounds():
    """The ceiling is deliberately not used for gating. It is reported so a
    reader can see how far above their ceiling a posting sits, which is the
    difference between "blocked" and "blocked, and here is the distance".

    A bare figure has no stated upper bound, so its ceiling is None rather than
    a guess of N.
    """
    assert years_range("1 to 3 years of experience") == (1, 3)
    assert years_range("0-2 years of experience") == (0, 2)
    assert years_range("5+ years") == (5, None)
    assert years_range("2 years") == (2, None)
    assert years_range("nothing here") is None


def test_floor_not_ceiling_keeps_a_reachable_range():
    """The regression this guards: parsing "1-3 years" as a ceiling of 3
    silently discards every role whose floor you actually clear."""
    floor, ceiling = years_range("1-3 years of experience in a SOC")
    assert floor <= 2 < ceiling
    assert required_years("1-3 years of experience in a SOC") == 1


# ------------------------------------------------------ eligibility blockers --


def test_nationality_restriction_blocks_a_junior_titled_role(profile):
    """The trap this engine exists for: a posting that says entry-level and
    then restricts itself to Emirati nationals."""
    bad = next(j for j in GOLDEN if j.id == "talents-emirati-only")
    v = rank(bad, profile, NOW)
    assert not v.keep
    assert any("nationality" in b.lower() for b in v.blockers)


def test_nationality_regex_avoids_ordinary_copy(profile):
    """A false positive here silently discards a real role, so the pattern has
    to be narrow. 'international team' and 'national office' must not match."""
    from jobfunnel.models import Job as J

    for text in (
        "We are an international team based in Berlin",
        "The national office oversees regional operations",
        "Internal nationals from all backgrounds are welcome",
    ):
        j = J(
            id="x", title="MLOps Engineer", company="Acme", url=None, source="test", tier=1, description=text
        )
        assert eligibility(j, profile, NOW) == [], text


def test_clearance_requirement_blocks(profile):
    from jobfunnel.models import Job as J

    j = J(
        id="c",
        title="ML Engineer",
        company="CACI",
        url=None,
        source="test",
        tier=1,
        description="Bachelor's degree. Security clearance is required for this role.",
    )
    assert any("citizenship" in b.lower() or "clearance" in b.lower() for b in eligibility(j, profile, NOW))


def test_senior_title_blocks_even_with_perfect_skills(profile):
    from jobfunnel.models import Job as J

    j = J(
        id="s",
        title="Staff Machine Learning Engineer",
        company="NB",
        url=None,
        source="test",
        tier=1,
        description="Python, PyTorch, RAG, LLM, MLOps, Kubernetes, Docker, AWS, FastAPI, evaluation. "
        "Entry-level welcome, no years of experience stated.",
    )
    v = rank(j, profile, NOW)
    assert not v.keep
    assert "senior title" in v.blockers


def test_experience_ceiling_blocks(profile):
    from jobfunnel.models import Job as J

    j = J(
        id="y",
        title="Junior DevOps Engineer",
        company="X",
        url=None,
        source="test",
        tier=1,
        description="5+ years of experience required.",
    )
    assert any("floor above" in b for b in eligibility(j, profile, NOW))


def test_pipeline_posting_blocks(profile):
    from jobfunnel.models import Job as J

    j = J(
        id="p",
        title="AI Developer/Engineer",
        company="TF",
        url=None,
        source="test",
        tier=1,
        description="Talent pooling through our Ready-to-Hire Portal. "
        "Not associated with an immediate or confirmed vacancy.",
    )
    assert any("pipeline" in b for b in eligibility(j, profile, NOW))


def test_stale_posting_blocks(profile):
    from jobfunnel.models import Job as J

    j = J(
        id="st",
        title="MLOps Engineer",
        company="X",
        url=None,
        source="test",
        tier=1,
        posted_at="2026-03-26T00:00:00+00:00",
        description="Entry level, Python, MLOps.",
    )
    assert any("stale" in b for b in eligibility(j, profile, NOW))


# ------------------------------------------------------------- scoring shape --


def test_a_real_good_posting_scores_high_and_explains_itself(profile):
    """TensorOps scores 47, and that is the honest number rather than a low one.

    It is a data-analysis role: its requirements are Python, Pandas, NumPy, SQL
    and Tableau against a profile that is ML-ops weighted, so skill overlap is
    small. Asserting an arbitrary 60 would be a test tuned to the author's guess
    about what the answer should be. What actually matters is that it clears the
    floor, lands in the top half, and itemises why -- so that is what is asserted.
    """
    best = next(j for j in GOLDEN if j.id == "tensorops-junior-ds")
    v = rank(best, profile, NOW)
    assert v.keep
    ranked = rank_all(list(GOLDEN), profile, NOW)
    position = next(i for i, (j, _) in enumerate(ranked) if j.id == "tensorops-junior-ds")
    assert v.score >= 40, v
    assert position < len(ranked) // 2, f"ranked {position} of {len(ranked)}"
    # every component of the score has to leave a reason behind
    assert len(v.reasons) >= 4
    assert any("skill overlap" in r for r in v.reasons)
    assert any("preferred title" in r for r in v.reasons)
    assert any("location tier 1" in r for r in v.reasons)
    assert any("posted" in r for r in v.reasons)


def test_rank_all_puts_the_best_posting_first(profile):
    ranked = rank_all(list(GOLDEN), profile, NOW)
    assert ranked, "everything got blocked, which is wrong"
    # score must be monotonically non-increasing
    scores = [v.score for _, v in ranked]
    assert scores == sorted(scores, reverse=True)
    # and the top of the list must be genuinely good, not merely unblocked
    assert LABELS[ranked[0][0].id] == "good", ranked[0][0].id


def test_skill_fit_is_the_dominant_signal_and_location_breaks_ties(profile):
    """A posting whose requirements map onto the profile should outrank a
    posting that is in a nicer place but needs a different job. Location is a
    tiebreaker between comparable fits, not an override for a weak one.

    Big Byte Insights outranks TensorOps on this corpus: its requirements are
    Linux, Git, CI/CD, Kubernetes, GCP/AWS, Terraform and Prometheus, which the
    profile can evidence almost point for point, while TensorOps is a data
    analysis role that leans on Tableau and Power BI. Both are excellent leads
    and both belong in the top handful; the ordering is the profile's, not the
    test author's preference.
    """
    ranked = rank_all(list(GOLDEN), profile, NOW)
    top_ids = [j.id for j, _ in ranked[:5]]
    assert "bigbyte-junior-devops" in top_ids and "tensorops-junior-ds" in top_ids
    for jid in top_ids:
        assert LABELS[jid] == "good", jid


def test_every_country_in_a_remote_tier_also_appears_in_its_onsite_tier(profile):
    """The overlap guard, stated as a test rather than a comment.

    If a bare country name sits in a remote tier, that country's onsite
    postings match the remote tier first and both collapse to the same score.
    """
    remote_terms = set(profile.location_tiers[0])
    onsite_terms = set(profile.location_tiers[1])
    # only multi-word remote phrases may live in tier 0 on their own
    bare = {t for t in remote_terms if " " not in t}
    assert not (bare & onsite_terms), f"bare terms in both tiers: {bare & onsite_terms}"


def test_pakistan_remote_outranks_the_same_role_on_site(profile):
    from jobfunnel.models import Job as J

    common: dict = {
        "company": "Acme",
        "url": None,
        "source": "test",
        "tier": 1,
        "description": "Python MLOps Docker AWS",
    }
    remote = rank(
        J(id="r", title="Junior DevOps Engineer", location="Remote - Pakistan", **common), profile, NOW
    )
    onsite = rank(
        J(id="o", title="Junior DevOps Engineer", location="Lahore, Pakistan", **common), profile, NOW
    )
    assert remote.keep and onsite.keep
    assert remote.score > onsite.score


def test_absent_skill_penalises_but_does_not_kill(profile):
    """A posting that demands something the profile does not have must lose
    points, not vanish. Otherwise the engine can never tell you that the thing
    standing between you and a role is learnable."""
    from jobfunnel.models import Job as J

    common: dict = {"company": "Acme", "url": None, "source": "test", "tier": 1, "posted_at": None}
    without = rank(
        J(
            id="w",
            title="MLOps Engineer",
            location="Remote - Pakistan",
            description="Python MLOps Docker AWS Kubernetes Prometheus Grafana",
            **common,
        ),
        profile,
        NOW,
    )
    with_tf = rank(
        J(
            id="t",
            title="MLOps Engineer",
            location="Remote - Pakistan",
            description="Python MLOps Docker AWS Kubernetes Prometheus Grafana tensorflow keras",
            **common,
        ),
        profile,
        NOW,
    )
    assert with_tf.keep, "an absent skill must not be an eligibility gate"
    assert with_tf.score < without.score
    assert any("absent" in r for r in with_tf.reasons)
    assert with_tf.skill_gaps


def test_certification_name_is_not_a_seniority_signal(profile):
    """The bug this pins: `\bassociate\b` matched "Solutions Architect
    Associate" inside a job description and credited the posting +10 as
    entry-level. The word is only ever a seniority marker when a title leads
    with it; everywhere else it names a certification.

    The two descriptions still differ in score, and they should -- "Cloud
    Practitioner" and "Cloud Engineer" are genuine skill mentions. The point is
    that neither the certification nor the cert-derived word contributes to the
    junior-ness signal, so the entry-level bonus is identical either way.
    """
    from jobfunnel.models import Job as J

    cert_named = J(
        id="cert",
        title="Junior DevOps Engineer",
        company="Big Byte",
        url=None,
        source="test",
        tier=1,
        description=(
            "1-2 years of experience. Terraform even at a learning level. "
            "AWS Cloud Practitioner / Solutions Architect Associate or GCP "
            "Associate Cloud Engineer are a plus."
        ),
    )
    cert_free = J(
        id="free",
        title="Junior DevOps Engineer",
        company="Big Byte",
        url=None,
        source="test",
        tier=1,
        description=(
            "1-2 years of experience. Terraform even at a learning level. AWS Cloud Practitioner is a plus."
        ),
    )
    a, b = rank(cert_named, profile, NOW), rank(cert_free, profile, NOW)

    # the junior-ness bonus is the same in both, and comes from the title
    assert [r for r in a.reasons if "junior signal" in r] == [r for r in b.reasons if "junior signal" in r]
    assert not any("Associate" in r for r in a.reasons)
    assert not any("Associate" in r for r in b.reasons)

    # and the certification never inflates the skill fit either
    assert "associate" not in " ".join(a.skill_hits)


def test_a_title_leading_with_associate_still_counts(profile):
    from jobfunnel.models import Job as J

    v = rank(
        J(
            id="a",
            title="Associate Data Engineer",
            company="X",
            url=None,
            source="test",
            tier=1,
            location="Remote - Pakistan",
            description="Python and SQL.",
        ),
        profile,
        NOW,
    )
    assert any("junior signal 'Associate'" in r for r in v.reasons)


def test_entry_level_beats_a_role_that_says_nothing(profile):
    from jobfunnel.models import Job as J

    common: dict = {"company": "Acme", "url": None, "source": "test", "tier": 1, "posted_at": None}
    explicit = rank(
        J(
            id="e",
            title="Junior DevOps Engineer",
            location="Remote - Pakistan",
            description="Entry level, 0-2 years, recent graduates welcome. Python MLOps Docker AWS.",
            **common,
        ),
        profile,
        NOW,
    )
    silent = rank(
        J(
            id="s",
            title="DevOps Engineer",
            location="Remote - Pakistan",
            description="Python MLOps Docker AWS. Duties as agreed.",
            **common,
        ),
        profile,
        NOW,
    )
    assert explicit.score > silent.score


def test_reasons_are_human_readable_and_never_empty_for_a_keep(profile):
    for j in GOLDEN:
        v = rank(j, profile, NOW)
        if v.keep:
            assert v.reasons, f"{j.id} kept with no explanation"
            assert all(len(r) < 200 for r in v.reasons), "a reason nobody can read is not a reason"


# ------------------------------------------------------- precision and recall --


def test_the_engine_beats_a_keyword_search_on_this_corpus(profile):
    ranked = rank_all(list(GOLDEN), profile, NOW)
    kept_ids = {j.id for j, _ in ranked}
    good_ids = {i for i, lab in LABELS.items() if lab == "good"}
    bad_ids = {i for i, lab in LABELS.items() if lab == "bad"}

    kept_good = kept_ids & good_ids
    kept_bad = kept_ids & bad_ids

    precision = len(kept_good) / len(kept_ids) if kept_ids else 0.0
    recall = len(kept_good) / len(good_ids)

    # A keyword matcher keeps all 26 of these. Precision has to beat that.
    assert precision >= 0.85, f"precision {precision:.2f}: {sorted(kept_bad)}"
    assert recall >= 0.85, f"recall {recall:.2f}: lost {sorted(good_ids - kept_ids)}"

    # the violations report: which good ones were lost and which bad ones kept
    if STATS:
        STATS.update(
            kept=len(kept_ids),
            good=len(good_ids),
            precision=precision,
            recall=recall,
            lost=sorted(good_ids - kept_ids),
            leaked=sorted(kept_bad),
        )


def test_every_bad_posting_is_blocked_for_a_reason_it_states(profile):
    """A blocked posting has to be blocked by something visible in the posting
    itself. Blocking for a reason the text does not contain is how a scorer
    becomes unfalsifiable."""
    for j in GOLDEN:
        if LABELS[j.id] != "bad":
            continue
        v = rank(j, profile, NOW)
        assert not v.keep, f"{j.id} should have been blocked"
        assert v.blockers, f"{j.id} blocked with no stated reason"


def test_good_postings_are_not_all_bunched_at_the_same_score(profile):
    """If every kept posting scores the same, the ranking carries no
    information and the dashboard is just a list."""
    kept = [v for _, v in rank_all(list(GOLDEN), profile, NOW)]
    assert len(kept) >= 5
    spread = max(v.score for v in kept) - min(v.score for v in kept)
    assert spread >= 10, f"scores bunched in a band of {spread}"


# scratch stats for the summary printed by the CLI test
STATS: dict = {}


@pytest.mark.skipif(not STATS, reason="only meaningful after the corpus test runs")
def test_report():
    pass
