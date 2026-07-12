# Verifies: REQ-SG-016 (authenticate then add a magnet with an explicit save
#   path and optional category, autoTMM disabled), REQ-SG-017 (delete a
#   torrent with its files, keyed by infohash), REQ-SG-018 (a session expiry
#   triggers exactly one re-auth-and-retry, never a loop).
# Scenario: drive QbittorrentDownloader against an httpx.MockTransport fake
#   that mimics qBittorrent's cookie-based login and torrents/add|delete.
#
# The default fake mimics the REAL response contract of a live qBittorrent
# 5.2.3 (confirmed 2026-07-12 against production: 204/empty body on login
# success, 401 on bad credentials, torrents/add returns 200 + a JSON summary
# {"added_torrent_ids": [...], "failure_count": N, ...}) — the older "200 +
# plain-text Ok./Fails." contract documented in most tutorials is wrong for
# this version. A second fake (FakeQbittorrentLegacy) covers that older text
# contract so both API generations stay supported.

from urllib.parse import parse_qsl

import httpx
import pytest

from showgrab.adapters.qbittorrent import QbittorrentDownloader, QbittorrentError


class FakeQbittorrent:
    """Mimics real qBittorrent 5.x response bodies/status codes."""

    def __init__(self):
        self.logged_in = False
        self.added: list[dict] = []
        self.deleted: list[dict] = []
        self.expire_next_authed_call = False
        self.login_attempts = 0
        self.next_add_failure_count = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v2/auth/login":
            self.login_attempts += 1
            self.logged_in = True
            return httpx.Response(204, headers={"set-cookie": "QBT_SID=abc; Path=/"})
        if not self.logged_in:
            return httpx.Response(401, text="Unauthorized")
        if self.expire_next_authed_call:
            self.expire_next_authed_call = False
            self.logged_in = False
            return httpx.Response(401, text="Unauthorized")
        body = dict(parse_qsl(request.content.decode()))
        if path == "/api/v2/torrents/add":
            self.added.append(body)
            failures = self.next_add_failure_count
            self.next_add_failure_count = 0
            return httpx.Response(
                200,
                json={
                    "added_torrent_ids": [] if failures else ["deadbeef"],
                    "failure_count": failures,
                    "pending_count": 0,
                    "success_count": 0 if failures else 1,
                },
            )
        if path == "/api/v2/torrents/delete":
            self.deleted.append(body)
            return httpx.Response(200, text="")
        if path == "/api/v2/app/version":
            return httpx.Response(200, text="v5.2.3")
        return httpx.Response(404)


class FakeQbittorrentLegacy:
    """Mimics the older (<= v4.x) plain-text response contract."""

    def __init__(self, fail_login: bool = False):
        self.logged_in = False
        self.fail_login = fail_login
        self.added: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v2/auth/login":
            if self.fail_login:
                return httpx.Response(200, text="Fails.")
            self.logged_in = True
            return httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=abc; Path=/"})
        if not self.logged_in:
            return httpx.Response(403, text="Forbidden")
        if path == "/api/v2/torrents/add":
            self.added.append(dict(parse_qsl(request.content.decode())))
            return httpx.Response(200, text="Ok.")
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
    dl.add_magnet("magnet:?xt=urn:btih:Y", "/downloads/tv_series/Show")  # 401 -> reauth -> retry
    assert fake.login_attempts == 2
    assert len(fake.added) == 2


def test_login_failure_raises_on_modern_401():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return httpx.Response(401, text="Unauthorized")
        return httpx.Response(401)

    http_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://qbt.local")
    dl = QbittorrentDownloader("http://qbt.local", "admin", "wrong", client=http_client)
    with pytest.raises(QbittorrentError):
        dl.add_magnet("magnet:?xt=urn:btih:X", "/downloads/tv_series/Show")


def test_add_magnet_soft_failure_in_json_body_raises():
    # A 200 response whose JSON summary reports failure_count > 0 (observed
    # live) must still be treated as a failure, not silently swallowed.
    fake = FakeQbittorrent()
    fake.next_add_failure_count = 1
    dl = make_client(fake)
    with pytest.raises(QbittorrentError):
        dl.add_magnet("magnet:?xt=urn:btih:BAD", "/downloads/tv_series/Show")


def test_connection_reports_true_on_modern_204_login():
    fake = FakeQbittorrent()
    dl = make_client(fake)
    assert dl.test_connection() is True


# --- legacy (<= v4.x) plain-text contract compatibility -------------------


def test_legacy_login_success_with_plain_text_ok():
    fake = FakeQbittorrentLegacy()
    http_client = httpx.Client(transport=httpx.MockTransport(fake.handler), base_url="http://qbt.local")
    dl = QbittorrentDownloader("http://qbt.local", "admin", "secret", client=http_client)
    dl.add_magnet("magnet:?xt=urn:btih:X", "/downloads/tv_series/Show")
    assert len(fake.added) == 1


def test_legacy_login_failure_with_plain_text_fails():
    fake = FakeQbittorrentLegacy(fail_login=True)
    http_client = httpx.Client(transport=httpx.MockTransport(fake.handler), base_url="http://qbt.local")
    dl = QbittorrentDownloader("http://qbt.local", "admin", "wrong", client=http_client)
    with pytest.raises(QbittorrentError):
        dl.add_magnet("magnet:?xt=urn:btih:X", "/downloads/tv_series/Show")
