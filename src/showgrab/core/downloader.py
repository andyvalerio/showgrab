"""The Downloader contract adapters must satisfy (REQ-SG-016/017).

Executing engine actions (GrabAction/SwapAction) against a Downloader is a
phase-3 orchestration concern (the poll loop); this module only defines the
interface so phase-2 adapters have a contract to implement against.
"""

from __future__ import annotations

from typing import Protocol


class Downloader(Protocol):
    def add_magnet(self, magnet: str, save_path: str, category: str | None = None) -> None:
        """Start downloading a magnet link into save_path (REQ-SG-016)."""
        ...

    def delete_with_files(self, infohash: str) -> None:
        """Remove a torrent and its downloaded files (REQ-SG-017)."""
        ...
