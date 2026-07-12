# Verifies: REQ-SG-033..036 (episode actions reachable via the web UI —
#   ignore, retry, grab-now, manual swap — mutate the ledger through the same
#   service/actions.py functions already unit-tested), REQ-SG-037 (settings
#   page reads/writes through the real SettingsStore), REQ-SG-038
#   (test-connection uses the currently-entered form values), REQ-SG-039
#   (dashboard reads the live ledger; "run poll now" calls the real poll_fn),
#   REQ-SG-040 (activity log renders most-recent-first).
# Scenario: build a real app via create_app() with real (in-memory SQLite)
#   stores, drive it through FastAPI's TestClient. Adapter construction
#   (build_downloader/build_jellyfin) is monkeypatched to fakes so episode
#   actions never touch a real network — matching how test_actions.py
#   already proves the underlying logic; this file proves the HTTP wiring.

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from showgrab.core.models import LedgerEntry, Status, Variant
from showgrab.core.quality import Quality
from showgrab.service.app import create_app
from showgrab.service.web import routes as routes_module
from showgrab.store.activity_store import ActivityLogStore
from showgrab.store.db import init_db, make_engine, make_session_factory
from showgrab.store.ledger_store import LedgerStore
from showgrab.store.settings_store import SettingsStore

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


class FakeDownloader:
    def __init__(self):
        self.added = []
        self.deleted = []

    def add_magnet(self, magnet, save_path, category=None):
        self.added.append((magnet, save_path, category))

    def delete_with_files(self, infohash):
        self.deleted.append(infohash)


class FakeJellyfinPaths:
    def series_path(self, show_name):
        return None


def make_app(monkeypatch, poll_calls=None):
    engine = make_engine(":memory:")
    init_db(engine)
    sf = make_session_factory(engine)
    settings_store = SettingsStore(sf)
    ledger_store = LedgerStore(sf)
    activity_log = ActivityLogStore(sf)

    poll_calls = poll_calls if poll_calls is not None else []

    def poll_fn():
        poll_calls.append(1)

    monkeypatch.setattr(routes_module, "build_downloader", lambda settings: FakeDownloader())
    monkeypatch.setattr(routes_module, "build_jellyfin", lambda settings: FakeJellyfinPaths())

    app = create_app(
        poll_fn=poll_fn,
        poll_interval_minutes=30,
        run_scheduler=False,  # avoid a real background poll firing mid-test
        settings_store=settings_store,
        ledger_store=ledger_store,
        activity_log=activity_log,
    )
    return app, settings_store, ledger_store, activity_log


def variant(infohash: str, quality: Quality = Quality.HD720) -> Variant:
    return Variant(
        quality=quality, is_remux=False, is_repack=False,
        infohash=infohash, magnet=f"magnet:?xt=urn:btih:{infohash}", pub_date=NOW, episode_id=infohash,
    )


def make_entry(status=Status.WAITING, variants=None, chosen_infohash=None) -> LedgerEntry:
    return LedgerEntry(
        key=("1675", 3, 2), show_id="1675", show_name="Silo", external_id="38052",
        first_seen=NOW, variants=variants or {}, status=status, chosen_infohash=chosen_infohash,
    )


# --- dashboard ---------------------------------------------------------


