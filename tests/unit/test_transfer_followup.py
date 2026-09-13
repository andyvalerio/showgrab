# Verifies download follow-through:
#   REQ-SG-043 (batched transfer lookup — one call per poll, not one per episode),
#   REQ-SG-045 (incomplete on the poll after the grab -> stuck + exactly ONE notify),
#   REQ-SG-046 (stuck is not terminal: silent recovery, budget resets on a new magnet),
#   REQ-SG-047 (a hash absent from a successful lookup is stuck-and-gone, not incomplete),
#   REQ-SG-048 (a lookup that raises changes nothing and alarms nothing),
#   REQ-SG-021 (whatever this emits must be a kind the digest can title).
# Scenario: drive one episode past a grab with a fake tracker standing in for
#   qBittorrent's torrents/info, asserting status, event count, and call count.
#
# The event-count assertions are the point of this file. Every in-flight entry
# is re-examined on every poll, so the natural bug here is the one the
# `settled` digest regression already demonstrated: a state that re-announces
# itself forever. "Exactly one" is asserted across many polls, never one.

from datetime import datetime, timedelta, timezone

from showgrab.core import engine
from showgrab.core.digest import DIGEST_KINDS, build_digest
from showgrab.core.downloader import TransferStatus
from showgrab.core.ledger import Ledger
from showgrab.core.models import Config, Status
from showgrab.core.quality import Quality
from showgrab.core.release import parse_release

from tests.conftest import make_item

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
RECENT = NOW - timedelta(days=1)
CFG = Config(preferred_quality=Quality.HD720, wait_hours=6.0, swap_window_days=7.0)


class _Lib:
    def has_episode(self, *a, **k):
        return False


class _Meta:
    def airdate(self, *a, **k):
        return RECENT


class _Tracker:
    """Stands in for qBittorrent's torrents/info."""

    def __init__(self, statuses=None, raises=False):
        self.statuses = dict(statuses or {})
        self.raises = raises
        self.calls: list[list[str]] = []

    def transfer_status(self, infohashes):
        self.calls.append(list(infohashes))
        if self.raises:
            raise RuntimeError("qBittorrent unreachable")
        wanted = {h.lower() for h in infohashes}
        # Mirrors the real adapter: hashes the client doesn't know are ABSENT.
        return {h: s for h, s in self.statuses.items() if h in wanted}


def downloading(infohash, progress=0.34, state="stalledDL"):
    return TransferStatus(infohash=infohash, progress=progress, state=state, complete=False)


def done(infohash):
    return TransferStatus(infohash=infohash, progress=1.0, state="stalledUP", complete=True)


def checks(tracker=None):
    return engine.Checks(_Lib(), _Meta(), transfers=tracker)


def rel(title, **kw):
    return parse_release(make_item(title, **kw))


def grabbed(ledger, tracker, releases):
    """Run the poll that grabs, leaving the entry in-flight for follow-up."""
    result = engine.plan(ledger, releases, NOW, CFG, checks(tracker))
    assert ledger.all()[0].status is Status.GRABBED
    return result


def test_completed_transfer_is_silent_and_stays_grabbed():
    ledger = Ledger()
    r = [rel("Show S01E01 720p", show_id="1", infohash="p720")]
    tracker = _Tracker({"p720": done("p720")})
    grabbed(ledger, tracker, r)

    later = engine.plan(ledger, r, NOW + timedelta(hours=2), CFG, checks(tracker))
    entry = ledger.all()[0]
    assert entry.status is Status.GRABBED  # never stuck
    assert later.events == []  # a finished download is not news
    assert entry.download_progress == 1.0


def test_incomplete_on_next_poll_is_stuck_and_notifies_once():
    ledger = Ledger()
    r = [rel("Show S01E01 720p", show_id="1", infohash="p720")]
    tracker = _Tracker({"p720": downloading("p720", progress=0.34)})
    grab = grabbed(ledger, tracker, r)
    assert [e.kind for e in grab.events] == ["grabbed"]

    first = engine.plan(ledger, r, NOW + timedelta(hours=2), CFG, checks(tracker))
    entry = ledger.all()[0]
    assert entry.status is Status.STUCK  # REQ-SG-045
    assert [e.kind for e in first.events] == ["stuck"]
    detail = first.events[0].detail
    assert detail.startswith("S01E01 ")  # which episode, not just which show
    assert "34%" in detail  # how far it got
    assert "stalledDL" in detail  # what qBittorrent calls it


