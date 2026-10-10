"""Default settings shared by the config loader and the source registry.

Feed defaults live here exactly once: config.py uses them to fill in feeds a
config file omits, and the registry uses them for option fallbacks, so the
two can never disagree.
"""

from __future__ import annotations

from typing import Any

FEED_DEFAULTS: dict[str, dict[str, Any]] = {
    "remoteok": {"enabled": True},
    "remotive": {"enabled": True, "category": "software-dev"},
    "himalayas": {"enabled": True},
    "workingnomads": {"enabled": True},
    "hn_whoshiring": {"enabled": True, "threads": 1},
    "weworkremotely": {"enabled": True},
}
