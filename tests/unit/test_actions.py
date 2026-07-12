# Verifies: REQ-SG-033 (ignore is terminal and suppresses future notify),
#   REQ-SG-034 (retry only applies to needs-attention, resets to discovered),
#   REQ-SG-035 (grab now only for discovered/waiting with a variant, picks
#   the best-scoring one, only marks grabbed on real execution success),
#   REQ-SG-036 (manual swap only for a known, different variant; only
#   applies on real execution success).
# Scenario: build ledger entries directly (bypassing the engine, since these
#   are user-initiated overrides) and drive each action against a fake
#   downloader/jellyfin.

from datetime import datetime, timezone

from showgrab.core.ledger import Ledger
from showgrab.core.models import LedgerEntry, Status, Variant
from showgrab.core.quality import Quality
from showgrab.service import actions
from showgrab.store.settings_store import Settings

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


def make_settings(**overrides) -> Settings:
    base = dict(
        feed_url="", poll_interval_minutes=30, dry_run=False,
        preferred_quality="720p", wait_hours=6, swap_window_days=7, old_cutoff_days=180,
        qbittorrent_url="http://qbt.local", qbittorrent_username="admin", qbittorrent_password="secret",
        qbittorrent_category="showgrab",
        jellyfin_url="http://jf.local", jellyfin_api_key="key",
        smtp_host="", smtp_port=587, smtp_username="", smtp_password="",
        smtp_from="", smtp_to=[], smtp_use_tls=True, smtp_use_ssl=False,
        tv_root="/downloads/tv_series", path_mappings=[],
    )
    base.update(overrides)
    return Settings(**base)


def variant(infohash: str, quality: Quality = Quality.HD720) -> Variant:
    return Variant(
        quality=quality, is_remux=False, is_repack=False,
        infohash=infohash, magnet=f"magnet:?xt=urn:btih:{infohash}", pub_date=NOW, episode_id=infohash,
    )


def make_entry(status=Status.WAITING, variants=None, chosen_infohash=None) -> LedgerEntry:
    return LedgerEntry(
        key=("1675", 3, 2),
        show_id="1675",
        show_name="Silo",
        external_id="38052",
        first_seen=NOW,
        variants=variants or {},
        status=status,
        chosen_infohash=chosen_infohash,
    )


class FakeDownloader:
    def __init__(self, fail=False):
        self.added = []
        self.deleted = []
        self._fail = fail

    def add_magnet(self, magnet, save_path, category=None):
        if self._fail:
            raise RuntimeError("boom")
        self.added.append((magnet, save_path, category))

    def delete_with_files(self, infohash):
        if self._fail:
            raise RuntimeError("boom")
        self.deleted.append(infohash)


class FakeJellyfinPaths:
    def series_path(self, show_name):
        return None  # new series -> falls back to tv_root


# --- ignore ---------------------------------------------------------------


def test_ignore_marks_terminal_and_notified():
    ledger = Ledger()
    entry = make_entry(status=Status.NEEDS_ATTENTION)
    ledger.put(entry)
    assert actions.ignore(ledger, entry.key) is True
    assert entry.status is Status.IGNORED
    assert entry.notified is True  # REQ-SG-033: never re-surfaces in a digest


def test_ignore_missing_key_is_noop():
    ledger = Ledger()
    assert actions.ignore(ledger, ("nope", 1, 1)) is False


# --- retry -----------------------------------------------------------------


def test_retry_resets_needs_attention_to_discovered():
    ledger = Ledger()
    entry = make_entry(status=Status.NEEDS_ATTENTION)
    entry.notified = True
    ledger.put(entry)
    assert actions.retry(ledger, entry.key) is True
    assert entry.status is Status.DISCOVERED
    assert entry.notified is False


def test_retry_noop_for_non_needs_attention_status():
    ledger = Ledger()
    entry = make_entry(status=Status.WAITING)
    ledger.put(entry)
    assert actions.retry(ledger, entry.key) is False
    assert entry.status is Status.WAITING  # unchanged


# --- grab now ---------------------------------------------------------------


