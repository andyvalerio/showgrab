"""Real deployment entrypoint: wires real adapters + persistent stores from
settings and serves. `showgrab-serve` (console script) or
`python -m showgrab.service`.

Thin by design and not unit-tested directly — the logic it wires together
(run_poll, the stores, every adapter) is all tested in isolation elsewhere;
this module's only job is construction, which is exercised by actually
running the service (phase 5 deployment).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx
import uvicorn

from ..adapters.feed import parse_feed
from ..core.release import parse_release
from ..store.activity_store import ActivityLogStore
from ..store.db import init_db, make_engine, make_session_factory
from ..store.ledger_store import LedgerStore
from ..store.settings_store import SettingsStore
from .app import create_app
from .poll import run_poll
from .wiring import build_checks, build_downloader, build_jellyfin, build_notifier


def _fetch_releases(feed_url: str) -> list:
    resp = httpx.get(feed_url, timeout=30.0)
    resp.raise_for_status()
    return [parse_release(item) for item in parse_feed(resp.text)]


def build_app():
    db_path = os.environ.get("SHOWGRAB_DB_PATH", "/config/showgrab.db")
    db_engine = make_engine(db_path)
    init_db(db_engine)
    session_factory = make_session_factory(db_engine)

    settings_store = SettingsStore(session_factory)
    ledger_store = LedgerStore(session_factory)
    activity_log = ActivityLogStore(session_factory)

    startup_settings = settings_store.load()

    def poll_fn():
        # Rebuild everything from freshly-read settings every poll — a
        # settings-page edit (credentials, URLs, thresholds, dry_run, feed
        # URL, ...) takes effect on the very next poll, no restart needed.
        current = settings_store.load()
        run_poll(
            settings=current,
            ledger_store=ledger_store,
            activity_log=activity_log,
            fetch_releases=_fetch_releases,
            checks=build_checks(current),
            downloader=build_downloader(current),
            jellyfin=build_jellyfin(current),
            notifier=build_notifier(current),
            now=datetime.now(timezone.utc),
        )

    return create_app(
        poll_fn=poll_fn,
        poll_interval_minutes=startup_settings.poll_interval_minutes,
        settings_store=settings_store,
        ledger_store=ledger_store,
        activity_log=activity_log,
    )


app = build_app()


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("SHOWGRAB_PORT", "8989")))


if __name__ == "__main__":
    main()
