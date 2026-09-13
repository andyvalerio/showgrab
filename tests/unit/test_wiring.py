# Verifies: REQ-SG-050 (dry-run gets no transfer tracker, so REQ-SG-026's
#   "MUST NOT call the downloader" stays absolute), and that live mode does
#   get one (REQ-SG-045 — without it the whole follow-up is dead code).
# Scenario: build Checks from Settings both ways and inspect what came back.
#
# This is the seam where the feature could silently not exist: every engine
# test supplies its own tracker, so a build_checks that forgot to pass one
# would leave the suite green and production blind.

from showgrab.adapters.qbittorrent import QbittorrentDownloader
from showgrab.service.wiring import build_checks

from tests.unit.test_poll import make_settings


def test_live_mode_gets_a_transfer_tracker():
    checks = build_checks(make_settings(dry_run=False))
    assert isinstance(checks.transfers, QbittorrentDownloader)


def test_dry_run_gets_no_transfer_tracker():
    """REQ-SG-050. scripts/live_dry_run_test.py enforces the same rule at
    runtime with a PoisonDownloader that raises on ANY method call, read-only
    ones included — a transfer lookup in dry-run would trip it."""
    checks = build_checks(make_settings(dry_run=True))
    assert checks.transfers is None


def test_dry_run_checks_still_have_library_and_metadata():
    """Only the tracker is withheld; the gates still run in full (REQ-SG-026)."""
    checks = build_checks(make_settings(dry_run=True))
    assert checks.library is not None
    assert checks.metadata is not None
