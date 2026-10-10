"""HTTP layer: caching, retry plumbing, error handling."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import requests

from jobfunnel.http import DEFAULT_CACHE_TTL, Http, HttpError


class FakeResponse:
    def __init__(self, payload=None, text=None, status_code=200, headers=None, json_error=None):
        self._payload = payload
        self._text = text
        self.status_code = status_code
        self.headers = headers or {}
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload

    @property
    def text(self):
        return self._text if self._text is not None else json.dumps(self._payload)


def make_session(monkeypatch, responses):
    """responses: list of FakeResponse consumed in order."""
    it = iter(responses)
    calls = []

    def fake_request(self, method, url, **kwargs):
        calls.append((method, url))
        try:
            return next(it)
        except StopIteration:
            raise AssertionError("no canned response left") from None

    monkeypatch.setattr(requests.Session, "request", fake_request)
    return calls


def test_get_json_caches_within_ttl(tmp_path, monkeypatch):
    resp = FakeResponse(payload={"jobs": [1, 2, 3]}, status_code=200, headers={"ETag": '"abc"'})
    calls = make_session(monkeypatch, [resp, FakeResponse(status_code=304)])
    http = Http(cache_dir=tmp_path, polite_delay=0, cache_ttl=DEFAULT_CACHE_TTL)

    first = http.get_json("https://example.com/api")
    second = http.get_json("https://example.com/api")  # TTL short-circuit
    assert first == second == {"jobs": [1, 2, 3]}
    assert len(calls) == 1
    assert http.stats["cache_hits"] == 1


def test_304_returns_cached_body(tmp_path, monkeypatch):
    resp = FakeResponse(payload={"a": 1}, headers={"ETag": '"v1"'})
    calls = make_session(monkeypatch, [resp, FakeResponse(status_code=304)])
    http = Http(cache_dir=tmp_path, polite_delay=0, cache_ttl=0)  # ttl=0 forces revalidation

    http.get_json("https://example.com/x")
    body = http.get_json("https://example.com/x")  # revalidate -> 304 -> cached
    assert body == {"a": 1}
    assert len(calls) == 2
    assert http.stats["cache_hits"] == 1


def test_cache_persists_across_instances(tmp_path, monkeypatch):
    make_session(monkeypatch, [FakeResponse(payload={"a": 1})])
    http = Http(cache_dir=tmp_path, polite_delay=0)
    http.get_json("https://example.com/y")
    http.close()

    # a fresh instance must read the cached body instead of hitting the network
    monkeypatch.setattr(
        requests.Session, "request", MagicMock(side_effect=AssertionError("should not hit network"))
    )
    http2 = Http(cache_dir=tmp_path, polite_delay=0)
    assert http2.get_json("https://example.com/y") == {"a": 1}
    http2.close()


def test_get_text_caches(tmp_path, monkeypatch):
    calls = make_session(monkeypatch, [FakeResponse(text="<rss></rss>")])
    http = Http(cache_dir=tmp_path, polite_delay=0)
    assert http.get_text("https://example.com/feed") == "<rss></rss>"
    assert http.get_text("https://example.com/feed") == "<rss></rss>"
    assert len(calls) == 1


def test_http_error_on_500(tmp_path, monkeypatch):
    # retries are configured on the adapter, but the mocked session bypasses it,
    # so a single 500 response must still surface as HttpError.
    make_session(monkeypatch, [FakeResponse(text="server error", status_code=500)])
    http = Http(cache_dir=tmp_path, polite_delay=0)
    with pytest.raises(HttpError, match="HTTP 500"):
        http.get_text("https://example.com/broken")


def test_request_exception_becomes_http_error(tmp_path, monkeypatch):
    def boom(self, method, url, **kwargs):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(requests.Session, "request", boom)
    http = Http(cache_dir=tmp_path, polite_delay=0, retries=0)
    with pytest.raises(HttpError, match="no route to host"):
        http.get_json("https://example.com/down")


def test_clear_cache(tmp_path):
    http = Http(cache_dir=tmp_path, polite_delay=0)
    (tmp_path / "responses.json").write_text("{}", encoding="utf-8")
    assert http.clear_cache() is True
    assert http.clear_cache() is False


def test_no_cache_dir_means_no_caching(tmp_path, monkeypatch):
    calls = make_session(monkeypatch, [FakeResponse(payload={"a": 1}), FakeResponse(payload={"a": 1})])
    http = Http(cache_dir=None, polite_delay=0)
    http.get_json("https://example.com/z")
    http.get_json("https://example.com/z")
    assert len(calls) == 2
    assert http.stats["cache_hits"] == 0
