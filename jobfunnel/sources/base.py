"""Shared helpers for source fetchers."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any

# Descriptions are kept out of regex-accelerated HTML parsing on purpose: the
# boards return enough variety of markup that a real parser would be worth its
# dependency, but the two things we actually need from a description -- distinct
# words and years-of-experience figures -- survive a crude strip fine.
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"\s+")


def strip_html(raw: Any, limit: int = 8000) -> str | None:
    """Flatten a posting's HTML body to capped plain text.

    The cap stops the scorer from holding a full 20k posting in memory for
    every one of 8,665 fetched jobs, but it is generous enough to reach the
    requirements and qualifications sections where the experience figure and
    the must-have skills actually live. 1200 was too tight -- it cut off
    before any of that, and every job scored as though it had no requirements
    at all, which flattered nothing and told the reader nothing.

    This text is scoring input only. jobs.json is committed on every scan and
    stores the verdict, not the evidence, so the cap costs repository space
    no matter how large it is.
    """
    if not raw or not isinstance(raw, str):
        return None
    text = _SCRIPT_RE.sub(" ", raw)
    text = _TAG_RE.sub(" ", text)
    text = unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:limit] or None


def stable_id(*parts: Any) -> str:
    raw = "|".join(str(p) for p in parts if p is not None)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def get_first(d: Any, *keys: str, default: Any = None) -> Any:
    """First present, non-empty value among `keys`, or `default`.

    Handles both our own objects and the dicts that come straight off the
    APIs, which is why `d` is typed Any.
    """
    for k in keys:
        v = getattr(d, k, None) if not isinstance(d, dict) else d.get(k)
        if isinstance(v, str):
            v = v.strip()
        if v not in (None, "", [], {}, 0):
            return v
    return default


def to_iso(dt_value: Any) -> str | None:
    """Best-effort normalize epoch/ISO/RFC-822 dates to an ISO 8601 UTC string."""
    if dt_value is None:
        return None

    if isinstance(dt_value, bool):  # bools are ints in Python - guard explicitly
        return None

    if isinstance(dt_value, (int, float)):
        ts = dt_value / 1000 if dt_value > 10_000_000_000 else dt_value
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except (ValueError, OSError, OverflowError):
            return None

    if isinstance(dt_value, str):
        s = dt_value.strip()
        if not s:
            return None
        try:  # RFC 822, e.g. RSS <pubDate>
            parsed = parsedate_to_datetime(s)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            pass
        try:  # ISO 8601 (also handles "2026-10-06 13:46:11 UTC" via slash below)
            s2 = s.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(s2)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
        try:  # trailing " UTC" style
            parsed = datetime.fromisoformat(s.replace(" UTC", "+00:00"))
            return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            return None

    return None


def clean_tags(raw: Any) -> list[str]:
    """Normalize tags payloads (list or comma/space string) to a list[str]."""
    if not raw:
        return []
    if isinstance(raw, str):
        items = re.split(r"[,;|]", raw)
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        return []
    out: list[str] = []
    for t in items:
        t = str(t).strip()
        if t:
            out.append(t)
    return out


def coerce_location(value: Any) -> str | None:
    """Defensive: sources hand back strings, dicts, or lists for location."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in ("name", "location", "city"):
            if value.get(key):
                return str(value[key])
        country = value.get("country")
        if isinstance(country, dict) and country.get("name"):
            return str(country["name"])
        return None
    if isinstance(value, (list, tuple)):
        parts = [coerce_location(v) for v in value]
        parts = [p for p in parts if p]
        return ", ".join(parts) if parts else None
    return str(value)


def format_salary(
    low: Any = None,
    high: Any = None,
    currency: str | None = None,
    period: str | None = None,
) -> str | None:
    """Format a salary range like '$120k-131k/yr' or 'USD 120000-131500'."""

    def fmt(v: Any) -> str | None:
        if v in (None, "") or v == 0:
            return None
        try:
            n = int(float(v))
        except (TypeError, ValueError):
            return str(v)
        if n >= 1000:
            return f"{n // 1000}k" if n % 1000 == 0 else f"{n / 1000:.1f}k"
        return str(n)

    lo, hi = fmt(low), fmt(high)
    if not lo and not hi:
        return None
    cur = (currency or "").upper()
    rng = f"{lo}-{hi}" if lo and hi and lo != hi else (lo or hi or "")
    parts = [p for p in (cur, rng) if p]
    out = " ".join(parts)
    if period and str(period).lower() not in ("year", "yearly", "annually", ""):
        out = f"{out}/{period}"
    return out or None