def test_grab_now_picks_best_variant_and_marks_grabbed_on_success():
    ledger = Ledger()
    entry = make_entry(
        status=Status.WAITING,
        variants={"hd1080": variant("hd1080", Quality.HD1080), "hd720": variant("hd720", Quality.HD720)},
    )
    ledger.put(entry)
    downloader = FakeDownloader()

    result = actions.grab_now(ledger, entry.key, make_settings(), downloader, FakeJellyfinPaths())

    assert result is True
    assert entry.status is Status.GRABBED
    assert entry.chosen_infohash == "hd720"  # preferred=720p, matches exactly
    assert entry.grabbed_at is not None
    assert downloader.added == [("magnet:?xt=urn:btih:hd720", "/downloads/tv_series/Silo", "showgrab")]


def test_grab_now_noop_when_already_grabbed():
    ledger = Ledger()
    entry = make_entry(status=Status.GRABBED, variants={"hd720": variant("hd720")}, chosen_infohash="hd720")
    ledger.put(entry)
    downloader = FakeDownloader()

    assert actions.grab_now(ledger, entry.key, make_settings(), downloader, FakeJellyfinPaths()) is False
    assert downloader.added == []


def test_grab_now_noop_when_no_variants_yet():
    ledger = Ledger()
    entry = make_entry(status=Status.WAITING, variants={})
    ledger.put(entry)
    assert actions.grab_now(ledger, entry.key, make_settings(), FakeDownloader(), FakeJellyfinPaths()) is False


def test_grab_now_does_not_mark_grabbed_on_execution_failure():
    ledger = Ledger()
    entry = make_entry(status=Status.WAITING, variants={"hd720": variant("hd720")})
    ledger.put(entry)
    downloader = FakeDownloader(fail=True)

    result = actions.grab_now(ledger, entry.key, make_settings(), downloader, FakeJellyfinPaths())

    assert result is False
    assert entry.status is Status.WAITING  # unchanged — REQ-SG-035 mirrors REQ-SG-027
    assert entry.chosen_infohash is None


# --- manual swap -------------------------------------------------------------


def test_manual_swap_deletes_old_adds_new_and_updates_ledger():
    ledger = Ledger()
    entry = make_entry(
        status=Status.GRABBED,
        variants={"hd1080": variant("hd1080", Quality.HD1080), "hd720": variant("hd720", Quality.HD720)},
        chosen_infohash="hd1080",
    )
    ledger.put(entry)
    downloader = FakeDownloader()

    result = actions.manual_swap(ledger, entry.key, "hd720", make_settings(), downloader, FakeJellyfinPaths())

    assert result is True
    assert entry.status is Status.SWAPPED
    assert entry.chosen_infohash == "hd720"
    assert downloader.deleted == ["hd1080"]
    assert downloader.added == [("magnet:?xt=urn:btih:hd720", "/downloads/tv_series/Silo", "showgrab")]


def test_manual_swap_noop_for_unknown_infohash():
    ledger = Ledger()
    entry = make_entry(status=Status.GRABBED, variants={"hd1080": variant("hd1080")}, chosen_infohash="hd1080")
    ledger.put(entry)
    assert actions.manual_swap(ledger, entry.key, "nonexistent", make_settings(), FakeDownloader(), FakeJellyfinPaths()) is False


def test_manual_swap_noop_when_target_already_chosen():
    ledger = Ledger()
    entry = make_entry(status=Status.GRABBED, variants={"hd720": variant("hd720")}, chosen_infohash="hd720")
    ledger.put(entry)
    assert actions.manual_swap(ledger, entry.key, "hd720", make_settings(), FakeDownloader(), FakeJellyfinPaths()) is False


def test_manual_swap_does_not_update_ledger_on_execution_failure():
    ledger = Ledger()
    entry = make_entry(
        status=Status.GRABBED,
        variants={"hd1080": variant("hd1080"), "hd720": variant("hd720", Quality.HD720)},
        chosen_infohash="hd1080",
    )
    ledger.put(entry)
    downloader = FakeDownloader(fail=True)

    result = actions.manual_swap(ledger, entry.key, "hd720", make_settings(), downloader, FakeJellyfinPaths())

    assert result is False
    assert entry.chosen_infohash == "hd1080"  # unchanged
    assert entry.status is Status.GRABBED
