# Verifies: REQ-SG-019 (has_episode matches series by name, ignoring a
#   trailing (YYYY) on either side, and only counts a real downloaded file,
#   not a virtual/placeholder episode) and the transport-error contract
#   (raises JellyfinError instead of silently returning False, feeding
#   REQ-SG-023's retry-on-next-poll behavior in the engine).
# Scenario: fake Jellyfin /Items search + /Shows/{id}/Episodes against
#   httpx.MockTransport.

import httpx
import pytest

from showgrab.adapters.jellyfin import JellyfinError, JellyfinLibrary

EPISODES = [
    {"IndexNumber": 6, "ParentIndexNumber": 8, "LocationType": "FileSystem"},
    {"IndexNumber": 7, "ParentIndexNumber": 8, "LocationType": "Virtual"},
]


def client_for(handler) -> JellyfinLibrary:
    http_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://jf.local")
    return JellyfinLibrary("http://jf.local", "testkey", client=http_client)


def test_has_episode_true_for_downloaded_file():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Items":
            return httpx.Response(200, json={"Items": [{"Id": "s1", "Name": "Doctor Who (2005)"}]})
        if request.url.path == "/Shows/s1/Episodes":
            return httpx.Response(200, json={"Items": EPISODES})
        return httpx.Response(404)

    lib = client_for(handler)
    assert lib.has_episode("x", None, "Doctor Who (2005)", 8, 6) is True


def test_has_episode_false_for_virtual_episode():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Items":
            return httpx.Response(200, json={"Items": [{"Id": "s1", "Name": "Doctor Who (2005)"}]})
        if request.url.path == "/Shows/s1/Episodes":
            return httpx.Response(200, json={"Items": EPISODES})
        return httpx.Response(404)

    lib = client_for(handler)
    assert lib.has_episode("x", None, "Doctor Who (2005)", 8, 7) is False  # not really downloaded


def test_series_name_matches_ignoring_year_suffix_mismatch():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Items":
            return httpx.Response(200, json={"Items": [{"Id": "s1", "Name": "Doctor Who (2005)"}]})
        if request.url.path == "/Shows/s1/Episodes":
            return httpx.Response(
                200, json={"Items": [{"IndexNumber": 1, "ParentIndexNumber": 1, "LocationType": "FileSystem"}]}
            )
        return httpx.Response(404)

    lib = client_for(handler)
    # feed's show_name has no year suffix; Jellyfin's does — must still match.
    assert lib.has_episode("x", None, "Doctor Who", 1, 1) is True


def test_has_episode_false_when_series_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Items":
            return httpx.Response(200, json={"Items": []})
        return httpx.Response(404)

    lib = client_for(handler)
    assert lib.has_episode("x", None, "Some New Show", 1, 1) is False


def test_transport_error_raises_jellyfin_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    lib = client_for(handler)
    with pytest.raises(JellyfinError):
        lib.has_episode("x", None, "Doctor Who", 1, 1)


def test_connection_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/System/Info":
            return httpx.Response(200, json={"Version": "10.11.11"})
        return httpx.Response(404)

    lib = client_for(handler)
    assert lib.test_connection() is True


def test_connection_failure_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    lib = client_for(handler)
    with pytest.raises(JellyfinError):
        lib.test_connection()
