"""Resume-profile relevance: eligibility gates, skill fit, explanations.

Distinct from matching.py, which answers "is this the kind of role I want?".
This module answers the two questions a keyword matcher cannot:

  1. Can I actually get this?  -- eligibility gates for the traps a keyword
     search sails straight through: nationality-restricted postings, clearance
     requirements, five-years-in-disguise "junior" roles, and closed deadlines.
  2. Why is this a good fit?   -- skill overlap against a structured profile,
     not just a title keyword hit.

Every verdict carries its reasons, because a ranking nobody can inspect is a
ranking nobody trusts. The golden-set test in test_relevance.py scores this
the same way the rest of the engine is scored: against hand-labelled real
postings, not against the author's intuition.

Design notes worth keeping:

- Experience requirements are read from free text and are a *filter signal*,
  not a promise. Postings routinely say "junior" and mean three years. The
  parser prefers the highest requirement it can see on the assumption that the
  employer will screen against it.
- Nationality gates match on phrases that identify a *requirement*, deliberately
  avoiding substrings that appear in ordinary copy: "national office", "internal
  nationals", "international". A false negative costs one bad lead; a false
  positive silently discards a real role.
- Seniority is encoded twice. matching.py flags it without dropping it, because
  a keyword search should stay honest about what it saw. Here it is a hard gate,
  because for a candidate with a hard experience ceiling a Staff role is not a
  weak match, it is an ineligible one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import Job

# --------------------------------------------------------------------------- #
# Profile
# --------------------------------------------------------------------------- #


@dataclass
class Profile:
    """A structured, honest view of a candidate.

    ``absent`` is not an insult to the candidate, it is a contract with the
    scorer: claiming a skill that does not exist produces exactly the failure
    this engine is built to prevent. Anything listed there is penalised when a
    posting demands it.
    """

    title: str
    max_years: int = 2
    # skills that gate a role, weighted by how fundamental they are
    critical: list[str] = field(default_factory=list)
    # real but secondary
    valuable: list[str] = field(default_factory=list)
    # do not claim; presence in a posting costs score
    absent: list[str] = field(default_factory=list)
    # preferred titles, most preferred first
    titles: list[str] = field(default_factory=list)
    has_degree: bool = False
    # ISO-3166-ish names, most preferred first
    location_tiers: list[list[str]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict) -> Profile:
        def _words(key: str) -> list[str]:
            v = raw.get(key) or []
            return [str(w).strip().lower() for w in v if str(w).strip()]

        tiers = [[str(c).strip().lower() for c in (t or [])] for t in (raw.get("location_tiers") or [])]
        return cls(
            title=str(raw.get("title", "")).strip(),
            max_years=int(raw.get("max_years", 2)),
            critical=_words("critical"),
            valuable=_words("valuable"),
            absent=_words("absent"),
            titles=_words("titles"),
            has_degree=bool(raw.get("has_degree", False)),
            location_tiers=tiers,
        )


# --------------------------------------------------------------------------- #
# Verdict
# --------------------------------------------------------------------------- #


@dataclass
class Verdict:
    keep: bool
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    skill_hits: list[str] = field(default_factory=list)
    skill_gaps: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Experience extraction
# --------------------------------------------------------------------------- #

# "5+ years", "3 to 5 years", "0-2 years", "minimum of 2 years", "two years"
_NUM_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
# group 3 is a hyphen/en-dash range, group 4 a spelled "to" range. Both exist in
# the wild and they are not interchangeable: a hyphen range that silently fails
# to match leaves only the upper bound captured, which turns "1 to 3 years" into
# a three-year requirement and discards the role.
_YEAR_RE = re.compile(
    r"(?<![A-Za-z0-9])(\d{1,2})\s*(\+|plus)?\s*"
    r"(?:[-\u2013]\s*(\d{1,2})|\s+to\s+(\d{1,2}))?\s*(?:years?|yrs?)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_WORD_YEAR_RE = re.compile(
    r"(?<![A-Za-z])(one|two|three|four|five|six|seven|eight|nine|ten)\s*(?:-|to)?\s*(\w+)?\s*(?:years?|yrs?)(?![A-Za-z])",
    re.IGNORECASE,
)


def years_range(text: str | None) -> tuple[int, int] | None:
    """(floor, ceiling) of experience this posting appears to demand, or None.

    ``("1 to 3 years")`` is (1, 3); ``("5+ years")`` is (5, None); a bare
    ``("2 years")`` is (2, 2).

    The ceiling is returned separately from the floor because they answer
    different questions, and conflating them is how a scorer ends up discarding
    roles you can actually win while keeping roles you cannot.
    """
    if not text:
        return None
    floors_ceils: list[tuple[int, int | None]] = []

    for m in _YEAR_RE.finditer(text):
        lo = int(m.group(1))
        has_plus = bool(m.group(2))
        hi = m.group(3) or m.group(4)
        floors_ceils.append((lo, None if has_plus else (int(hi) if hi else None)))

    for m in _WORD_YEAR_RE.finditer(text):
        lo = _NUM_WORDS.get(m.group(1).lower())
        if lo is None:
            continue
        hi = _NUM_WORDS.get((m.group(2) or "").lower())
        floors_ceils.append((lo, hi))

    if not floors_ceils:
        return None

    # An explicit "N+" is a stated minimum, and it dominates a loose range.
    # Otherwise the most reachable stated floor is the honest reading of a
    # range like "1 to 3 years", because one year sits inside that band.
    plus_floors = [f for f, c in floors_ceils if c is None]
    floor = max(plus_floors) if plus_floors else min(f for f, _ in floors_ceils)
    ceiling = max([c for _, c in floors_ceils if c is not None], default=None)
    return floor, ceiling


def required_years(text: str | None) -> int | None:
    """The number a candidate must clear to be considered, or None.

    A stated range gates on its lower end, because "1-3 years" means someone at
    one year is inside the band. An explicit "5+" gates on five, because that is
    the employer's stated floor. This is the whole difference between an engine
    that filters for you and one that filters you out.
    """
    r = years_range(text)
    return None if r is None else r[0]


# --------------------------------------------------------------------------- #
# Eligibility gates
# --------------------------------------------------------------------------- #

# A requirement for a specific nationality or citizenship. Deliberately narrow:
# these must not overlap with ordinary copy about international teams.
_NATIONALITY_RE = re.compile(
    r"(?ix)"
    r"\b(?:uae|emirati|saudi|qatari|kuwaiti|bahraini|omani)\s+national"
    r"|nationals?\s+only"
    r"|\bemiratis?\b"
    r"|must\s+be\s+an?\s+(?:uae|saudi|qatari|kuwaiti|bahraini|omani)\s+citizen"
    r"|uae\s+citizen"
    r"|nationals\s+are\s+encouraged\s+to\s+apply"
    r"|\bemirati-national\b"
    r"|open\s+to\s+emiratis"
)

_CITIZENSHIP_RE = re.compile(
    r"(?ix)"
    r"must\s+be\s+(?:a\s+)?(?:us|u\.s\.|united\s+states)\s+citizen"
    r"|us\s+citizen"
    r"|us\s+persons?\s+only"
    r"|requires?\s+(?:us|u\.s\.)\s+(?:citizenship|work\s+authorization)"
    r"|\b(?:us|u\.s\.)\s+work\s+authorization\s+(?:required|only)"
    r"|must\s+be\s+legally\s+authorized\s+to\s+work\s+in\s+the\s+(?:us|united\s+states)"
    r"|security\s+clearance"
    r"|\bgreen\s+card\s+holder"
)

# A posting that says it is a pipeline rather than a vacancy.
_PIPELINE_RE = re.compile(
    r"(?ix)"
    r"not\s+associated\s+with\s+(?:an?\s+)?(?:immediate|confirmed)\s+vacancy"
    r"|talent\s+(?:pool|pooling)"
    r"|\bready-to-hire\b"
    r"|general\s+applicants?\s+only"
    r"|no\s+specific\s+role"
)

_SENIOR_TITLE_RE = re.compile(
    r"(?ix)"
    r"\b(?:staff|principal|architect|lead|head\s+of|director|vp|vice\s+president|chief|manager|mgr)\b"
    r"|\bsenior\b"
    r"|\bsr\.?\b"
    r"|\blead\b"
)

# Junior-ness is asserted in two places and they need different rules.
#
# A title leads with it, so "Associate DevOps Engineer" and "Junior ML Engineer"
# are unambiguous. A description does not, and "Solutions Architect Associate"
# is a certification rather than a seniority -- scoring it as entry-level
# inflates the wrong job by ten points. So "associate" is a title signal only.
_JUNIOR_TITLE_RE = re.compile(r"^\s*(?:junior|associate|graduate|trainee|jr\.?)\b", re.IGNORECASE)

# Signals that are only ever written about the role, wherever they appear.
_JUNIOR_SIGNAL_RE = re.compile(
    r"(?ix)"
    r"entry[\s-]?level"
    r"|\bjunior\b"
    r"\brecent\s+graduate"
    r"|\bfresh\s+graduate"
    r"|\bgraduate\b"
    r"|\btrainee\b"
    r"early[\s-]?career"
    r"|\bstarting\s+out\b"
    r"\b(?:0|1|2)\s*(?:[-&/]|\s+to\s+)\s*[0123]\s+years?\b"
)


def eligibility(job: Job, profile: Profile, now: datetime | None = None) -> list[str]:
    """Return every reason this job is off-limits. Empty means eligible."""
    now = now or datetime.now(timezone.utc)
    blockers: list[str] = []
    text = getattr(job, "description", None) or ""

    # --- nationality / citizenship, the trap that most cleanly disqualifies ---
    nat = _NATIONALITY_RE.search(text)
    if nat:
        blockers.append(f"nationality-restricted ({nat.group(0).strip()!r})")
    cit = _CITIZENSHIP_RE.search(text)
    if cit:
        blockers.append(f"requires citizenship/clearance ({cit.group(0).strip()!r})")

    # --- seniority in the title ---
    if _SENIOR_TITLE_RE.search(job.title or ""):
        blockers.append("senior title")

    # --- stated experience ---
    span = years_range(text)
    if span is not None:
        floor, ceiling = span
        if floor > profile.max_years:
            ask = f"{floor}-{ceiling}" if ceiling and ceiling != floor else str(floor)
            blockers.append(f"asks {ask} years (floor above {profile.max_years})")

    # --- pipeline rather than vacancy ---
    if text:
        pipe = _PIPELINE_RE.search(text)
        if pipe:
            blockers.append(f"pipeline posting, not a vacancy ({pipe.group(0).strip()!r})")

    # --- closed or stale ---
    if job.posted_at:
        try:
            dt = datetime.fromisoformat(str(job.posted_at).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = (now - dt).days
            if age > 60:
                blockers.append(f"stale, posted {age}d ago")
        except ValueError:
            pass

    return blockers


# --------------------------------------------------------------------------- #
# Skill fit
# --------------------------------------------------------------------------- #


def _skill_pattern(skill: str) -> re.Pattern[str]:
    """Word-boundary-ish match that survives 'ai/ml', 'c++', 'node.js'."""
    return re.compile(
        r"(?<![A-Za-z0-9])" + re.escape(skill.strip()) + r"(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


# skills a candidate wants, in the order that matters to the score
_SKILL_WEIGHTS = {"critical": 4, "valuable": 2}


def _skill_fit(job: Job, profile: Profile) -> tuple[list[str], list[str], int, int]:
    text = " ".join(
        p for p in [(job.title or ""), (getattr(job, "description", None) or ""), *(job.tags or [])] if p
    )
    if not text.strip():
        return [], [], 0, 0

    hits: list[str] = []
    gaps: list[str] = []
    raw = 0
    penalty = 0

    for weight_name, weight in _SKILL_WEIGHTS.items():
        for skill in profile.critical if weight_name == "critical" else profile.valuable:
            if _skill_pattern(skill).search(text):
                hits.append(skill)
                raw += weight

    for skill in profile.absent:
        if _skill_pattern(skill).search(text):
            gaps.append(skill)
            penalty += 5

    return hits, gaps, min(raw, 40), min(penalty, 15)


# --------------------------------------------------------------------------- #
# Location tiering
# --------------------------------------------------------------------------- #

_REMOTE_WORDS = ("remote", "worldwide", "anywhere", "global", "distributed")

# Distance between tiers in points. Wide enough that a genuinely better region
# survives a couple of skill keywords, which is what a preference list is for.
_TIER_STEP = 3
_MAX_LOCATION_BONUS = 15


def _location_bonus(job: Job, profile: Profile) -> tuple[int, list[str]]:
    """Score location on two axes: which region, and remote or not.

    The tier lists are matched in order and the first hit wins, so they must be
    written most-specific-first and must not overlap. A bare country name in a
    remote tier will swallow that country's onsite postings and the two collapse
    into the same score, which is exactly the bug this docstring warns about.
    """
    loc = (job.location or "").lower()
    if not loc:
        return 6, ["location unspecified, assume viable"]

    for idx, tier in enumerate(profile.location_tiers):
        if any(c in loc for c in tier):
            bonus = max(0, _MAX_LOCATION_BONUS - idx * _TIER_STEP)
            remote = any(w in loc for w in _REMOTE_WORDS)
            if remote:
                # cap applies within the tier, so a remote posting in a weaker
                # tier can never outrank a better region
                bonus = min(_MAX_LOCATION_BONUS, bonus + 3)
            note = f"location tier {idx + 1} ({job.location})" + (" + remote" if remote else "")
            return bonus, [note]

    return 0, [f"location {job.location!r} not in preference tiers"]


def _title_bonus(job: Job, profile: Profile) -> tuple[int, list[str]]:
    t = (job.title or "").lower()
    if not t:
        return 0, []
    for idx, want in enumerate(profile.titles):
        if want and want in t:
            return max(0, 20 - idx * 4), [f"preferred title '{want}'"]
    return 0, []


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #


def rank(job: Job, profile: Profile, now: datetime | None = None) -> Verdict:
    """Score one job against the profile, with reasons for every component."""
    now = now or datetime.now(timezone.utc)

    blockers = eligibility(job, profile, now)
    if blockers:
        return Verdict(keep=False, blockers=blockers, reasons=[f"blocked: {b}" for b in blockers])

    text = getattr(job, "description", None) or ""
    score = 0
    reasons: list[str] = []

    skill_hits, skill_gaps, skill_pts, penalty = _skill_fit(job, profile)
    score += skill_pts - penalty
    if skill_hits:
        reasons.append(f"skill overlap {len(skill_hits)}: {', '.join(sorted(skill_hits)[:6])}")
    if skill_gaps:
        reasons.append(f"required but absent from profile: {', '.join(sorted(skill_gaps)[:4])}")

    t_pts, t_reasons = _title_bonus(job, profile)
    score += t_pts
    reasons += t_reasons

    l_pts, l_reasons = _location_bonus(job, profile)
    score += l_pts
    reasons += l_reasons

    # recency decays rather than vanishes
    if job.posted_at:
        try:
            dt = datetime.fromisoformat(str(job.posted_at).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            days = (now - dt).days
            if days <= 7:
                score += 10
                reasons.append(f"posted {max(days, 0)}d ago")
            elif days <= 30:
                score += 5
                reasons.append(f"posted {days}d ago")
        except ValueError:
            pass

    # Junior-ness: the title leads with it, the description says it two ways.
    signal = _JUNIOR_TITLE_RE.match(job.title or "") or (_JUNIOR_SIGNAL_RE.search(text) if text else None)
    if signal:
        score += 10
        reasons.append(f"explicit junior signal {signal.group(0).strip()!r}")

    if job.tier == 1:
        score += 5
        reasons.append("direct from company board")

    return Verdict(
        keep=True,
        score=max(0, min(100, score)),
        reasons=reasons,
        skill_hits=skill_hits,
        skill_gaps=skill_gaps,
    )


def rank_all(jobs: list[Job], profile: Profile, now: datetime | None = None) -> list[tuple[Job, Verdict]]:
    """Rank every job, dropping the ineligible, best first."""
    now = now or datetime.now(timezone.utc)
    out: list[tuple[Job, Verdict]] = []
    for j in jobs:
        v = rank(j, profile, now)
        if v.keep:
            out.append((j, v))
    out.sort(key=lambda pair: (-pair[1].score, pair[0].title or ""))
    return out
