"""Shared HTTP layer: retries with backoff, per-host politeness, ETag cache.

Every fetcher goes through this, so the whole pipeline gets the same
resilience and good-neighbour behaviour for free.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter

log = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = ".cache/http"
DEFAULT_TIMEOUT = 20
DEFAULT_POLITE_DELAY = 0.3  # seconds between requests to the same host
DEFAULT_CACHE_TTL = 900  # seconds; local re-runs inside the TTL skip the network
RETRY_STATUS = (429, 500, 502, 503, 504)


class HttpError(requests.RequestException):
    """A request failed after retries (or was a non-retryable HTTP error)."""


class Http:
    """Thin wrapper around requests.Session with retry + cache + politeness."""

    def __init__(
        self,
        cache_dir: str | Path | None = DEFAULT_CACHE_DIR,
        timeout: int = DEFAULT_TIMEOUT,
        polite_delay: float = DEFAULT_POLITE_DELAY,
        retries: int = 3,
        cache_ttl: int = DEFAULT_CACHE_TTL,
        user_agent: str | None = None,
    ) -> None:
        self.timeout = timeout
        self.polite_delay = polite_delay
        self.cache_ttl = cache_ttl
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._lock = threading.Lock()
        self._host_last: dict[str, float] = {}
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": user_agent
                or "job-funnel/2.0 (+https://github.com/AwonAziz/cleanjobfunnel; personal job search tool)",
                "Accept": "application/json, text/plain, */*",
            }
        )
        if retries > 0:
            retry = requests.adapters.Retry(
                total=retries,
                connect=retries,
                read=retries,
                backoff_factor=1.0,
                status_forcelist=RETRY_STATUS,
                allowed_methods=frozenset({"GET"}),
                respect_retry_after_header=True,
            )
            adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=16)
            self._session.mount("https://", adapter)
            self._session.mount("http://", adapter)
        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_dirty = False
        self._cache_file = self.cache_dir / "responses.json" if self.cache_dir else None
        if self._cache_file:
            self._cache = self._load_cache()
        self.stats = {"cache_hits": 0}

    # ------------------------------------------------------------- cache --

    def _load_cache(self) -> dict[str, dict[str, Any]]:
        try:
            return json.loads(self._cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_cache(self) -> None:
        if not self._cache_file or not self._cache_dirty:
            return
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self._cache_file.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._cache, fh)
            os.replace(tmp, self._cache_file)
            self._cache_dirty = False
        except OSError as e:
            log.debug("could not persist HTTP cache: %s", e)

    def close(self) -> None:
        self._save_cache()
        self._session.close()

    def __enter__(self) -> Http:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------ helpers --

    def _wait_polite(self, url: str) -> None:
        if self.polite_delay <= 0:
            return
        from urllib.parse import urlparse

        host = urlparse(url).netloc
        with self._lock:
            last = self._host_last.get(host, 0.0)
            delta = time.monotonic() - last
            if delta < self.polite_delay:
                time.sleep(self.polite_delay - delta)
            self._host_last[host] = time.monotonic()

    @staticmethod
    def _cache_key(url: str, params: Any) -> str:
        if not params:
            return url
        try:
            encoded = urlencode(sorted(params.items()))
        except AttributeError:
            encoded = urlencode(sorted(params))
        return f"{url}?{encoded}"

    # -------------------------------------------------------------- calls --

    def _request(
        self,
        url: str,
        params: Any = None,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        self._wait_polite(url)
        try:
            resp = self._session.request(
                "GET",
                url,
                params=params,
                timeout=self.timeout,
                headers=headers,
            )
        except requests.RequestException as e:
            raise HttpError(str(e)) from e
        if resp.status_code >= 400:
            raise HttpError(f"GET {url} -> HTTP {resp.status_code}")
        return resp

    def _cached_get(self, url: str, params: Any, decode: Callable[[requests.Response], Any]) -> Any:
        """Shared GET path: TTL short-circuit, conditional revalidation, cache write.

        `decode` turns the response into the body (resp.json / resp.text); it is
        the only difference between get_json and get_text, so both pump through
        here and can never drift apart.
        """
        key = self._cache_key(url, params)
        entry = self._cache.get(key) if self.cache_dir else None
        if entry and time.time() - entry.get("fetched_at", 0) < self.cache_ttl:
            self.stats["cache_hits"] += 1
            return entry["body"]
        headers: dict[str, str] = {}
        if entry:
            if entry.get("etag"):
                headers["If-None-Match"] = entry["etag"]
            if entry.get("last_modified"):
                headers["If-Modified-Since"] = entry["last_modified"]
        resp = self._request(url, params=params, headers=headers)
        if resp.status_code == 304 and entry:
            self.stats["cache_hits"] += 1
            entry["fetched_at"] = time.time()
            self._cache_dirty = True
            return entry["body"]
        body = decode(resp)
        if self.cache_dir:
            self._cache[key] = {
                "url": url,
                "etag": resp.headers.get("ETag"),
                "last_modified": resp.headers.get("Last-Modified"),
                "fetched_at": time.time(),
                "body": body,
            }
            self._cache_dirty = True
        return body

    def get_json(self, url: str, params: Any = None) -> Any:
        """GET a JSON document, with ETag/TTL caching when a cache dir is set."""
        return self._cached_get(url, params, lambda r: r.json())

    def get_text(self, url: str, params: Any = None) -> str:
        """GET a text/XML document (cached the same way as get_json)."""
        return self._cached_get(url, params, lambda r: r.text)

    def clear_cache(self) -> bool:
        """Delete the on-disk cache. Returns True if anything was removed."""
        if not self.cache_dir or not self.cache_dir.exists():
            return False
        removed = False
        for p in self.cache_dir.glob("*.json"):
            p.unlink(missing_ok=True)
            removed = True
        self._cache = {}
        return removed
