"""Structured, queryable record of every poll — the "what did showgrab do"
log a future UI (phase 4) reads from."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from .models import ActivityLogRow


@dataclass
class ActivityRecord:
    timestamp: datetime
    dry_run: bool
    ok: bool
    summary: str
    details: dict = field(default_factory=dict)


def _row_to_record(row: ActivityLogRow) -> ActivityRecord:
    return ActivityRecord(
        timestamp=datetime.fromisoformat(row.timestamp),
        dry_run=row.dry_run,
        ok=row.ok,
        summary=row.summary,
        details=json.loads(row.details_json),
    )


class ActivityLogStore:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def record(
        self,
        timestamp: datetime,
        *,
        dry_run: bool,
        ok: bool,
        summary: str,
        details: dict | None = None,
    ) -> None:
        with self._session_factory() as session:
            session.add(
                ActivityLogRow(
                    timestamp=timestamp.isoformat(),
                    dry_run=dry_run,
                    ok=ok,
                    summary=summary,
                    details_json=json.dumps(details or {}),
                )
            )
            session.commit()

    def recent(self, limit: int = 50) -> list[ActivityRecord]:
        with self._session_factory() as session:
            rows = (
                session.query(ActivityLogRow)
                .order_by(ActivityLogRow.id.desc())
                .limit(limit)
                .all()
            )
            return [_row_to_record(r) for r in rows]
