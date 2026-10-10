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
    # Full posting text where a source provides it. Only the relevance scorer
    # reads this, because it is the only thing that can tell a genuine
    # entry-level role from one that says "junior" and means three years.
    # Sources that cannot supply it leave it None, and scoring degrades to
    # title and location rather than guessing.
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
