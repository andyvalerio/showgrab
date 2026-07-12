#!/usr/bin/env python3
"""Run ONE real dry-run poll cycle against real services and print the result.

This is the manual verification tool for the safety mechanism the staged
production rollout (see the home-server architecture doc, phase 5) depends
on entirely: dry-run mode must run the full engine against the real feed and
real Jellyfin/TVmaze, and must NEVER touch the downloader. To prove that
isn't just true in mocked tests, the downloader passed here is a
PoisonDownloader that raises immediately if any method is ever called.

Not part of pytest — talks to real services, like live_smoke_test.py.

Env vars:
  SHOWGRAB_FEED_URL          required — the real showRSS feed
  SHOWGRAB_JELLYFIN_URL, SHOWGRAB_JELLYFIN_API_KEY
  SHOWGRAB_PREFERRED_QUALITY  default 720p
  SHOWGRAB_OLD_CUTOFF_DAYS    default 180
  SHOWGRAB_SMTP_*             optional — set these to also send a real
                               dry-run digest email; omit to skip SMTP
                               entirely (dry-run still runs and prints)

Usage:
  cd ~/dev/showgrab && .venv/bin/python scripts/live_dry_run_test.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import httpx

from showgrab.adapters.feed import parse_feed
from showgrab.adapters.jellyfin import JellyfinLibrary
from showgrab.adapters.tvmaze import TvMazeMetadata
from showgrab.core import engine
from showgrab.core.release import parse_release
from showgrab.store.activity_store import ActivityLogStore
from showgrab.store.db import init_db, make_engine, make_session_factory
from showgrab.store.ledger_store import LedgerStore
from showgrab.store.settings_store import Settings
from showgrab.service.poll import run_poll


class PoisonDownloader:
    """Proves dry-run never touches the downloader — any call is a bug."""

    def add_magnet(self, *a, **k):
        raise AssertionError("dry-run called add_magnet() — REQ-SG-026 violated")

    def delete_with_files(self, *a, **k):
        raise AssertionError("dry-run called delete_with_files() — REQ-SG-026 violated")


class PoisonJellyfinPaths:
    """The `jellyfin` param to run_poll is only used for save-path resolution
    during real execution; dry-run must never reach it either."""

    def series_path(self, *a, **k):
        raise AssertionError("dry-run resolved a save path — REQ-SG-026 violated")


def _fetch_releases(feed_url: str) -> list:
    resp = httpx.get(feed_url, timeout=30.0)
    resp.raise_for_status()
    return [parse_release(item) for item in parse_feed(resp.text)]


def main() -> int:
    feed_url = os.environ.get("SHOWGRAB_FEED_URL")
    if not feed_url:
        print("SHOWGRAB_FEED_URL is required.")
        return 1

    jellyfin_url = os.environ.get("SHOWGRAB_JELLYFIN_URL", "")
    jellyfin_key = os.environ.get("SHOWGRAB_JELLYFIN_API_KEY", "")
    if not (jellyfin_url and jellyfin_key):
        print("SHOWGRAB_JELLYFIN_URL + SHOWGRAB_JELLYFIN_API_KEY are required "
              "(the library-check gate needs a real Jellyfin to be meaningful).")
        return 1

    settings = Settings(
        feed_url=feed_url,
        poll_interval_minutes=30,
        dry_run=True,  # the whole point of this script
        preferred_quality=os.environ.get("SHOWGRAB_PREFERRED_QUALITY", "720p"),
        wait_hours=float(os.environ.get("SHOWGRAB_WAIT_HOURS", "6")),
        swap_window_days=float(os.environ.get("SHOWGRAB_SWAP_WINDOW_DAYS", "7")),
        old_cutoff_days=float(os.environ.get("SHOWGRAB_OLD_CUTOFF_DAYS", "180")),
        qbittorrent_url="", qbittorrent_username="", qbittorrent_password="",
        qbittorrent_category=None,
        jellyfin_url=jellyfin_url, jellyfin_api_key=jellyfin_key,
        smtp_host=os.environ.get("SHOWGRAB_SMTP_HOST", ""),
        smtp_port=int(os.environ.get("SHOWGRAB_SMTP_PORT", "587")),
        smtp_username=os.environ.get("SHOWGRAB_SMTP_USERNAME", ""),
        smtp_password=os.environ.get("SHOWGRAB_SMTP_PASSWORD", ""),
        smtp_from=os.environ.get("SHOWGRAB_SMTP_FROM", ""),
        smtp_to=[a for a in os.environ.get("SHOWGRAB_SMTP_TO", "").split(",") if a],
        smtp_use_tls=os.environ.get("SHOWGRAB_SMTP_USE_SSL") != "1",
        smtp_use_ssl=os.environ.get("SHOWGRAB_SMTP_USE_SSL") == "1",
        tv_root="/downloads/tv_series",
        path_mappings=[],
    )

    jellyfin = JellyfinLibrary(jellyfin_url, jellyfin_key)
    tvmaze = TvMazeMetadata()
    checks = engine.Checks(jellyfin, tvmaze)

    notifier = None
    if settings.smtp_host:
        from showgrab.adapters.notify import SmtpConfig, SmtpNotifier
        notifier = SmtpNotifier(SmtpConfig(
            host=settings.smtp_host, port=settings.smtp_port,
            username=settings.smtp_username, password=settings.smtp_password,
            from_addr=settings.smtp_from, to_addrs=settings.smtp_to,
            use_tls=settings.smtp_use_tls, use_ssl=settings.smtp_use_ssl,
        ))

    db_engine = make_engine(":memory:")
    init_db(db_engine)
    session_factory = make_session_factory(db_engine)
    ledger_store = LedgerStore(session_factory)
    activity_log = ActivityLogStore(session_factory)

    print(f"Running one real dry-run poll against {feed_url} ...")
    outcome = run_poll(
        settings=settings,
        ledger_store=ledger_store,
        activity_log=activity_log,
        fetch_releases=_fetch_releases,
        checks=checks,
        downloader=PoisonDownloader(),
        jellyfin=PoisonJellyfinPaths(),
        notifier=notifier,
        now=datetime.now(timezone.utc),
    )

    print(f"\nexecution_ok={outcome.execution_ok}  episodes_tracked={outcome.episode_count}  "
          f"would_grab={outcome.grabbed}  would_swap={outcome.swapped}")
    print(f"events emitted: {len(outcome.events)}")
    for e in outcome.events[:20]:
        print(f"  [{e.kind:16}] {e.show_name}: {e.detail}")
    if len(outcome.events) > 20:
        print(f"  ... and {len(outcome.events) - 20} more")

    record = activity_log.recent()[0]
    print(f"\nactivity log: ok={record.ok}  dry_run={record.dry_run}  summary={record.summary!r}")

    if notifier is not None and outcome.events:
        print(f"\nA real dry-run digest email was sent to {settings.smtp_to}.")

    return 0 if outcome.execution_ok else 1


if __name__ == "__main__":
    sys.exit(main())
