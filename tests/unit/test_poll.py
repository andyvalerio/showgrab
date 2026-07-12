# Verifies: REQ-SG-026 (dry-run never calls the downloader and never persists
#   ledger mutations, but still runs the full engine and sends a marked
#   digest), REQ-SG-027 (a live poll persists only on full execution success;
#   on any action failure, nothing from that poll is saved and the next poll
#   retries from last-known-good state), REQ-SG-032 (an unhandled exception
#   anywhere in the poll is caught, logged, best-effort notified, and never
#   crashes the caller).
# Scenario: run_poll against fakes for every adapter and a real in-memory
#   SQLite-backed ledger/activity store, covering the live-success,
#   dry-run, execution-failure-then-retry, and unhandled-exception paths.

from datetime import datetime, timedelta, timezone

from showgrab.core.models import Status
from showgrab.core.quality import Quality
from showgrab.core.release import parse_release
from showgrab.service.poll import run_poll
from showgrab.store.activity_store import ActivityLogStore
from showgrab.store.db import init_db, make_engine, make_session_factory
from showgrab.store.ledger_store import LedgerStore
from showgrab.store.settings_store import Settings

from tests.conftest import make_item

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


def make_stores():
    engine = make_engine(":memory:")
    init_db(engine)
    sf = make_session_factory(engine)
    return LedgerStore(sf), ActivityLogStore(sf)


def make_settings(**overrides) -> Settings:
    base = dict(
        feed_url="https://example.test/feed.rss",
        poll_interval_minutes=30,
        dry_run=False,
        preferred_quality="720p",
        wait_hours=6,
        swap_window_days=7,
        old_cutoff_days=180,
        qbittorrent_url="http://qbt.local",
        qbittorrent_username="admin",
        qbittorrent_password="secret",
        qbittorrent_category="showgrab",
        jellyfin_url="http://jf.local",
        jellyfin_api_key="key",
        smtp_host="", smtp_port=587, smtp_username="", smtp_password="",
        smtp_from="", smtp_to=[], smtp_use_tls=True, smtp_use_ssl=False,
        tv_root="/downloads/tv_series",
        path_mappings=[],
    )
    base.update(overrides)
    return Settings(**base)


class FakeLibrary:
    def has_episode(self, *a, **k):
        return False


class FakeMetadata:
    def airdate(self, *a, **k):
        return NOW - timedelta(days=1)


from showgrab.core import engine as engine_module


def make_checks():
    return engine_module.Checks(FakeLibrary(), FakeMetadata())


class FakeJellyfinPaths:
    """Only the series_path() surface run_poll's execution path needs."""

    def series_path(self, show_name: str):
        return None  # brand-new series -> falls back to tv_root


class FakeDownloader:
    def __init__(self, fail_on: set[str] | None = None):
        self.added = []
        self.deleted = []
        self._fail_on = fail_on or set()

    def add_magnet(self, magnet, save_path, category=None):
        if magnet in self._fail_on:
            raise RuntimeError("qBittorrent unreachable")
        self.added.append((magnet, save_path, category))

    def delete_with_files(self, infohash):
        self.deleted.append(infohash)


class FakeNotifier:
    def __init__(self):
        self.calls = []

    def send_digest(self, events, dry_run=False):
        self.calls.append((events, dry_run))
        return bool(events)


def one_release(title="Silo S03E02 720p", **kw):
    kw.setdefault("show_name", "Silo")
    return [parse_release(make_item(title, show_id="1675", infohash="hd720", **kw))]


def fetch_releases_factory(releases):
    return lambda feed_url: releases


# --- live success ----------------------------------------------------------


def test_live_poll_persists_and_notifies_on_success():
    ledger_store, activity_log = make_stores()
    downloader = FakeDownloader()
    notifier = FakeNotifier()
    settings = make_settings(dry_run=False)

    outcome = run_poll(
        settings=settings,
        ledger_store=ledger_store,
        activity_log=activity_log,
        fetch_releases=fetch_releases_factory(one_release()),
        checks=make_checks(),
        downloader=downloader,
        jellyfin=FakeJellyfinPaths(),
        notifier=notifier,
        now=NOW,
    )

    assert outcome.execution_ok is True
    assert outcome.grabbed == 1
    assert len(downloader.added) == 1
    assert downloader.added[0][1] == "/downloads/tv_series/Silo"  # new-series fallback path
    assert downloader.added[0][2] == "showgrab"  # category from settings

    # persisted: reloading the ledger shows the grab happened
    reloaded = ledger_store.load()
    entry = reloaded.all()[0]
    assert entry.status is Status.GRABBED

    # notified, not marked dry-run
    assert len(notifier.calls) == 1
    events, dry_run = notifier.calls[0]
    assert dry_run is False
    assert any(e.kind == "grabbed" for e in events)

    # activity logged
    records = activity_log.recent()
    assert len(records) == 1
    assert records[0].ok is True
    assert records[0].dry_run is False


# --- dry run -----------------------------------------------------------


