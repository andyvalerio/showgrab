# Verifies the activity log store records and retrieves poll history — the
#   data source a future UI/settings page reads from (not itself a numbered
#   REQ-SG yet; covered structurally by REQ-SG-030's "don't corrupt state").
# Scenario: record a few entries, confirm ordering (most recent first) and
#   that details round-trip through JSON.

from datetime import datetime, timedelta, timezone

from showgrab.store.activity_store import ActivityLogStore
from showgrab.store.db import init_db, make_engine, make_session_factory

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


def make_store(tmp_path):
    engine = make_engine(":memory:")
    init_db(engine)
    return ActivityLogStore(make_session_factory(engine))


def test_record_and_recent_round_trip(tmp_path):
    store = make_store(tmp_path)
    store.record(NOW, dry_run=False, ok=True, summary="1 grabbed", details={"grabbed": 1})
    store.record(
        NOW + timedelta(minutes=30), dry_run=True, ok=True, summary="0 events", details={}
    )

    recent = store.recent()
    assert len(recent) == 2
    # most recent first
    assert recent[0].summary == "0 events"
    assert recent[0].dry_run is True
    assert recent[1].summary == "1 grabbed"
    assert recent[1].details == {"grabbed": 1}
    assert recent[1].timestamp == NOW


def test_recent_respects_limit(tmp_path):
    store = make_store(tmp_path)
    for i in range(5):
        store.record(NOW + timedelta(minutes=i), dry_run=False, ok=True, summary=f"poll {i}")
    assert len(store.recent(limit=2)) == 2


def test_ok_false_recorded_for_execution_errors(tmp_path):
    store = make_store(tmp_path)
    store.record(NOW, dry_run=False, ok=False, summary="execution failed", details={"error": "boom"})
    record = store.recent()[0]
    assert record.ok is False
    assert record.details == {"error": "boom"}
