# Verifies the decision engine:
#   REQ-SG-008 (already-in-library → skipped-have, silent),
#   REQ-SG-009 (aired before old_cutoff → skipped-old + one notify),
#   REQ-SG-010 (no metadata airdate → needs-attention + one notify, no download),
#   REQ-SG-011 (wait for preferred quality or wait_hours, then grab best),
#   REQ-SG-012 (swap to a strictly better variant within swap_window, then settle),
#   REQ-SG-013 (a REPACK of the chosen tier triggers a replace within the window),
#   REQ-SG-014 (idempotent: re-planning the same feed produces no dup actions/events),
#   REQ-SG-023 (a check that raises leaves the episode discovered for retry on
#     the next poll, with no false notification or misclassification).
# Scenario: drive one episode through each path with a fake library/metadata and
#   an explicit clock, asserting statuses, actions, and event counts.

from datetime import datetime, timedelta, timezone

from showgrab.core import engine
from showgrab.core.digest import DIGEST_KINDS
from showgrab.core.ledger import Ledger
from showgrab.core.models import (
    Config,
    GrabAction,
    Status,
    SwapAction,
)
from showgrab.core.quality import Quality
from showgrab.core.release import parse_release

from tests.conftest import make_item

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
RECENT = NOW - timedelta(days=1)
OLD = NOW - timedelta(days=400)


class _Lib:
    def __init__(self, have=False):
        self.have = have

    def has_episode(self, *a, **k):
        return self.have


class _Meta:
    def __init__(self, airdate=RECENT):
        self._airdate = airdate

    def airdate(self, *a, **k):
        return self._airdate


def checks(have=False, airdate=RECENT):
    return engine.Checks(_Lib(have), _Meta(airdate))


def rel(title, **kw):
    return parse_release(make_item(title, **kw))


def kinds(actions):
    return [type(a).__name__ for a in actions]


# --- gates ---------------------------------------------------------------


def test_skipped_have_is_silent():
    ledger = Ledger()
    r = [rel("Silo S03E02 720p", show_id="1", infohash="a")]
    result = engine.plan(ledger, r, NOW, Config(preferred_quality=Quality.HD720), checks(have=True))
    entry = ledger.all()[0]
    assert entry.status is Status.SKIPPED_HAVE
    assert result.actions == []
    assert result.events == []  # REQ-SG-008: no notification


def test_skipped_old_notifies_once():
    ledger = Ledger()
    r = [rel("Silo S03E02 720p", show_id="1", infohash="a")]
    cfg = Config(preferred_quality=Quality.HD720)
    r1 = engine.plan(ledger, r, NOW, cfg, checks(airdate=OLD))
    entry = ledger.all()[0]
    assert entry.status is Status.SKIPPED_OLD
    assert [e.kind for e in r1.events] == ["skipped-old"]
    assert r1.actions == []
    # The episode identifier must be in the message — a digest/activity-log
    # line naming only the show, not which episode, isn't actionable.
    assert r1.events[0].detail.startswith("S03E02 ")
    # Re-plan: no second notification (REQ-SG-009 + REQ-SG-014).
    r2 = engine.plan(ledger, r, NOW, cfg, checks(airdate=OLD))
    assert r2.events == []


def test_no_airdate_is_needs_attention_and_not_downloaded():
    ledger = Ledger()
    r = [rel("Silo S03E02 720p", show_id="1", infohash="a")]
    cfg = Config(preferred_quality=Quality.HD720)
    result = engine.plan(ledger, r, NOW, cfg, checks(airdate=None))
    entry = ledger.all()[0]
    assert entry.status is Status.NEEDS_ATTENTION
    assert [e.kind for e in result.events] == ["needs-attention"]
    assert result.actions == []  # REQ-SG-010
    assert result.events[0].detail.startswith("S03E02:")


# --- wait / grab ---------------------------------------------------------


def test_grabs_immediately_when_preferred_offered():
    ledger = Ledger()
    r = [
        rel("Show S01E01 2160p", show_id="1", infohash="uhd"),
        rel("Show S01E01 1080p", show_id="1", infohash="hd"),
        rel("Show S01E01 720p", show_id="1", infohash="sd720"),
    ]
    result = engine.plan(ledger, r, NOW, Config(preferred_quality=Quality.HD720), checks())
    entry = ledger.all()[0]
    assert entry.status is Status.GRABBED
    assert kinds(result.actions) == ["GrabAction"]
    grab = result.actions[0]
    assert isinstance(grab, GrabAction)
    assert grab.variant.quality is Quality.HD720  # REQ-SG-011: preferred chosen