def test_stuck_transfer_notifies_once_not_once_per_poll():
    ledger = Ledger()
    r = [rel("Show S01E01 720p", show_id="1", infohash="p720")]
    tracker = _Tracker({"p720": downloading("p720")})
    grabbed(ledger, tracker, r)

    alarms = 0
    for hours in range(2, 49, 2):  # a day of 2-hourly polls, still stuck
        result = engine.plan(ledger, r, NOW + timedelta(hours=hours), CFG, checks(tracker))
        alarms += sum(1 for e in result.events if e.kind == "stuck")
    assert alarms == 1, "a stuck transfer must not re-announce itself every poll"
    assert ledger.all()[0].status is Status.STUCK


def test_stuck_then_completing_recovers_silently():
    ledger = Ledger()
    r = [rel("Show S01E01 720p", show_id="1", infohash="p720")]
    tracker = _Tracker({"p720": downloading("p720")})
    grabbed(ledger, tracker, r)
    engine.plan(ledger, r, NOW + timedelta(hours=2), CFG, checks(tracker))
    assert ledger.all()[0].status is Status.STUCK

    # It was just slow, not dead.
    tracker.statuses["p720"] = done("p720")
    recovered = engine.plan(ledger, r, NOW + timedelta(hours=4), CFG, checks(tracker))
    entry = ledger.all()[0]
    assert entry.status is Status.GRABBED  # REQ-SG-046: not terminal
    assert recovered.events == []  # recovery is silent
    assert entry.pre_stuck_status is None


def test_recovery_restores_swapped_not_grabbed():
    """A stuck entry that had been swapped must come back as swapped — the
    dashboard shouldn't quietly forget a swap happened."""
    ledger = Ledger()
    only_1080 = [rel("Show S01E01 1080p", show_id="1", infohash="hd")]
    tracker = _Tracker({"hd": done("hd")})
    # Only 1080p offered against a 720p preference, so the grab waits out
    # wait_hours rather than happening on first sight.
    engine.plan(ledger, only_1080, NOW, CFG, checks(tracker))
    engine.plan(ledger, only_1080, NOW + timedelta(hours=7), CFG, checks(tracker))
    assert ledger.all()[0].status is Status.GRABBED

    with_720 = only_1080 + [rel("Show S01E01 720p", show_id="1", infohash="sd720")]
    tracker.statuses["sd720"] = downloading("sd720")
    engine.plan(ledger, with_720, NOW + timedelta(days=1), CFG, checks(tracker))
    assert ledger.all()[0].status is Status.SWAPPED

    engine.plan(ledger, with_720, NOW + timedelta(days=1, hours=2), CFG, checks(tracker))
    assert ledger.all()[0].status is Status.STUCK

    tracker.statuses["sd720"] = done("sd720")
    engine.plan(ledger, with_720, NOW + timedelta(days=1, hours=4), CFG, checks(tracker))
    assert ledger.all()[0].status is Status.SWAPPED


def test_swap_resets_the_stuck_budget():
    """A swap is a brand-new download, so it gets its own alarm (REQ-SG-046) —
    otherwise the first stuck variant would mask every later one forever."""
    ledger = Ledger()
    only_1080 = [rel("Show S01E01 1080p", show_id="1", infohash="hd")]
    tracker = _Tracker({"hd": downloading("hd")})
    engine.plan(ledger, only_1080, NOW, CFG, checks(tracker))
    engine.plan(ledger, only_1080, NOW + timedelta(hours=7), CFG, checks(tracker))  # grabs
    first = engine.plan(ledger, only_1080, NOW + timedelta(hours=9), CFG, checks(tracker))
    assert [e.kind for e in first.events] == ["stuck"]
    assert ledger.all()[0].stuck_notified is True

    # A better variant appears and is swapped to — new magnet, fresh budget.
    with_720 = only_1080 + [rel("Show S01E01 720p", show_id="1", infohash="sd720")]
    tracker.statuses["sd720"] = downloading("sd720", progress=0.02, state="metaDL")
    swapped = engine.plan(ledger, with_720, NOW + timedelta(hours=11), CFG, checks(tracker))
    assert [e.kind for e in swapped.events] == ["swapped"]
    assert ledger.all()[0].stuck_notified is False

    second = engine.plan(ledger, with_720, NOW + timedelta(hours=13), CFG, checks(tracker))
    assert [e.kind for e in second.events] == ["stuck"]
    assert "2%" in second.events[0].detail


