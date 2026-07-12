# Verifies: REQ-SG-021 (digest groups notify events by kind and counts them;
#   an empty event list produces no digest at all), REQ-SG-026 (dry-run
#   digests are unambiguously marked in both subject and body).
# Scenario: build_digest over a mixed set of events and over the empty list.

from showgrab.core.digest import build_digest
from showgrab.core.models import NotifyEvent


def test_empty_events_produce_no_digest():
    assert build_digest([]) is None


def test_digest_groups_by_kind_and_counts():
    events = [
        NotifyEvent("grabbed", ("1", 1, 1), "Silo", "S01E01 720p"),
        NotifyEvent("grabbed", ("1", 1, 2), "Silo", "S01E02 720p"),
        NotifyEvent("skipped-old", ("2", 8, 6), "Doctor Who", "aired 2014-10-25"),
    ]
    subject, body = build_digest(events)
    assert "3 events" in subject
    assert "Grabbed (2)" in body
    assert "Skipped (too old) (1)" in body
    assert "Silo: S01E01 720p" in body
    assert "Doctor Who: aired 2014-10-25" in body


def test_singular_event_count_in_subject():
    events = [NotifyEvent("grabbed", ("1", 1, 1), "Silo", "S01E01 720p")]
    subject, _ = build_digest(events)
    assert "1 event" in subject and "1 events" not in subject


def test_dry_run_marks_subject_and_body():
    events = [NotifyEvent("grabbed", ("1", 1, 1), "Silo", "S01E01 720p")]
    subject, body = build_digest(events, dry_run=True)
    assert subject.startswith("[DRY RUN]")
    assert "DRY RUN" in body


def test_non_dry_run_has_no_dry_run_marker():
    events = [NotifyEvent("grabbed", ("1", 1, 1), "Silo", "S01E01 720p")]
    subject, body = build_digest(events, dry_run=False)
    assert "DRY RUN" not in subject
    assert "DRY RUN" not in body