def test_waits_for_preferred_then_grabs_best_after_window():
    ledger = Ledger()
    # Only 1080p and 2160p offered; preferred is 720p.
    r = [
        rel("Show S01E01 2160p", show_id="1", infohash="uhd"),
        rel("Show S01E01 1080p", show_id="1", infohash="hd"),
    ]
    cfg = Config(preferred_quality=Quality.HD720, wait_hours=6.0)

    # First sight: hold out for the preferred quality.
    r1 = engine.plan(ledger, r, NOW, cfg, checks())
    assert ledger.all()[0].status is Status.WAITING
    assert r1.actions == []

    # After the wait window: grab the closest available (1080p).
    r2 = engine.plan(ledger, r, NOW + timedelta(hours=7), cfg, checks())
    entry = ledger.all()[0]
    assert entry.status is Status.GRABBED
    assert kinds(r2.actions) == ["GrabAction"]
    assert r2.actions[0].variant.quality is Quality.HD1080  # REQ-SG-011


def test_needs_attention_when_no_usable_quality_after_wait():
    ledger = Ledger()
    # Episode identity parses, but no resolution token → no variant to grab.
    r = [rel("Show S01E01 With No Resolution Token", show_id="1", infohash="x")]
    cfg = Config(preferred_quality=Quality.HD720, wait_hours=6.0)
    engine.plan(ledger, r, NOW, cfg, checks())
    assert ledger.all()[0].status is Status.WAITING  # still hoping one appears
    result = engine.plan(ledger, r, NOW + timedelta(hours=7), cfg, checks())
    assert ledger.all()[0].status is Status.NEEDS_ATTENTION
    assert [e.kind for e in result.events] == ["needs-attention"]
    assert result.events[0].detail.startswith("S01E01:")


# --- swap ----------------------------------------------------------------


def test_swaps_to_better_variant_within_window():
    ledger = Ledger()
    cfg = Config(preferred_quality=Quality.HD720, wait_hours=6.0, swap_window_days=7.0)
    only_1080 = [rel("Show S01E01 1080p", show_id="1", infohash="hd")]

    # Grab 1080p after the wait window (720p not yet offered).
    engine.plan(ledger, only_1080, NOW, cfg, checks())
    engine.plan(ledger, only_1080, NOW + timedelta(hours=7), cfg, checks())
    assert ledger.all()[0].chosen.quality is Quality.HD1080

    # 720p appears two days later → swap down to it (REQ-SG-012).
    with_720 = only_1080 + [rel("Show S01E01 720p", show_id="1", infohash="sd720")]
    result = engine.plan(ledger, with_720, NOW + timedelta(days=2), cfg, checks())
    entry = ledger.all()[0]
    assert entry.status is Status.SWAPPED
    assert kinds(result.actions) == ["SwapAction"]
    swap = result.actions[0]
    assert isinstance(swap, SwapAction)
    assert swap.old_infohash == "hd"
    assert swap.new_variant.quality is Quality.HD720
    assert entry.chosen.quality is Quality.HD720

    # Re-plan: no duplicate swap (REQ-SG-014).
    again = engine.plan(ledger, with_720, NOW + timedelta(days=2, hours=1), cfg, checks())
    assert again.actions == []


def test_no_swap_after_window():
    ledger = Ledger()
    cfg = Config(preferred_quality=Quality.HD720, wait_hours=6.0, swap_window_days=7.0)
    only_1080 = [rel("Show S01E01 1080p", show_id="1", infohash="hd")]
    engine.plan(ledger, only_1080, NOW, cfg, checks())
    engine.plan(ledger, only_1080, NOW + timedelta(hours=7), cfg, checks())

    # 720p appears 10 days after the grab — past the swap window.
    with_720 = only_1080 + [rel("Show S01E01 720p", show_id="1", infohash="sd720")]
    result = engine.plan(ledger, with_720, NOW + timedelta(days=10), cfg, checks())
    entry = ledger.all()[0]
    assert entry.status is Status.SETTLED
    assert result.actions == []
    assert entry.chosen.quality is Quality.HD1080  # unchanged


