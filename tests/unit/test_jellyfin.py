# Verifies: REQ-SG-019 (has_episode matches series by name, ignoring a
#   trailing (YYYY) on either side, and only counts a real downloaded file,
#   not a virtual/placeholder episode), REQ-SG-028 (series_path returns the
#   library folder for an existing series, None for a new one), and the
#   transport-error contract (raises JellyfinError instead of silently
#   returning False/None, feeding REQ-SG-023's retry-on-next-poll behavior).
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


def test_series_path_returns_existing_folder():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Items":
            return httpx.Response(
                200, json={"Items": [{"Id": "s1", "Name": "Silo", "Path": "/media1/Silo"}]}
            )
        return httpx.Response(404)

    lib = client_for(handler)
    assert lib.series_path("Silo") == "/media1/Silo"


def test_series_path_none_for_new_series():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Items":
            return httpx.Response(200, json={"Items": []})
        return httpx.Response(404)

    lib = client_for(handler)
    assert lib.series_path("Brand New Show") is None


def test_series_path_transport_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    lib = client_for(handler)
    with pytest.raises(JellyfinError):
        lib.series_path("Silo")


def test_series_path_rejects_release_folders_among_duplicate_series_entries():
    # Replicates a real library-quality issue found live 2026-07-12: a flat
    # per-episode-folder TV layout makes Jellyfin register several loose
    # release folders as their own bogus "Series" entries sharing the real
    # show's name, alongside the one genuine series folder. Judged by name
    # alone (Andy's correction): reject anything that looks like a release,
    # never by querying how many episodes it contains.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Items":
            return httpx.Response(
                200,
                json={
                    "Items": [
                        {
                            "Id": "junk1",
                            "Name": "American Dad!",
                            "Path": "/media1/www.Torrenting.com - American Dad! S21E10 Idiot Rich 1080p DSNP WEB-DL DDP5 1 H 264-NTb",
                        },
                        {
                            "Id": "junk2",
                            "Name": "American Dad!",
                            "Path": "/media1/www.Torrenting.com - American Dad S21E03 Ive Got a Friend in Me 1080p DSNP WEB-DL DDP5 1 H 264-NTb",
                        },
                        {
                            "Id": "real",
                            "Name": "American Dad!",
                            "Path": "/media1/American.Dad",
                        },
                    ]
                },
            )
        return httpx.Response(404)

    lib = client_for(handler)
    assert lib.series_path("American Dad!") == "/media1/American.Dad"


def test_series_path_none_when_every_candidate_is_a_release_folder():
    # Replicates the "Doctor Who" case found live 2026-07-12: every
    # same-named Series entry is a mis-parsed single-episode release
    # folder, with no genuine series folder at all. MUST return None (a
    # fresh folder gets created) rather than reuse a release folder.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Items":
            return httpx.Response(
                200,
                json={
                    "Items": [
                        {
                            "Id": "junk1",
                            "Name": "Doctor Who",
                            "Path": "/media1/www.UIndex.org - Doctor Who 2023 S00E02 Wild Blue Yonder 1080p DSNP WEB-DL DDP5 1 H 264-Kitsune",
                        },
                    ]
                },
            )
        return httpx.Response(404)

    lib = client_for(handler)
    assert lib.series_path("Doctor Who") is None


def test_has_episode_unaffected_by_which_duplicate_series_entry_is_picked():
    # has_episode() (_find_series) doesn't apply the release-folder filter —
    # it picks the FIRST matching entry, whichever duplicate that is. This is
    # safe because Jellyfin groups /Shows/{id}/Episodes by the show's
    # underlying metadata identity, not by the specific folder item id —
    # confirmed live 2026-07-12: querying three differently-Path'd
    # "American Dad!" entries all returned the identical episode list. The
    # mock below reflects that: every candidate's Episodes endpoint returns
    # the same data, matching production reality.
    episodes_response = httpx.Response(
        200, json={"Items": [{"IndexNumber": 10, "ParentIndexNumber": 22, "LocationType": "FileSystem"}]}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Items":
            return httpx.Response(
                200,
                json={
                    "Items": [
                        {"Id": "junk1", "Name": "American Dad!", "Path": "/media1/junk-release-folder-name"},
                        {"Id": "real", "Name": "American Dad!", "Path": "/media1/American.Dad"},
                    ]
                },
            )
        if request.url.path in ("/Shows/junk1/Episodes", "/Shows/real/Episodes"):
            return episodes_response
        return httpx.Response(404)

    lib = client_for(handler)
    assert lib.has_episode("x", None, "American Dad!", 22, 10) is True
