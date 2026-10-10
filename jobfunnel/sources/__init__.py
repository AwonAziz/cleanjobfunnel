"""Registry of every source the scanner knows how to talk to.

ATS_FETCHERS serves config/companies.json entries (keyed by "ats");
FEED_FETCHERS serves aggregator feeds (keyed by feed name, aligned with
the keys in config's "feeds" object).
"""

from __future__ import annotations

from ..defaults import FEED_DEFAULTS
from .ats import (
    fetch_ashby,
    fetch_breezy,
    fetch_greenhouse,
    fetch_lever,
    fetch_personio,
    fetch_recruitee,
    fetch_smartrecruiters,
)
from .feeds import (
    fetch_himalayas,
    fetch_hn_whoshiring,
    fetch_remoteok,
    fetch_remotive,
    fetch_weworkremotely,
    fetch_workingnomads,
)

ATS_FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "recruitee": fetch_recruitee,
    "breezy": fetch_breezy,
    "personio": fetch_personio,
}

# each takes (http, options) and returns (jobs, error)
FEED_FETCHERS = {
    "remoteok": lambda http, opts: fetch_remoteok(http),
    "remotive": lambda http, opts: fetch_remotive(
        http, category=opts.get("category", FEED_DEFAULTS["remotive"]["category"])
    ),
    "himalayas": lambda http, opts: fetch_himalayas(http, limit=int(opts.get("limit", 200))),
    "workingnomads": lambda http, opts: fetch_workingnomads(http),
    "hn_whoshiring": lambda http, opts: fetch_hn_whoshiring(
        http, threads=int(opts.get("threads", FEED_DEFAULTS["hn_whoshiring"]["threads"]))
    ),
    "weworkremotely": lambda http, opts: fetch_weworkremotely(http),
}
