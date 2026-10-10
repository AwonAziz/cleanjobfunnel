"""Shared fixtures for the offline test suite.

Source fetchers are tested against trimmed, real payloads captured from
each provider's live API (see tests/fixtures/) through a FakeHttp stub -
no network, but no invented shapes either.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"

ALL_FEEDS = ["remoteok", "remotive", "himalayas", "workingnomads", "hn_whoshiring", "weworkremotely"]


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def feeds_only(*enabled: str) -> dict[str, dict[str, bool]]:
    """Feeds block that enables only the named feeds.

    Single source for the "disable the rest" list across test files, so a new
    feed added to the registry fails loudly in the tests that forget it
    instead of silently reaching an unrouted FakeHttp URL.
    """
    return {name: {"enabled": name in enabled} for name in ALL_FEEDS}


class FakeHttp:
    """Stands in for jobfunnel.http.Http: maps URL substrings to payloads."""

    def __init__(self, routes: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.routes = routes or {}
        self.error = error
        self.calls: list[str] = []

    def _resolve(self, url: str) -> Any:
        for pattern, payload in self.routes.items():
            if pattern in url:
                return payload
        raise AssertionError(f"FakeHttp has no route for {url}")

    def get_json(self, url: str, params: Any = None) -> Any:
        self.calls.append(url)
        if self.error:
            raise self.error
        payload = self._resolve(url)
        if isinstance(payload, Exception):
            raise payload
        return payload

    def get_text(self, url: str, params: Any = None) -> str:
        self.calls.append(url)
        if self.error:
            raise self.error
        payload = self._resolve(url)
        if isinstance(payload, Exception):
            raise payload
        return payload if isinstance(payload, str) else json.dumps(payload)

    # context-manager parity with the real Http
    def close(self) -> None:
        pass

    def __enter__(self) -> FakeHttp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@pytest.fixture
def fake_http_factory():
    def make(routes: dict[str, Any], error: Exception | None = None) -> FakeHttp:
        return FakeHttp(routes, error)

    return make


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "docs" / "data"
    d.mkdir(parents=True)
    return d