def test_hash_missing_from_the_client_is_reported_distinctly():
    ledger = Ledger()
    r = [rel("Show S01E01 720p", show_id="1", infohash="p720")]
    tracker = _Tracker({})  # a SUCCESSFUL lookup that knows nothing about it
    grabbed(ledger, tracker, r)

    result = engine.plan(ledger, r, NOW + timedelta(hours=2), CFG, checks(tracker))
    assert ledger.all()[0].status is Status.STUCK
    assert [e.kind for e in result.events] == ["stuck"]
    # REQ-SG-047: "gone" must not read as "still downloading at 0%".
    assert "no longer in qBittorrent" in result.events[0].detail


def test_lookup_failure_changes_nothing_and_alarms_nothing():
    ledger = Ledger()
    r = [rel("Show S01E01 720p", show_id="1", infohash="p720")]
    tracker = _Tracker({"p720": downloading("p720")}, raises=True)
    grabbed(ledger, tracker, r)

    for hours in (2, 4, 6):
        result = engine.plan(ledger, r, NOW + timedelta(hours=hours), CFG, checks(tracker))
        assert result.events == []  # REQ-SG-048
        assert ledger.all()[0].status is Status.GRABBED  # not misclassified


def test_no_tracker_configured_disables_follow_up():
    """Dry-run (REQ-SG-050) and the --dry-run CLI supply no tracker at all."""
    ledger = Ledger()
    r = [rel("Show S01E01 720p", show_id="1", infohash="p720")]
    engine.plan(ledger, r, NOW, CFG, checks(None))
    for hours in (2, 4, 6):
        result = engine.plan(ledger, r, NOW + timedelta(hours=hours), CFG, checks(None))
        assert result.events == []
        assert ledger.all()[0].status is Status.GRABBED


def test_lookup_is_batched_once_per_poll():
    """REQ-SG-043: three in-flight episodes, one lookup — not three."""
    ledger = Ledger()
    r = [
        rel("Show S01E01 720p", show_id="1", infohash="a"),
        rel("Show S01E02 720p", show_id="1", infohash="b"),
        rel("Show S01E03 720p", show_id="1", infohash="c"),
    ]
    tracker = _Tracker({h: done(h) for h in ("a", "b", "c")})
    engine.plan(ledger, r, NOW, CFG, checks(tracker))
    tracker.calls.clear()

    engine.plan(ledger, r, NOW + timedelta(hours=2), CFG, checks(tracker))
    assert len(tracker.calls) == 1
    assert sorted(tracker.calls[0]) == ["a", "b", "c"]


def test_no_lookup_when_nothing_is_in_flight():
    """Don't spend a round trip on a ledger with nothing to follow up."""
    ledger = Ledger()
    tracker = _Tracker({})
    engine.plan(ledger, [], NOW, CFG, checks(tracker))
    assert tracker.calls == []


def test_stuck_is_a_kind_the_digest_can_title():
    """REQ-SG-021: an emitted kind outside DIGEST_KINDS renders as its raw
    status string in the email — the `settled` regression, repeated."""
    ledger = Ledger()
    r = [rel("Show S01E01 720p", show_id="1", infohash="p720")]
    tracker = _Tracker({"p720": downloading("p720", progress=0.5)})
    grabbed(ledger, tracker, r)
    result = engine.plan(ledger, r, NOW + timedelta(hours=2), CFG, checks(tracker))

    assert set(e.kind for e in result.events) <= set(DIGEST_KINDS)
    subject, body = build_digest(result.events)
    assert "Stuck downloading (1)" in body  # titled, not a bare "stuck (1)"
    assert "S01E01" in body and "50%" in body


def test_settling_still_wins_while_stuck():
    """Settling deliberately stayed purely time-based: an episode still stuck
    when the swap window closes is settled and stops being followed."""
    ledger = Ledger()
    r = [rel("Show S01E01 720p", show_id="1", infohash="p720")]
    tracker = _Tracker({"p720": downloading("p720")})
    grabbed(ledger, tracker, r)
    engine.plan(ledger, r, NOW + timedelta(hours=2), CFG, checks(tracker))
    assert ledger.all()[0].status is Status.STUCK

    engine.plan(ledger, r, NOW + timedelta(days=8), CFG, checks(tracker))
    assert ledger.all()[0].status is Status.SETTLED
    # And having settled, it goes quiet for good.
    for day in range(9, 13):
        assert engine.plan(ledger, r, NOW + timedelta(days=day), CFG, checks(tracker)).events == []