def test_repack_of_chosen_tier_triggers_replace():
    ledger = Ledger()
    cfg = Config(preferred_quality=Quality.HD720, swap_window_days=7.0)
    plain = [rel("Show S01E01 720p", show_id="1", infohash="p720")]

    # 720p is preferred and offered → grab immediately.
    engine.plan(ledger, plain, NOW, cfg, checks())
    assert ledger.all()[0].chosen.infohash == "p720"

    # A REPACK at the same tier appears within the window → replace (REQ-SG-013).
    with_repack = plain + [rel("Show S01E01 REPACK 720p", show_id="1", infohash="r720")]
    result = engine.plan(ledger, with_repack, NOW + timedelta(days=1), cfg, checks())
    entry = ledger.all()[0]
    assert entry.status is Status.SWAPPED
    assert kinds(result.actions) == ["SwapAction"]
    assert entry.chosen.infohash == "r720"

    # Idempotent afterward (REQ-SG-014).
    again = engine.plan(ledger, with_repack, NOW + timedelta(days=1, hours=1), cfg, checks())
    assert again.actions == []


# --- idempotency & orphans ----------------------------------------------


def test_replanning_same_feed_is_idempotent():
    ledger = Ledger()
    cfg = Config(preferred_quality=Quality.HD720)
    r = [rel("Show S01E01 720p", show_id="1", infohash="p720")]
    r1 = engine.plan(ledger, r, NOW, cfg, checks())
    assert kinds(r1.actions) == ["GrabAction"]
    r2 = engine.plan(ledger, r, NOW, cfg, checks())
    assert r2.actions == []
    assert r2.events == []  # REQ-SG-014


def test_item_without_episode_marker_is_needs_attention_once():
    ledger = Ledger()
    cfg = Config(preferred_quality=Quality.HD720)
    r = [rel("No Episode Marker Here 1080p", show_id="9", infohash="np")]
    r1 = engine.plan(ledger, r, NOW, cfg, checks())
    entry = ledger.all()[0]
    assert entry.status is Status.NEEDS_ATTENTION
    assert [e.kind for e in r1.events] == ["needs-attention"]
    r2 = engine.plan(ledger, r, NOW, cfg, checks())
    assert r2.events == []


# --- terminal-state silence ----------------------------------------------
# Regression: every terminal entry is re-walked by plan() on every poll, so a
# blanket notify on TERMINAL leaked an event for statuses meant to be silent.
# In production that arrived as a digest email reading "settled (1)" about a
# week after each grab, and one reading "skipped-have (1)" for every episode
# already in the library. REQ-SG-021 fixes the digest kinds to grabbed /
# swapped / skipped-old / needs-attention; neither of those is among them.


def test_settling_emits_no_event_on_the_poll_that_settles():
    ledger = Ledger()
    cfg = Config(preferred_quality=Quality.HD720, swap_window_days=7.0)
    r = [rel("Show S01E01 720p", show_id="1", infohash="p720")]
    engine.plan(ledger, r, NOW, cfg, checks())
    assert ledger.all()[0].status is Status.GRABBED

    settling = engine.plan(ledger, r, NOW + timedelta(days=8), cfg, checks())
    assert ledger.all()[0].status is Status.SETTLED
    assert settling.events == []  # REQ-SG-012: settling is silent


def test_settled_entry_stays_silent_on_every_later_poll():
    ledger = Ledger()
    cfg = Config(preferred_quality=Quality.HD720, swap_window_days=7.0)
    r = [rel("Show S01E01 720p", show_id="1", infohash="p720")]
    engine.plan(ledger, r, NOW, cfg, checks())
    engine.plan(ledger, r, NOW + timedelta(days=8), cfg, checks())
    assert ledger.all()[0].status is Status.SETTLED

    # The bug surfaced one poll AFTER the settle, not on the settling poll
    # itself — so re-plan repeatedly, as a real 2-hourly scheduler does.
    for hours in range(2, 25, 2):
        later = engine.plan(ledger, r, NOW + timedelta(days=8, hours=hours), cfg, checks())
        assert later.events == [], f"settled entry notified {hours}h after settling"
        assert later.actions == []


def test_skipped_have_stays_silent_on_every_later_poll():
    ledger = Ledger()
    cfg = Config(preferred_quality=Quality.HD720)
    r = [rel("Silo S03E02 720p", show_id="1", infohash="a")]
    engine.plan(ledger, r, NOW, cfg, checks(have=True))
    assert ledger.all()[0].status is Status.SKIPPED_HAVE

    # REQ-SG-008 is silent for the episode's whole life, not just first sight.
    for hours in range(2, 25, 2):
        later = engine.plan(ledger, r, NOW + timedelta(hours=hours), cfg, checks(have=True))
        assert later.events == [], f"skipped-have entry notified {hours}h after the gate"


