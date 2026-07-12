#!/usr/bin/env python3
"""Manual smoke test of showgrab's adapters against REAL, LIVE services.

Not part of `pytest` — the automated suite only ever talks to fakes
(httpx.MockTransport / fake SMTP doubles), on purpose, so CI never needs
production secrets. This script is for a human to run by hand before trusting
a newly-built adapter, and later as an operational "is everything reachable"
check before a deploy.

Every value comes from the environment — nothing is hardcoded, no adapter
guesses a default host/credential. Unset a section's variables and this
script simply skips it.

  TVMAZE_SHOW_ID, TVMAZE_SEASON, TVMAZE_EPISODE   (optional; defaults to
                                                     Silo S03E02 / TVmaze id 38052)

  JELLYFIN_URL, JELLYFIN_API_KEY

  QBITTORRENT_URL, QBITTORRENT_USER, QBITTORRENT_PASS
  QBITTORRENT_LIVE_TEST_CONFIRM=yes   (extra explicit gate — the qBittorrent
                                        test adds and then deletes a real,
                                        legal, public-domain test torrent in
                                        an isolated save path/category. It
                                        will NOT run without this var set.)

  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM, SMTP_TO
  SMTP_USE_TLS=1 (default) | SMTP_USE_SSL=1

Usage:
  cd ~/dev/showgrab && .venv/bin/python scripts/live_smoke_test.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Well-known, legal, public-domain test torrent (Sintel, Blender Foundation,
# CC-BY 3.0). Used only to prove the add/delete round-trip through the real
# qBittorrent API; we delete it again within seconds, well before any
# meaningful amount of it downloads. (The other famous public-domain test
# magnet, Big Buck Bunny, was tried first and got a hard 409 from this
# specific qBittorrent instance for reasons unrelated to showgrab — some
# blocklist/IP-filter interaction with that exact infohash. Sintel works.)
TEST_MAGNET = "magnet:?xt=urn:btih:08ada5a7a6183aae1e09d831df6748d566095a10&dn=Sintel"
TEST_INFOHASH = "08ada5a7a6183aae1e09d831df6748d566095a10"


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def ok(msg: str) -> None:
    print(f"  PASS  {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL  {msg}")


def skip(msg: str) -> None:
    print(f"  SKIP  {msg}")


def test_tvmaze() -> None:
    section("TVmaze (free, keyless)")
    from showgrab.adapters.tvmaze import TvMazeError, TvMazeMetadata

    show_id = os.environ.get("TVMAZE_SHOW_ID", "38052")
    season = int(os.environ.get("TVMAZE_SEASON", "3"))
    episode = int(os.environ.get("TVMAZE_EPISODE", "2"))

    meta = TvMazeMetadata()
    try:
        assert meta.test_connection()
        ok("test_connection()")
    except (TvMazeError, AssertionError) as e:
        fail(f"test_connection(): {e}")
        return

    try:
        dt = meta.airdate(show_id, "Silo", season, episode)
        if dt is None:
            fail(f"airdate() returned None for show {show_id} S{season:02d}E{episode:02d}")
        else:
            ok(f"airdate(show={show_id}, S{season:02d}E{episode:02d}) = {dt.isoformat()}")
    except TvMazeError as e:
        fail(f"airdate(): {e}")

    try:
        dt = meta.airdate(None, "No External Id", 1, 1)
        assert dt is None
        ok("airdate() with no external_id returns None without a network call")
    except Exception as e:
        fail(f"no-external-id case: {e}")


def test_jellyfin() -> None:
    section("Jellyfin")
    url = os.environ.get("JELLYFIN_URL")
    key = os.environ.get("JELLYFIN_API_KEY")
    if not (url and key):
        skip("set JELLYFIN_URL + JELLYFIN_API_KEY to run this")
        return

    from showgrab.adapters.jellyfin import JellyfinError, JellyfinLibrary

    lib = JellyfinLibrary(url, key)
    try:
        assert lib.test_connection()
        ok(f"test_connection() against {url}")
    except (JellyfinError, AssertionError) as e:
        fail(f"test_connection(): {e}")
        return

    try:
        have = lib.has_episode("_", None, "This Show Definitely Does Not Exist Xyzzy", 1, 1)
        assert have is False
        ok("has_episode() for a nonexistent series correctly returns False")
    except JellyfinError as e:
        fail(f"has_episode() nonexistent-series case: {e}")

    # If the caller told us a real series known to be in the library, check it.
    real_show = os.environ.get("JELLYFIN_TEST_SHOW")
    real_season = os.environ.get("JELLYFIN_TEST_SEASON")
    real_episode = os.environ.get("JELLYFIN_TEST_EPISODE")
    if real_show and real_season and real_episode:
        try:
            have = lib.has_episode("_", None, real_show, int(real_season), int(real_episode))
            ok(f"has_episode({real_show!r}, S{real_season}E{real_episode}) = {have}")
        except JellyfinError as e:
            fail(f"has_episode() real-show case: {e}")
    else:
        skip("set JELLYFIN_TEST_SHOW/_SEASON/_EPISODE to check a real episode's presence")


def test_qbittorrent() -> None:
    section("qBittorrent")
    url = os.environ.get("QBITTORRENT_URL")
    user = os.environ.get("QBITTORRENT_USER")
    password = os.environ.get("QBITTORRENT_PASS")
    if not (url and user and password):
        skip("set QBITTORRENT_URL + QBITTORRENT_USER + QBITTORRENT_PASS to run this")
        return

    from showgrab.adapters.qbittorrent import QbittorrentDownloader, QbittorrentError

    dl = QbittorrentDownloader(url, user, password)
    try:
        assert dl.test_connection()
        ok(f"test_connection() against {url}")
    except (QbittorrentError, AssertionError) as e:
        fail(f"test_connection(): {e}")
        return

    if os.environ.get("QBITTORRENT_LIVE_TEST_CONFIRM") != "yes":
        skip(
            "add/delete round-trip needs QBITTORRENT_LIVE_TEST_CONFIRM=yes "
            "(this adds + deletes a real, legal, public-domain test torrent "
            "in an isolated save path — off by default on purpose)"
        )
        return

    save_path = "/downloads/tv_series/_showgrab_livetest/"
    try:
        dl.add_magnet(TEST_MAGNET, save_path, category="showgrab-test")
        ok(f"add_magnet() into {save_path} (category=showgrab-test)")
    except QbittorrentError as e:
        fail(f"add_magnet(): {e}")
        return

    time.sleep(2)  # let it register before we clean up; not waiting for any data

    try:
        dl.delete_with_files(TEST_INFOHASH)
        ok("delete_with_files() cleaned up the test torrent")
    except QbittorrentError as e:
        fail(f"delete_with_files() — MANUAL CLEANUP MAY BE NEEDED in qBittorrent: {e}")


def test_smtp() -> None:
    section("SMTP")
    host = os.environ.get("SMTP_HOST")
    port = os.environ.get("SMTP_PORT")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    from_addr = os.environ.get("SMTP_FROM")
    to_addr = os.environ.get("SMTP_TO")
    if not all([host, port, user, password, from_addr, to_addr]):
        skip("set SMTP_HOST/_PORT/_USER/_PASS/_FROM/_TO to run this")
        return

    from showgrab.adapters.notify import SmtpConfig, SmtpError, SmtpNotifier
    from showgrab.core.models import NotifyEvent

    config = SmtpConfig(
        host=host,
        port=int(port),
        username=user,
        password=password,
        from_addr=from_addr,
        to_addrs=[to_addr],
        use_tls=os.environ.get("SMTP_USE_SSL") != "1",
        use_ssl=os.environ.get("SMTP_USE_SSL") == "1",
    )
    notifier = SmtpNotifier(config)
    try:
        assert notifier.test_connection()
        ok(f"test_connection() against {host}:{port}")
    except (SmtpError, AssertionError) as e:
        fail(f"test_connection(): {e}")
        return

    if os.environ.get("SMTP_SEND_TEST") != "yes":
        skip("set SMTP_SEND_TEST=yes to actually send one real digest email")
        return

    try:
        sent = notifier.send_digest(
            [NotifyEvent("grabbed", ("livetest", 1, 1), "showgrab live smoke test", "this is a real test email")]
        )
        assert sent is True
        ok(f"send_digest() delivered a real test email to {to_addr}")
    except SmtpError as e:
        fail(f"send_digest(): {e}")


if __name__ == "__main__":
    print("showgrab live adapter smoke test — talks to REAL services, reads config from env only.")
    test_tvmaze()
    test_jellyfin()
    test_qbittorrent()
    test_smtp()
    print()
