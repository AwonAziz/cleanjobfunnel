"""Matching engine: keyword/synonym/location rules, scoring, explanations.

Design notes:
- Keywords match on word boundaries with light stemming (engineer ->
  engineers/engineering), so "SRE" no longer accidentally matches
  unrelated words and "Platform Engineer" matches "Platform Engineering".
- synonyms map a canonical name to alternate spellings; a hit on either
  scores the same canonical term, and every hit is recorded as a
  human-readable reason so the dashboard can explain *why* a job matched.
- exclude_keywords are hard drops. location_exclude is only consulted
  after location_allow lets the job through.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import Config
from .models import Job

# suffixes that still count as a hit for the base word
_SUFFIX = r"(?:s|es|ing)?"


def _pattern(variants: list[str]) -> re.Pattern[str]:
    parts = []
    for v in variants:
        # lookarounds instead of \b: they behave for tokens containing
        # punctuation like "AI/ML", "+", or ".NET"
        parts.append(r"(?<![A-Za-z0-9])" + re.escape(v.strip()) + _SUFFIX + r"(?![A-Za-z0-9])")
    return re.compile("|".join(parts), re.IGNORECASE)


@dataclass
class RuleSet:
    keywords: list[tuple[str, re.Pattern[str]]] = field(default_factory=list)  # canonical -> pattern
    excludes: list[tuple[str, re.Pattern[str]]] = field(default_factory=list)
    location_allow: list[str] = field(default_factory=list)
    location_exclude: list[tuple[str, re.Pattern[str]]] = field(default_factory=list)
    seniority_flags: list[str] = field(default_factory=list)
    min_score: int = 0

    @classmethod
    def from_config(cls, config: Config) -> RuleSet:
        keywords: list[tuple[str, re.Pattern[str]]] = []
        for kw in config.role_keywords:
            variants = [kw]
            for alt in config.synonyms.get(kw, []):
                if alt.lower() != kw.lower():
                    variants.append(alt)
            keywords.append((kw, _pattern(variants)))
        excludes = [(x, _pattern([x])) for x in config.exclude_keywords]
        # location excludes use word boundaries so short codes like "us" or "uk"
        # can be blocked without nuking "focus"/"ukulele" inside longer strings
        loc_excludes = [
            (x, re.compile(rf"(?<![A-Za-z0-9]){re.escape(x.strip())}(?![A-Za-z0-9])", re.IGNORECASE))
            for x in config.location_exclude
        ]
        return cls(
            keywords=keywords,
            excludes=excludes,
            location_allow=[a.lower() for a in config.location_allow],
            location_exclude=loc_excludes,
            seniority_flags=[s.lower().strip() for s in config.seniority_flags],
            min_score=config.min_score,
        )


@dataclass
class Verdict:
    keep: bool
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    is_senior: bool = False
    dropped_by: str | None = None  # "keyword" | "location" | "excluded" | "score"


REMOTE_WORDS = ("remote", "worldwide", "anywhere", "global", "distributed")


def _rel_posted(posted_at: str | None, now: datetime) -> tuple[str | None, int]:
    """Return (human snippet, bonus) for how recently the job was posted."""
    if not posted_at:
        return None, 0
    try:
        dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
    except ValueError:
        return None, 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    days = (now - dt).days
    if days <= 7:
        return f"posted {max(days, 0)}d ago", 10
    if days <= 30:
        return f"posted {days}d ago", 5
    return None, 0


def evaluate(job: Job, rules: RuleSet, now: datetime | None = None) -> Verdict:
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    score = 0

    # hard excludes first - cheapest and most decisive
    title = job.title or ""
    for name, pattern in rules.excludes:
        if pattern.search(title):
            return Verdict(keep=False, dropped_by="excluded", reasons=[f'title excluded by "{name}"'])

    # keyword / synonym hit
    matched_any = False
    title_bonus = 0
    title_lower = title.lower()
    for canonical, pattern in rules.keywords:
        hit = pattern.search(title)
        if hit:
            matched_any = True
            title_bonus += 30
            reasons.append(f'title matches "{canonical}"')
            # note when the hit came from a synonym rather than the literal keyword
            if canonical.lower() not in title_lower:
                reasons.append(f'via alias "{hit.group(0).strip()}"')
    if rules.keywords and not matched_any:
        return Verdict(keep=False, dropped_by="keyword")
    title_bonus = min(50, title_bonus)
    score += title_bonus

    # location
    loc = (job.location or "").strip()
    if loc:
        loc_lower = loc.lower()
        allowed = not rules.location_allow or any(a in loc_lower for a in rules.location_allow)
        if not allowed:
            return Verdict(keep=False, dropped_by="location", reasons=[f'location "{loc}" not in allow list'])
        blocked = next((name for name, pattern in rules.location_exclude if pattern.search(loc_lower)), None)
        if blocked:
            return Verdict(
                keep=False, dropped_by="location", reasons=[f'location "{loc}" excluded by "{blocked}"']
            )
        if any(w in loc_lower for w in REMOTE_WORDS):
            score += 10
            reasons.append("remote-friendly location")
    else:
        score += 10  # ambiguous -> assume remote-friendly, never silently drop
        reasons.append("remote-friendly location (unspecified)")

    # tier
    if job.tier == 1:
        score += 15
        reasons.append("direct from company board")

    # recency
    snippet, bonus = _rel_posted(job.posted_at, now)
    if bonus:
        score += bonus
        reasons.append(snippet)

    score = max(0, min(100, score))

    # seniority (flag only, never drops)
    t = title.lower()
    is_senior = any(f and f in t for f in rules.seniority_flags)
    if is_senior:
        reasons.append("senior-leaning")

    keep = score >= rules.min_score
    return Verdict(
        keep=keep,
        score=score,
        reasons=reasons,
        is_senior=is_senior,
        dropped_by=None if keep else "score",
    )