def test_dashboard_renders_empty_state(monkeypatch):
    app, *_ = make_app(monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Nothing tracked yet" in resp.text


def test_dashboard_renders_tracked_episode(monkeypatch):
    app, settings_store, ledger_store, activity_log = make_app(monkeypatch)
    ledger = ledger_store.load()
    ledger.put(make_entry(variants={"hd720": variant("hd720")}))
    ledger_store.save(ledger)

    with TestClient(app) as client:
        resp = client.get("/")
        assert "Silo" in resp.text
        assert "S03E02" in resp.text
        assert "waiting" in resp.text


def test_run_poll_now_calls_poll_fn_and_redirects(monkeypatch):
    calls = []
    app, *_ = make_app(monkeypatch, poll_calls=calls)
    with TestClient(app) as client:
        resp = client.post("/poll/run", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"
        assert calls == [1]


# --- episode actions -----------------------------------------------------


def test_ignore_action_updates_ledger(monkeypatch):
    app, settings_store, ledger_store, activity_log = make_app(monkeypatch)
    entry = make_entry(status=Status.NEEDS_ATTENTION)
    ledger = ledger_store.load()
    ledger.put(entry)
    ledger_store.save(ledger)

    with TestClient(app) as client:
        resp = client.post("/episode/ignore", data={"key": '["1675", 3, 2]'}, follow_redirects=False)
        assert resp.status_code == 303

    reloaded = ledger_store.load().get(("1675", 3, 2))
    assert reloaded.status is Status.IGNORED


def test_retry_action_resets_needs_attention(monkeypatch):
    app, settings_store, ledger_store, activity_log = make_app(monkeypatch)
    ledger = ledger_store.load()
    ledger.put(make_entry(status=Status.NEEDS_ATTENTION))
    ledger_store.save(ledger)

    with TestClient(app) as client:
        client.post("/episode/retry", data={"key": '["1675", 3, 2]'}, follow_redirects=False)

    assert ledger_store.load().get(("1675", 3, 2)).status is Status.DISCOVERED


def test_grab_now_action_executes_and_persists(monkeypatch):
    app, settings_store, ledger_store, activity_log = make_app(monkeypatch)
    ledger = ledger_store.load()
    ledger.put(make_entry(status=Status.WAITING, variants={"hd720": variant("hd720")}))
    ledger_store.save(ledger)

    with TestClient(app) as client:
        client.post("/episode/grab-now", data={"key": '["1675", 3, 2]'}, follow_redirects=False)

    entry = ledger_store.load().get(("1675", 3, 2))
    assert entry.status is Status.GRABBED
    assert entry.chosen_infohash == "hd720"


def test_swap_action_executes_and_persists(monkeypatch):
    app, settings_store, ledger_store, activity_log = make_app(monkeypatch)
    ledger = ledger_store.load()
    ledger.put(
        make_entry(
            status=Status.GRABBED,
            variants={"hd1080": variant("hd1080", Quality.HD1080), "hd720": variant("hd720", Quality.HD720)},
            chosen_infohash="hd1080",
        )
    )
    ledger_store.save(ledger)

    with TestClient(app) as client:
        client.post(
            "/episode/swap", data={"key": '["1675", 3, 2]', "infohash": "hd720"}, follow_redirects=False
        )

    entry = ledger_store.load().get(("1675", 3, 2))
    assert entry.status is Status.SWAPPED
    assert entry.chosen_infohash == "hd720"


def test_action_on_unknown_key_is_a_harmless_noop(monkeypatch):
    app, *_ = make_app(monkeypatch)
    with TestClient(app) as client:
        resp = client.post("/episode/ignore", data={"key": '["nope", 1, 1]'}, follow_redirects=False)
        assert resp.status_code == 303  # still redirects, nothing crashes


# --- settings ------------------------------------------------------------


def test_settings_page_renders_current_values(monkeypatch):
    app, settings_store, *_ = make_app(monkeypatch)
    settings = settings_store.load()
    settings.feed_url = "https://showrss.info/user/1.rss"
    settings_store.save(settings)

    with TestClient(app) as client:
        resp = client.get("/settings")
        assert "https://showrss.info/user/1.rss" in resp.text


def test_saving_settings_persists_and_survives_reload(monkeypatch):
    app, settings_store, *_ = make_app(monkeypatch)
    with TestClient(app) as client:
        resp = client.post(
            "/settings",
            data={
                "feed_url": "https://showrss.info/user/2.rss",
                "poll_interval_minutes": "45",
                "dry_run": "on",
                "preferred_quality": "1080p",
                "wait_hours": "3",
                "swap_window_days": "5",
                "old_cutoff_days": "90",
                "qbittorrent_url": "http://qbt.local",
                "qbittorrent_username": "admin",
                "qbittorrent_password": "secret",
                "qbittorrent_category": "showgrab",
                "jellyfin_url": "http://jf.local",
                "jellyfin_api_key": "key",
                "smtp_host": "smtp.gmail.com",
                "smtp_port": "587",
                "smtp_username": "a@example.com",
                "smtp_password": "app-password",
                "smtp_from": "a@example.com",
                "smtp_to": "a@example.com, b@example.com",
                "smtp_tls_mode": "starttls",
                "tv_root": "/downloads/tv_series",
                "path_mappings": "/media1 -> /downloads/tv_series\n/media2 -> /downloads/movies",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    reloaded = settings_store.load()
    assert reloaded.feed_url == "https://showrss.info/user/2.rss"
    assert reloaded.poll_interval_minutes == 45.0
    assert reloaded.dry_run is True
    assert reloaded.preferred_quality == "1080p"
    assert reloaded.smtp_to == ["a@example.com", "b@example.com"]
    assert reloaded.smtp_use_tls is True
    assert reloaded.smtp_use_ssl is False
    assert reloaded.path_mappings == [
        ("/media1", "/downloads/tv_series"),
        ("/media2", "/downloads/movies"),
    ]


def test_saving_settings_with_dry_run_unchecked_is_false(monkeypatch):
    app, settings_store, *_ = make_app(monkeypatch)
    with TestClient(app) as client:
        client.post(
            "/settings",
            data={
                "feed_url": "", "poll_interval_minutes": "30",
                # dry_run field omitted entirely — an unchecked HTML checkbox
                # sends nothing, must be interpreted as False.
                "preferred_quality": "720p", "wait_hours": "6", "swap_window_days": "7", "old_cutoff_days": "180",
                "qbittorrent_url": "", "qbittorrent_username": "", "qbittorrent_password": "", "qbittorrent_category": "",
                "jellyfin_url": "", "jellyfin_api_key": "",
                "smtp_host": "", "smtp_port": "587", "smtp_username": "", "smtp_password": "",
                "smtp_from": "", "smtp_to": "", "smtp_tls_mode": "starttls",
                "tv_root": "/downloads/tv_series", "path_mappings": "",
            },
            follow_redirects=False,
        )
    assert settings_store.load().dry_run is False


# --- test-connection endpoints --------------------------------------------


def test_test_qbittorrent_reports_ok(monkeypatch):
    app, *_ = make_app(monkeypatch)

    class OkDownloader:
        def __init__(self, *a, **k):
            pass

        def test_connection(self):
            return True

    monkeypatch.setattr("showgrab.adapters.qbittorrent.QbittorrentDownloader", OkDownloader)
    with TestClient(app) as client:
        resp = client.post(
            "/settings/test/qbittorrent",
            data={"qbittorrent_url": "http://qbt.local", "qbittorrent_username": "a", "qbittorrent_password": "b"},
        )
        assert "test-ok" in resp.text


def test_test_qbittorrent_reports_failure(monkeypatch):
    app, *_ = make_app(monkeypatch)

    class FailDownloader:
        def __init__(self, *a, **k):
            pass

        def test_connection(self):
            raise RuntimeError("connection refused")

    monkeypatch.setattr("showgrab.adapters.qbittorrent.QbittorrentDownloader", FailDownloader)
    with TestClient(app) as client:
        resp = client.post(
            "/settings/test/qbittorrent",
            data={"qbittorrent_url": "http://qbt.local", "qbittorrent_username": "a", "qbittorrent_password": "b"},
        )
        assert "test-fail" in resp.text
        assert "connection refused" in resp.text


# --- activity log ----------------------------------------------------------


def test_activity_log_renders_most_recent_first(monkeypatch):
    from datetime import timedelta

    app, settings_store, ledger_store, activity_log = make_app(monkeypatch)
    activity_log.record(NOW, dry_run=False, ok=True, summary="first poll")
    activity_log.record(NOW + timedelta(minutes=30), dry_run=True, ok=True, summary="second poll")

    with TestClient(app) as client:
        resp = client.get("/activity")
        first_pos = resp.text.find("first poll")
        second_pos = resp.text.find("second poll")
        assert second_pos < first_pos  # most recent (second) appears first


def test_activity_log_shows_expandable_digest_and_email_status(monkeypatch):
    app, settings_store, ledger_store, activity_log = make_app(monkeypatch)
    activity_log.record(
        NOW, dry_run=True, ok=True, summary="1 grabbed",
        details={"grabbed": 1, "swapped": 0, "events": 1,
                 "digest_body": "Grabbed (1)\n  - Silo: S03E02 720p\n", "emailed": True},
    )
    activity_log.record(
        NOW, dry_run=True, ok=True, summary="1 grabbed (repeat)",
        details={"grabbed": 1, "swapped": 0, "events": 1,
                 "digest_body": "Grabbed (1)\n  - Silo: S03E02 720p\n", "emailed": False},
    )

    with TestClient(app) as client:
        resp = client.get("/activity")
        assert "Silo: S03E02 720p" in resp.text  # full digest visible, not just the summary
        assert "sent" in resp.text
        assert "skipped (unchanged since last poll)" in resp.text


def test_static_htmx_is_served(monkeypatch):
    app, *_ = make_app(monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/static/htmx.min.js")
        assert resp.status_code == 200
        assert b"htmx" in resp.content.lower()
