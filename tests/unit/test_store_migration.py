# Verifies: REQ-SG-049 (additive schema changes reach a database that already
#   exists), plus the round-trip of the phase-6 transfer fields (REQ-SG-025).
# Scenario: build a database with the PRE-phase-6 ledger schema, put a row in
#   it, then run init_db() over it exactly as a service restart would, and
#   confirm the new columns appear, the old row survives, and the store can
#   read and write it.
#
# Why this file exists: every other test builds its schema from scratch with
# create_all(), which always produces the current columns. That makes a
# missing-column bug invisible to the whole suite while breaking every real
# deployment on upgrade — the schema equivalent of a test that only ever runs
# on an empty disk.

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from showgrab.core.models import LedgerEntry, Status
from showgrab.core.quality import Quality
from showgrab.core.models import Variant
from showgrab.store.db import init_db, make_engine, make_session_factory
from showgrab.store.ledger_store import LedgerStore
from showgrab.core.ledger import Ledger

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)

# The ledger_entries table exactly as it shipped before transfer follow-up.
LEGACY_LEDGER_DDL = """
CREATE TABLE ledger_entries (
    id INTEGER NOT NULL PRIMARY KEY,
    key_json VARCHAR NOT NULL UNIQUE,
    show_id VARCHAR NOT NULL,
    show_name VARCHAR NOT NULL,
    external_id VARCHAR,
    first_seen VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    chosen_infohash VARCHAR,
    grabbed_at VARCHAR,
    notified BOOLEAN NOT NULL,
    variants_json VARCHAR NOT NULL
)
"""

LEGACY_ROW = """
INSERT INTO ledger_entries
  (key_json, show_id, show_name, external_id, first_seen, status,
   chosen_infohash, grabbed_at, notified, variants_json)
VALUES
  ('["1", 1, 5]', '1', 'Old Show', '1000', '2026-07-01T00:00:00+00:00',
   'grabbed', 'p720', '2026-07-01T00:00:00+00:00', 0, '{}')
"""


def legacy_db(tmp_path):
    """A real on-disk database at the old schema, as a running install has."""
    db_path = str(tmp_path / "showgrab.sqlite")
    eng = make_engine(db_path)
    with eng.begin() as conn:
        conn.execute(text(LEGACY_LEDGER_DDL))
        conn.execute(text(LEGACY_ROW))
    return db_path


def columns(engine, table):
    with engine.begin() as conn:
        return {r[1] for r in conn.execute(text(f"PRAGMA table_info({table})"))}


def test_legacy_database_is_missing_the_new_columns(tmp_path):
    """Guards the premise: if this ever fails, the fixture stopped being a
    legacy schema and the migration test below proves nothing."""
    eng = make_engine(legacy_db(tmp_path))
    assert "stuck_notified" not in columns(eng, "ledger_entries")


def test_init_db_adds_missing_columns_to_an_existing_database(tmp_path):
    db_path = legacy_db(tmp_path)
    eng = make_engine(db_path)

    init_db(eng)  # what a service restart does

    cols = columns(eng, "ledger_entries")
    assert {"stuck_notified", "pre_stuck_status", "download_progress"} <= cols
    # The pre-existing row is still there, and backfilled rather than nulled.
    with eng.begin() as conn:
        row = conn.execute(
            text("SELECT show_name, status, stuck_notified FROM ledger_entries")
        ).one()
    assert row[0] == "Old Show"
    assert row[1] == "grabbed"
    assert row[2] == 0  # server_default backfilled the NOT NULL column


def test_init_db_is_idempotent(tmp_path):
    """A restart must not try to re-add columns it already added."""
    eng = make_engine(legacy_db(tmp_path))
    init_db(eng)
    init_db(eng)
    init_db(eng)
    assert "stuck_notified" in columns(eng, "ledger_entries")


def test_migrated_database_round_trips_the_new_fields(tmp_path):
    eng = make_engine(legacy_db(tmp_path))
    init_db(eng)
    store = LedgerStore(make_session_factory(eng))

    ledger = store.load()  # reads the legacy row through the new mapping
    entry = ledger.all()[0]
    assert entry.show_name == "Old Show"
    assert entry.stuck_notified is False
    assert entry.pre_stuck_status is None
    assert entry.download_progress is None

    entry.status = Status.STUCK
    entry.pre_stuck_status = Status.SWAPPED
    entry.stuck_notified = True
    entry.download_progress = 0.42
    store.save(ledger)

    reloaded = store.load().all()[0]
    assert reloaded.status is Status.STUCK
    assert reloaded.pre_stuck_status is Status.SWAPPED
    assert reloaded.stuck_notified is True
    assert reloaded.download_progress == pytest.approx(0.42)


def test_new_database_gets_the_columns_without_any_migration(tmp_path):
    eng = make_engine(str(tmp_path / "fresh.sqlite"))
    init_db(eng)
    assert {"stuck_notified", "pre_stuck_status", "download_progress"} <= columns(
        eng, "ledger_entries"
    )
