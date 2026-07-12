"""Save-path resolution glue: Jellyfin lookup (I/O) + core.paths (pure)
(REQ-SG-028)."""

from __future__ import annotations

from ..adapters.jellyfin import JellyfinLibrary
from ..core.paths import map_series_path, new_series_save_path


def resolve_save_path(
    show_name: str,
    jellyfin: JellyfinLibrary,
    path_mappings: list[tuple[str, str]],
    tv_root: str,
) -> str:
    """Prefers the media server's existing folder for this series, translated
    through configured path mappings; falls back to a sanitized new folder
    under tv_root when the series isn't in the library yet. Propagates
    JellyfinError on transport failure — the caller (executor) decides how
    that affects the poll (REQ-SG-027)."""
    existing = jellyfin.series_path(show_name)
    if existing is not None:
        mapped = map_series_path(existing, path_mappings)
        if mapped is not None:
            return mapped
    return new_series_save_path(show_name, tv_root)
