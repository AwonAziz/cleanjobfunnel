"""Config loading and validation for cleanjobfunnel.

The config file (config/companies.json by default) is plain JSON:

    {
      "companies":       [ {"name": ..., "ats": ..., "token": ..., "group": ...} ],
      "feeds":           { "remoteok": {"enabled": true}, ... },
      "role_keywords":   [ ... ],        # any-of, word-boundary aware
      "synonyms":        { "mlops": ["machine learning operations", ...] },
      "exclude_keywords":[ ... ],        # any-of -> hard drop
      "location_allow":  [ ... ],        # substring, case-insensitive
      "location_exclude":[ ... ],        # substring, case-insensitive
      "seniority_flags": [ ... ],        # substring -> is_senior
      "min_score":       0                # 0..100, optional
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .defaults import FEED_DEFAULTS

REQUIRED_KEYS = (
    "companies",
    "role_keywords",
    "location_allow",
    "seniority_flags",
)


class ConfigError(ValueError):
    """Raised when the config file cannot be used at all."""


@dataclass
class Company:
    name: str
    ats: str
    token: str
    group: str | None = None
    verified: bool = False
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class Feed:
    name: str
    enabled: bool = True
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class Config:
    companies: list[Company]
    role_keywords: list[str]
    location_allow: list[str]
    seniority_flags: list[str]
    synonyms: dict[str, list[str]] = field(default_factory=dict)
    exclude_keywords: list[str] = field(default_factory=list)
    location_exclude: list[str] = field(default_factory=list)
    min_score: int = 0
    feeds: dict[str, Feed] = field(default_factory=dict)
    path: Path | None = None
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path, known_ats: set[str], known_feeds: set[str]) -> Config:
        path = Path(path)
        if not path.exists():
            raise ConfigError(f"config not found: {path}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ConfigError(f"config is not valid JSON ({path}): {e}") from e
        if not isinstance(raw, dict):
            raise ConfigError(f"config root must be a JSON object ({path})")
        return cls.from_dict(raw, path=path, known_ats=known_ats, known_feeds=known_feeds)

    @classmethod
    def from_dict(
        cls,
        raw: dict[str, Any],
        path: Path | None = None,
        known_ats: set[str] | None = None,
        known_feeds: set[str] | None = None,
    ) -> Config:
        known_ats = known_ats or set()
        known_feeds = known_feeds or set()

        missing = [k for k in REQUIRED_KEYS if k not in raw]
        if missing:
            raise ConfigError(f"config is missing required key(s): {', '.join(missing)}")

        for key in (
            "role_keywords",
            "location_allow",
            "seniority_flags",
            "exclude_keywords",
            "location_exclude",
        ):
            value = raw.get(key, [])
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ConfigError(f"'{key}' must be a list of strings")

        companies: list[Company] = []
        for i, entry in enumerate(raw["companies"]):
            if not isinstance(entry, dict):
                raise ConfigError(f"companies[{i}] must be an object")
            for key in ("name", "ats", "token"):
                if not entry.get(key):
                    raise ConfigError(f"companies[{i}] is missing '{key}'")
            ats = str(entry["ats"]).strip().lower()
            if known_ats and ats not in known_ats:
                raise ConfigError(
                    f"companies[{i}] ({entry['name']}): unknown ats '{entry['ats']}'. Known: {', '.join(sorted(known_ats))}"
                )
            options = entry.get("options") or {}
            if not isinstance(options, dict):
                raise ConfigError(f"companies[{i}] ({entry['name']}): 'options' must be an object")
            companies.append(
                Company(
                    name=str(entry["name"]),
                    ats=ats,
                    token=str(entry["token"]),
                    group=entry.get("group"),
                    verified=bool(entry.get("verified", False)),
                    options=options,
                )
            )

        synonyms_raw = raw.get("synonyms", {}) or {}
        if not isinstance(synonyms_raw, dict):
            raise ConfigError("'synonyms' must be an object mapping canonical -> [variants]")
        synonyms: dict[str, list[str]] = {}
        for canonical, variants in synonyms_raw.items():
            if isinstance(variants, str):
                variants = [variants]
            if not isinstance(variants, list) or not all(isinstance(v, str) for v in variants):
                raise ConfigError(f"synonyms['{canonical}'] must be a list of strings")
            variants = [v.strip() for v in variants if v.strip()]
            synonyms[str(canonical)] = variants

        min_score = raw.get("min_score", 0)
        if not isinstance(min_score, int) or isinstance(min_score, bool) or not 0 <= min_score <= 100:
            raise ConfigError("'min_score' must be an integer 0..100")

        feeds_raw = raw.get("feeds") or {}
        if not isinstance(feeds_raw, dict):
            raise ConfigError("'feeds' must be an object")
        feeds: dict[str, Feed] = {}
        warnings: list[str] = []
        merged = {**FEED_DEFAULTS, **feeds_raw}
        for name, spec in merged.items():
            if known_feeds and name not in known_feeds:
                raise ConfigError(f"unknown feed '{name}'. Known: {', '.join(sorted(known_feeds))}")
            if spec is None:
                spec = {}
            if isinstance(spec, bool):
                spec = {"enabled": spec}
            if not isinstance(spec, dict):
                raise ConfigError(f"feeds['{name}'] must be an object")
            feeds[name] = Feed(
                name=name,
                enabled=bool(spec.get("enabled", True)),
                options={k: v for k, v in spec.items() if k != "enabled"},
            )
        if not any(f.enabled for f in feeds.values()):
            warnings.append("no aggregator feeds are enabled - only Tier 1 company boards will be scanned")

        cfg = cls(
            companies=companies,
            role_keywords=list(raw["role_keywords"]),
            location_allow=list(raw.get("location_allow", [])),
            seniority_flags=list(raw["seniority_flags"]),
            synonyms=synonyms,
            exclude_keywords=list(raw.get("exclude_keywords", [])),
            location_exclude=list(raw.get("location_exclude", [])),
            min_score=min_score,
            feeds=feeds,
            path=path,
            warnings=warnings,
        )
        cfg._extra_warnings()
        return cfg

    def _extra_warnings(self) -> None:
        if not self.role_keywords:
            self.warnings.append("'role_keywords' is empty - every job from every source will match")
        seen: dict[tuple[str, str], str] = {}
        for c in self.companies:
            key = (c.ats, c.token)
            if key in seen:
                self.warnings.append(
                    f"duplicate board: {c.name} and {seen[key]} share ats={c.ats} token={c.token}"
                )
            seen[key] = c.name

    def enabled_feeds(self) -> list[Feed]:
        return [f for f in self.feeds.values() if f.enabled]
