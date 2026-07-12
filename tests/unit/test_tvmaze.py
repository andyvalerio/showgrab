# Verifies: REQ-SG-020 (airdate resolves via TVmaze using the feed's
#   external_id; returns None without a network call when external_id is
#   absent; returns None on a 404; raises TvMazeError on any other
#   transport/HTTP failure rather than silently returning None).
# Scenario: fake /shows/{id}/episodebynumber against httpx.MockTransport.

import httpx
import pytest

from showgrab.adapters.tvmaze import TvMazeError, TvMazeMetadata


def client_for(handler) -> TvMazeMetadata:
    http_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.tvmaze.com")
    return TvMazeMetadata(client=http_client)


def test_airdate_parses_airstamp():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/shows/38052/episodebynumber"
        assert dict(request.url.params) == {"season": "3", "number": "2"}
        return httpx.Response(200, json={"airdate": "2026-07-10", "airstamp": "2026-07-10T21:00:00+00:00"})

    meta = client_for(handler)
    dt = meta.airdate("38052", "Silo", 3, 2)
    assert dt is not None
    assert dt.date().isoformat() == "2026-07-10"


def test_airdate_falls_back_to_airdate_field_without_airstamp():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"airdate": "2026-07-10"})

    meta = client_for(handler)
    dt = meta.airdate("38052", "Silo", 3, 2)
    assert dt.date().isoformat() == "2026-07-10"


def test_no_external_id_returns_none_without_a_request():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    meta = client_for(handler)
    assert meta.airdate(None, "Weird Show", 1, 1) is None
    assert calls == []


def test_404_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    meta = client_for(handler)
    assert meta.airdate("999999", "Unknown", 1, 1) is None


def test_server_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    meta = client_for(handler)
    with pytest.raises(TvMazeError):
        meta.airdate("38052", "Silo", 3, 2)


def test_connection_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": 1})

    meta = client_for(handler)
    assert meta.test_connection() is True
