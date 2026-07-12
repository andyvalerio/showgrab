"""In-memory episode ledger and the external-check protocols the engine needs.

Phase 1 keeps the ledger in memory; a SQLite/SQLAlchemy backing with the same
interface arrives in a later phase. The engine never talks to Jellyfin/TVmaze
directly — it calls these protocols, which real adapters implement in phase 2.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import LedgerEntry


class LibraryChecker(Protocol):
    def has_episode(
        self,
        show_id: str,
        external_id: str | None,
        show_name: str,
        season: int,
        episode: int,
    ) -> bool:
        """True if the media library already contains this episode (REQ-SG-008)."""
        ...


class MetadataResolver(Protocol):
    def airdate(
        self,
        external_id: str | None,
        show_name: str,
        season: int,
        episode: int,
    ) -> datetime | None:
        """Original air date, or None if no metadata match (REQ-SG-009/010)."""
        ...


class Ledger:
    def __init__(self) -> None:
        self._entries: dict[tuple, LedgerEntry] = {}

    def get(self, key: tuple) -> LedgerEntry | None:
        return self._entries.get(key)

    def put(self, entry: LedgerEntry) -> None:
        self._entries[entry.key] = entry

    def all(self) -> list[LedgerEntry]:
        return list(self._entries.values())
