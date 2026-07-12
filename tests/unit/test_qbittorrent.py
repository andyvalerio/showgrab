# Verifies: REQ-SG-016 (authenticate then add a magnet with an explicit save
#   path and optional category, autoTMM disabled), REQ-SG-017 (delete a
#   torrent with its files, keyed by infohash), REQ-SG-018 (a session expiry
#   triggers exactly one re-auth-and-retry, never a loop).
# Scenario: drive QbittorrentDownloader against an httpx.MockTransport fake
#   that mimics qBittorrent's cookie-based login and torrents/add|delete.

from urllib.parse import parse_qsl

import httpx
import pytest

from showgrab.adapters.qbittorrent import QbittorrentDownloader, QbittorrentError


class FakeQbittorrent:
    def __init__(self):
        self.logged_in = False
        self.added: list[dict] = []
        self.deleted: list[dict] = []
        self.expire_next_authed_call = False
        self.login_attempts = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v2/auth/login":
            self.login_attempts += 1
            self.logged_in = True
            return httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=abc; Path=/"})
        if not self.logged_in:
            return httpx.Response(403, text="Forbidden")
        if self.expire_next_authed_call:
            self.expire_next_authed_call = False
            self.logged_in = False
            return httpx.Response(403, text="Forbidden")
        body = dict(parse_qsl(request.content.decode()))
        if path == "/api/v2/torrents/add":
            self.added.append(body)
            return httpx.Response(200, text="Ok.")
        if path == "/api/v2/torrents/delete":
            self.deleted.append(body)
            return httpx.Response(200, text="")
        if path == "/api/v2/app/version":
            return httpx.Response(200, text="v4.6.0")
        return httpx.Response(404)


def make_client(fake: FakeQbittorrent) -> QbittorrentDownloader:
    http_client = httpx.Client(transport=httpx.MockTransport(fake.handler), base_url="http://qbt.local")
    return QbittorrentDownloader("http://qbt.local", "admin", "secret", client=http_client)


def test_add_magnet_logs_in_and_posts_savepath_and_category():
    fake = FakeQbittorrent()
    dl = make_client(fake)
    dl.add_magnet("magnet:?xt=urn:btih:ABC", "/downloads/tv_series/Silo", category="tv")
    assert fake.logged_in
    assert len(fake.added) == 1
    assert fake.added[0]["urls"] == "magnet:?xt=urn:btih:ABC"
    assert fake.added[0]["savepath"] == "/downloads/tv_series/Silo"
    assert fake.added[0]["category"] == "tv"
    assert fake.added[0]["autoTMM"] == "false"


def test_add_magnet_without_category_omits_it():
    fake = FakeQbittorrent()
    dl = make_client(fake)
    dl.add_magnet("magnet:?xt=urn:btih:ABC", "/downloads/tv_series/Silo")
    assert "category" not in fake.added[0]


def test_delete_with_files_sends_lowercased_hash_and_delete_flag():
    fake = FakeQbittorrent()
    dl = make_client(fake)
    dl.delete_with_files("ABC123")
    assert len(fake.deleted) == 1
    assert fake.deleted[0]["hashes"] == "abc123"
    assert fake.deleted[0]["deleteFiles"] == "true"


def test_expired_session_triggers_single_reauth_and_retry():
    fake = FakeQbittorrent()
    dl = make_client(fake)
    dl.add_magnet("magnet:?xt=urn:btih:X", "/downloads/tv_series/Show")  # establishes session
    assert fake.login_attempts == 1

    fake.expire_next_authed_call = True
    dl.add_magnet("magnet:?xt=urn:btih:Y", "/downloads/tv_series/Show")  # 403 -> reauth -> retry
    assert fake.login_attempts == 2
    assert len(fake.added) == 2


def test_login_failure_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return httpx.Response(200, text="Fails.")
        return httpx.Response(403)

    http_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://qbt.local")
    dl = QbittorrentDownloader("http://qbt.local", "admin", "wrong", client=http_client)
    with pytest.raises(QbittorrentError):
        dl.add_magnet("magnet:?xt=urn:btih:X", "/downloads/tv_series/Show")


def test_connection_reports_true_when_authenticated():
    fake = FakeQbittorrent()
    dl = make_client(fake)
    assert dl.test_connection() is True
