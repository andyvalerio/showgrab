"""Persistent ledger: round-trips core.ledger.Ledger through SQLite
(REQ-SG-025). The engine never touches this module directly — it only ever
sees the plain in-memory Ledger, loaded fresh at the start of each poll.
"""

from __future__ import annotations

import json
from datetime import datetime

from ..core.ledger import Ledger
from ..core.models import LedgerEntry, Status, Variant
from ..core.quality import from_label
from .models import LedgerEntryRow


def _variant_to_dict(v: Variant) -> dict:
    return {
        "quality": v.quality.label,
        "is_remux": v.is_remux,
        "is_repack": v.is_repack,
        "infohash": v.infohash,
        "magnet": v.magnet,
        "pub_date": v.pub_date.isoformat(),
        "episode_id": v.episode_id,
    }


def _dict_to_variant(d: dict) -> Variant:
    return Variant(
        quality=from_label(d["quality"]),
        is_remux=d["is_remux"],
        is_repack=d["is_repack"],
        infohash=d["infohash"],
        magnet=d["magnet"],
        pub_date=datetime.fromisoformat(d["pub_date"]),
        episode_id=d["episode_id"],
    )


def _entry_to_row(entry: LedgerEntry, row: LedgerEntryRow) -> None:
    row.key_json = json.dumps(list(entry.key))
    row.show_id = entry.show_id
    row.show_name = entry.show_name
    row.external_id = entry.external_id
    row.first_seen = entry.first_seen.isoformat()
    row.status = entry.status.value
    row.chosen_infohash = entry.chosen_infohash
    row.grabbed_at = entry.grabbed_at.isoformat() if entry.grabbed_at else None
    row.notified = entry.notified
    row.stuck_notified = entry.stuck_notified
    row.pre_stuck_status = entry.pre_stuck_status.value if entry.pre_stuck_status else None
    row.download_progress = entry.download_progress
    row.variants_json = json.dumps({ih: _variant_to_dict(v) for ih, v in entry.variants.items()})


def _row_to_entry(row: LedgerEntryRow) -> LedgerEntry:
    return LedgerEntry(
        key=tuple(json.loads(row.key_json)),
        show_id=row.show_id,
        show_name=row.show_name,
        external_id=row.external_id,
        first_seen=datetime.fromisoformat(row.first_seen),
        variants={ih: _dict_to_variant(v) for ih, v in json.loads(row.variants_json).items()},
        status=Status(row.status),
        chosen_infohash=row.chosen_infohash,
        grabbed_at=datetime.fromisoformat(row.grabbed_at) if row.grabbed_at else None,
        notified=row.notified,
        stuck_notified=bool(row.stuck_notified),
        pre_stuck_status=Status(row.pre_stuck_status) if row.pre_stuck_status else None,
        download_progress=row.download_progress,
    )


class LedgerStore:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def load(self) -> Ledger:
        ledger = Ledger()
        with self._session_factory() as session:
            for row in session.query(LedgerEntryRow).all():
                ledger.put(_row_to_entry(row))
        return ledger

    def save(self, ledger: Ledger) -> None:
        """Upserts every entry. Full-ledger overwrite, not incremental —
        simple and correct at showgrab's scale (tens to low hundreds of
        tracked episodes), and avoids any drift between what's in memory and
        what's on disk."""
        with self._session_factory() as session:
            existing = {row.key_json: row for row in session.query(LedgerEntryRow).all()}
            for entry in ledger.all():
                key_json = json.dumps(list(entry.key))
                row = existing.get(key_json)
                if row is None:
                    row = LedgerEntryRow(key_json=key_json)
                    session.add(row)
                _entry_to_row(entry, row)
            session.commit()
