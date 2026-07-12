# Verifies: REQ-SG-025 (the ledger persists across restarts, preserving
#   every field the engine depends on, and the round trip is lossless).
# Scenario: build a Ledger with a mix of entry shapes (episode, orphan,
#   multiple variants, grabbed) via the real engine, save it, load it back
#   into a *fresh* Ledger, and confirm re-running plan() against the reloaded
#   ledger behaves identically to the original (idempotent — REQ-SG-014).

from datetime import datetime, timedelta, timezone

from showgrab.core import engine
from showgrab.core.models import Config, Status
from showgrab.core.quality import Quality
from showgrab.core.release import parse_release
from showgrab.store.db import init_db, make_engine, make_session_factory
from showgrab.store.ledger_store import LedgerStore

from tests.conftest import make_item

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


class _Lib:
    def has_episode(self, *a, **k):
        return False


class _Meta:
    def airdate(self, *a, **k):
        return NOW - timedelta(days=1)


def make_store(tmp_path):
    eng = make_engine(":memory:")
    init_db(eng)
    return LedgerStore(make_session_factory(eng))


def rel(title, **kw):
    return parse_release(make_item(title, **kw))


def test_round_trip_preserves_grabbed_entry(tmp_path):
    from showgrab.core.ledger import Ledger

    ledger = Ledger()
    cfg = Config(preferred_quality=Quality.HD720)
    checks = engine.Checks(_Lib(), _Meta())
    releases = [
        rel("Silo S03E02 1080p", show_id="1675", infohash="hd1080"),
        rel("Silo S03E02 720p", show_id="1675", infohash="hd720"),
    ]
    engine.plan(ledger, releases, NOW, cfg, checks)
    original = ledger.all()[0]
    assert original.status is Status.GRABBED
    assert original.chosen.quality is Quality.HD720

    store = make_store(tmp_path)
    store.save(ledger)
    reloaded = store.load()

    entry = reloaded.get(original.key)
    assert entry is not None
    assert entry.status is Status.GRABBED
    assert entry.show_id == original.show_id
    assert entry.show_name == original.show_name
    assert entry.chosen_infohash == original.chosen_infohash
    assert entry.chosen.quality is Quality.HD720
    assert set(entry.variants) == set(original.variants)
    assert entry.grabbed_at == original.grabbed_at
    assert entry.notified == original.notified
    assert entry.first_seen == original.first_seen


def test_round_trip_preserves_orphan_entry(tmp_path):
    from showgrab.core.ledger import Ledger

    ledger = Ledger()
    cfg = Config(preferred_quality=Quality.HD720)
    checks = engine.Checks(_Lib(), _Meta())
    releases = [rel("No Episode Marker Here 1080p", show_id="9", infohash="np")]
    engine.plan(ledger, releases, NOW, cfg, checks)
    original = ledger.all()[0]
    assert original.status is Status.NEEDS_ATTENTION
    assert not original.is_episode

    store = make_store(tmp_path)
    store.save(ledger)
    reloaded = store.load()

    entry = reloaded.get(original.key)
    assert entry is not None
    assert entry.status is Status.NEEDS_ATTENTION
    assert entry.is_episode is False


def test_replanning_reloaded_ledger_is_idempotent(tmp_path):
    from showgrab.core.ledger import Ledger

    ledger = Ledger()
    cfg = Config(preferred_quality=Quality.HD720)
    checks = engine.Checks(_Lib(), _Meta())
    releases = [rel("Silo S03E02 720p", show_id="1675", infohash="hd720")]
    result1 = engine.plan(ledger, releases, NOW, cfg, checks)
    assert len(result1.actions) == 1  # a real grab happened

    store = make_store(tmp_path)
    store.save(ledger)
    reloaded = store.load()

    # Re-planning the SAME feed against the reloaded ledger must be a no-op,
    # exactly like re-planning against the original in-memory ledger would be
    # (REQ-SG-014) — proving the persisted state round-tripped losslessly.
    result2 = engine.plan(reloaded, releases, NOW, cfg, checks)
    assert result2.actions == []
    assert result2.events == []


def test_save_upserts_without_duplicating_rows(tmp_path):
    from showgrab.core.ledger import Ledger
    from showgrab.store.models import LedgerEntryRow

    ledger = Ledger()
    cfg = Config(preferred_quality=Quality.HD720)
    checks = engine.Checks(_Lib(), _Meta())
    releases = [rel("Silo S03E02 720p", show_id="1675", infohash="hd720")]
    engine.plan(ledger, releases, NOW, cfg, checks)

    store = make_store(tmp_path)
    store.save(ledger)
    store.save(ledger)  # save again, unchanged — must upsert, not duplicate

    with store._session_factory() as session:
        assert session.query(LedgerEntryRow).count() == 1