def test_ignored_entry_is_never_notified():
    ledger = Ledger()
    cfg = Config(preferred_quality=Quality.HD720)
    r = [rel("Show S01E01 720p", show_id="1", infohash="p720")]
    engine.plan(ledger, r, NOW, cfg, checks())
    entry = ledger.all()[0]
    # What the web UI's ignore action does (REQ-SG-033), minus the notified
    # flag it also sets — so this asserts the engine's own silence, not that
    # the flag happens to paper over it.
    entry.status = Status.IGNORED
    entry.notified = False

    later = engine.plan(ledger, r, NOW + timedelta(days=1), cfg, checks())
    assert later.events == []


def test_full_lifecycle_only_produces_digest_kinds():
    """End-to-end guard: whatever the engine emits across a grab -> swap ->
    settle lifecycle must be renderable by the digest, which only titles the
    four REQ-SG-021 kinds and would otherwise print a raw status string."""
    ledger = Ledger()
    cfg = Config(preferred_quality=Quality.HD720, wait_hours=6.0, swap_window_days=7.0)
    only_1080 = [rel("Show S01E01 1080p", show_id="1", infohash="hd")]
    with_720 = only_1080 + [rel("Show S01E01 720p", show_id="1", infohash="sd720")]

    emitted = []
    emitted += engine.plan(ledger, only_1080, NOW, cfg, checks()).events
    emitted += engine.plan(ledger, only_1080, NOW + timedelta(hours=7), cfg, checks()).events
    emitted += engine.plan(ledger, with_720, NOW + timedelta(days=2), cfg, checks()).events
    for day in range(8, 15):
        emitted += engine.plan(ledger, with_720, NOW + timedelta(days=day), cfg, checks()).events

    assert ledger.all()[0].status is Status.SETTLED
    assert [e.kind for e in emitted] == ["grabbed", "swapped"]
    assert set(e.kind for e in emitted) <= set(DIGEST_KINDS)


# --- gate resilience (REQ-SG-023) -----------------------------------------


class _FlakyOnce:
    """Raises once (simulating a transient adapter failure), then delegates
    to a real check on every subsequent call."""

    def __init__(self, real, raise_on):
        self._real = real
        self._raise_on = raise_on
        self._raised = False

    def has_episode(self, *a, **k):
        if self._raise_on == "library" and not self._raised:
            self._raised = True
            raise RuntimeError("transient network error")
        return self._real.has_episode(*a, **k)

    def airdate(self, *a, **k):
        if self._raise_on == "metadata" and not self._raised:
            self._raised = True
            raise RuntimeError("transient network error")
        return self._real.airdate(*a, **k)


def test_transient_library_check_failure_stays_discovered_and_retries():
    ledger = Ledger()
    cfg = Config(preferred_quality=Quality.HD720)
    r = [rel("Show S01E01 720p", show_id="1", infohash="a")]

    flaky_checks = engine.Checks(_FlakyOnce(_Lib(have=False), "library"), _Meta())
    r1 = engine.plan(ledger, r, NOW, cfg, flaky_checks)
    entry = ledger.all()[0]
    assert entry.status is Status.DISCOVERED  # not misclassified
    assert r1.actions == []
    assert r1.events == []  # no false notification

    r2 = engine.plan(ledger, r, NOW, cfg, flaky_checks)  # adapter recovers
    assert ledger.all()[0].status is Status.GRABBED


def test_transient_metadata_check_failure_stays_discovered_and_retries():
    ledger = Ledger()
    cfg = Config(preferred_quality=Quality.HD720)
    r = [rel("Show S01E01 720p", show_id="1", infohash="a")]

    flaky_checks = engine.Checks(_Lib(have=False), _FlakyOnce(_Meta(), "metadata"))
    r1 = engine.plan(ledger, r, NOW, cfg, flaky_checks)
    assert ledger.all()[0].status is Status.DISCOVERED
    assert r1.events == []

    r2 = engine.plan(ledger, r, NOW, cfg, flaky_checks)
    assert ledger.all()[0].status is Status.GRABBED
