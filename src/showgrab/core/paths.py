"""Save-path resolution: pure string logic, no I/O (REQ-SG-028).

Fetching the media server's existing series path is an adapter concern
(service/paths_resolver.py calls Jellyfin); this module only maps that path
through user-configured prefixes, or builds a fresh path for a new series.
"""

from __future__ import annotations

import re

_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)  # strips punctuation: ! ? : ' , etc.
_WHITESPACE = re.compile(r"\s+")

_RELEASE_MARKER = re.compile(
    r"S\d{1,2}E\d{1,2}"  # SxxEyy episode code
    r"|\b(2160p|1080p|720p|480p)\b"  # resolution
    r"|\b(WEB-?DL|HDTV|BluRay|BDRip|HEVC|x264|x265|H\s?26[45])\b",
    re.IGNORECASE,
)


def looks_like_release_folder(folder_name: str) -> bool:
    """True when a folder's name carries the markers of one specific
    torrent release (episode code, resolution, encode tag) rather than a
    clean series name. Judged purely on the name — no need to inspect the
    folder's actual contents. This is the signature of a media server
    mis-registering a single downloaded episode's own folder as if it were
    the whole series (REQ-SG-028; found live 2026-07-12 against a real,
    messy library where a flat one-file-per-folder layout produced several
    such folders alongside the genuine series directory). A path this
    matches must never be reused as a save path — a fresh one is created
    instead."""
    return bool(_RELEASE_MARKER.search(folder_name))


def sanitize_folder_name(name: str) -> str:
    """Builds a folder name matching this library's existing dot-separated
    convention (e.g. "American Dad!" -> "American.Dad", "Doctor Who" ->
    "Doctor.Who" — observed live 2026-07-12): strip punctuation entirely,
    then join words with dots. Also safe as a filesystem name — punctuation
    invalid on some filesystems (: / \\ etc.) is a subset of what's stripped
    here anyway."""
    cleaned = _NON_WORD.sub("", name)
    cleaned = _WHITESPACE.sub(".", cleaned.strip())
    cleaned = cleaned.strip(".")
    return cleaned or "Unknown"


def map_series_path(media_server_path: str, mappings: list[tuple[str, str]]) -> str | None:
    """Translate a media-server-side path to the downloader-side path using
    the longest matching (from_prefix, to_prefix) pair. None if no mapping
    matches — the caller decides the fallback."""
    best: tuple[str, str] | None = None
    for from_prefix, to_prefix in mappings:
        if media_server_path.startswith(from_prefix) and (
            best is None or len(from_prefix) > len(best[0])
        ):
            best = (from_prefix, to_prefix)
    if best is None:
        return None
    from_prefix, to_prefix = best
    return to_prefix + media_server_path[len(from_prefix):]


def new_series_save_path(show_name: str, tv_root: str) -> str:
    """Where a brand-new series (not yet in the media library) should land."""
    root = tv_root.rstrip("/")
    return f"{root}/{sanitize_folder_name(show_name)}"