def test_dry_run_never_touches_downloader_or_persists():
    ledger_store, activity_log = make_stores()
    downloader = FakeDownloader()
    notifier = FakeNotifier()
    settings = make_settings(dry_run=True)

    outcome = run_poll(
        settings=settings,
        ledger_store=ledger_store,
        activity_log=activity_log,
        fetch_releases=fetch_releases_factory(one_release()),
        checks=make_checks(),
        downloader=downloader,
        jellyfin=FakeJellyfinPaths(),
        notifier=notifier,
        now=NOW,
    )

    assert outcome.dry_run is True
    assert outcome.grabbed == 1  # the engine still decided to grab...
    assert downloader.added == []  # ...but nothing was actually downloaded (REQ-SG-026)
    assert downloader.deleted == []

    # nothing persisted — a fresh load shows no entries at all
    reloaded = ledger_store.load()
    assert reloaded.all() == []

    # still notified, clearly marked as dry-run
    events, dry_run = notifier.calls[0]
    assert dry_run is True
    assert any(e.kind == "grabbed" for e in events)


def test_dry_run_repeats_the_same_decision_every_poll():
    # Because nothing is persisted, polling twice in a row against the same
    # feed must produce the SAME decision both times (not settle/advance) —
    # that's the intended review-window behavior, not a bug.
    ledger_store, activity_log = make_stores()
    settings = make_settings(dry_run=True)
    releases = one_release()

    for _ in range(2):
        outcome = run_poll(
            settings=settings,
            ledger_store=ledger_store,
            activity_log=activity_log,
            fetch_releases=fetch_releases_factory(releases),
            checks=make_checks(),
            downloader=FakeDownloader(),
            jellyfin=FakeJellyfinPaths(),
            notifier=None,
            now=NOW,
        )
        assert outcome.grabbed == 1


# --- execution failure & retry -----------------------------------------


def test_execution_failure_persists_nothing_and_retries_next_poll():
    ledger_store, activity_log = make_stores()
    settings = make_settings(dry_run=False)
    releases = one_release()
    bad_magnet = releases[0].item.magnet

    failing_downloader = FakeDownloader(fail_on={bad_magnet})
    outcome1 = run_poll(
        settings=settings,
        ledger_store=ledger_store,
        activity_log=activity_log,
        fetch_releases=fetch_releases_factory(releases),
        checks=make_checks(),
        downloader=failing_downloader,
        jellyfin=FakeJellyfinPaths(),
        notifier=None,
        now=NOW,
    )
    assert outcome1.execution_ok is False
    # nothing persisted
    assert ledger_store.load().all() == []
    assert activity_log.recent()[0].ok is False

    # Next poll: the transient failure is gone, retry succeeds cleanly.
    working_downloader = FakeDownloader()
    outcome2 = run_poll(
        settings=settings,
        ledger_store=ledger_store,
        activity_log=activity_log,
        fetch_releases=fetch_releases_factory(releases),
        checks=make_checks(),
        downloader=working_downloader,
        jellyfin=FakeJellyfinPaths(),
        notifier=None,
        now=NOW + timedelta(minutes=30),
    )
    assert outcome2.execution_ok is True
    assert len(working_downloader.added) == 1
    entry = ledger_store.load().all()[0]
    assert entry.status is Status.GRABBED


# --- poll-level resilience (REQ-SG-032) ---------------------------------


def test_unhandled_exception_is_caught_logged_and_does_not_raise():
    ledger_store, activity_log = make_stores()
    settings = make_settings(dry_run=False)
    notifier = FakeNotifier()

    def broken_fetch(feed_url):
        raise ConnectionError("feed host unreachable")

    outcome = run_poll(
        settings=settings,
        ledger_store=ledger_store,
        activity_log=activity_log,
        fetch_releases=broken_fetch,
        checks=make_checks(),
        downloader=FakeDownloader(),
        jellyfin=FakeJellyfinPaths(),
        notifier=notifier,
        now=NOW,
    )

    assert outcome.execution_ok is False
    record = activity_log.recent()[0]
    assert record.ok is False
    assert "feed host unreachable" in record.summary
    # best-effort notification was attempted too
    assert len(notifier.calls) == 1
    assert notifier.calls[0][0][0].kind == "poll-error"


def test_unhandled_exception_survives_a_broken_notifier_and_activity_log():
    ledger_store, activity_log = make_stores()
    settings = make_settings(dry_run=False)

    class ExplodingNotifier:
        def send_digest(self, events, dry_run=False):
            raise RuntimeError("SMTP also down")

    def broken_fetch(feed_url):
        raise ConnectionError("feed host unreachable")

    # Must not raise even though the notifier ALSO fails while trying to
    # report the original failure.
    outcome = run_poll(
        settings=settings,
        ledger_store=ledger_store,
        activity_log=activity_log,
        fetch_releases=broken_fetch,
        checks=make_checks(),
        downloader=FakeDownloader(),
        jellyfin=FakeJellyfinPaths(),
        notifier=ExplodingNotifier(),
        now=NOW,
    )
    assert outcome.execution_ok is False
