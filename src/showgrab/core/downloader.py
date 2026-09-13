"""The Downloader contract adapters must satisfy (REQ-SG-016/017).

Executing engine actions (GrabAction/SwapAction) against a Downloader is a
phase-3 orchestration concern (the poll loop); this module only defines the
interface so phase-2 adapters have a contract to implement against.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TransferStatus:
    """One torrent's transfer state as the download client reports it."""

    infohash: str
    progress: float  # 0.0 - 1.0
    state: str  # the client's own state string, carried for the digest detail
    complete: bool


class Downloader(Protocol):
    def add_magnet(self, magnet: str, save_path: str, category: str | None = None) -> None:
        """Start downloading a magnet link into save_path (REQ-SG-016)."""
        ...

    def delete_with_files(self, infohash: str) -> None:
        """Remove a torrent and its downloaded files (REQ-SG-017)."""
        ...

    def transfer_status(self, infohashes: list[str]) -> dict[str, TransferStatus]:
        """Transfer status for each infohash, keyed by lowercase hash
        (REQ-SG-043). Batched deliberately: one lookup per poll for every
        in-flight episode, not one call per episode.

        A hash the client doesn't know about MUST be ABSENT from the result
        rather than reported as incomplete — "removed from the client" and
        "still downloading" are different problems and the engine tells them
        apart by presence (REQ-SG-047)."""
        ...
