"""Core data model shared by fetchers, matchers, the store, and the CLI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Job:
    """One open role, normalised across every source."""

    id: str
    title: str
    company: str
    url: str | None
    source: str
    tier: int
    location: str | None = None
    group: str | None = None
    posted_at: str | None = None
    salary: str | None = None
    tags: list[str] = field(default_factory=list)
    match_reasons: list[str] = field(default_factory=list)
    score: int = 0
    is_senior: bool = False
    also_on: list[str] = field(default_factory=list)
    first_seen: str | None = None
    # Relevance verdict, written by scan once the keyword matcher and dedupe have
    # both had their turn. Kept as a dict rather than fields so the two scoring
    # systems stay separable: `score`/`match_reasons` above are the keyword
    # matcher's, and everything under here is resume-fit.
    relevance: dict[str, Any] = field(default_factory=dict)
    # Full posting text where a source provides it. Only the relevance scorer
    # reads this, because it is the only thing that can tell a genuine
    # entry-level role from one that says "junior" and means three years.
    # Sources that cannot supply it leave it None, and scoring degrades to
    # title and location rather than guessing.
    description: str | None = None
    # A structured experience level, where the board publishes one. Both
    # SmartRecruiters (experienceLevel: internship/entry_level/associate/
    # mid_senior_level/...) and Personio (yearsOfExperience: "7-10") do, and
    # trusting a labelled field beats regexing it out of prose.
    experience_years: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_dashboard_dict(self) -> dict[str, Any]:
        """Everything in to_dict() except the description.

        jobs.json is committed on every scan, so a body of prose per job would
        grow the repository every twenty minutes for text nothing reads past
        the first paragraph of. The description has already done its job by
        the time this runs -- the scorer has read it and written a verdict into
        `relevance`. What the dashboard needs is the verdict, not the evidence.
        """
        d = self.to_dict()
        d.pop("description", None)
        return d


@dataclass
class SourceReport:
    """Outcome of talking to one source (a company board or an aggregator)."""

    source: str
    ok: bool
    count: int
    error: str | None = None
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
