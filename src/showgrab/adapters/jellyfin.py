"""Jellyfin API client — implements core.ledger.LibraryChecker (REQ-SG-019).

Credentials (API key) are always supplied by the caller; see
core/ledger.py:LibraryChecker for the interface this satisfies.
"""

from __future__ import annotations

import re

import httpx

from ..core.paths import looks_like_release_folder

_YEAR_SUFFIX = re.compile(r"\s*\(\d{4}\)\s*$")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalize(name: str) -> str:
    """Case/whitespace-insensitive match, ignoring a trailing (YYYY) that
    showRSS and Jellyfin don't always agree on including (REQ-SG-019)."""
    name = _YEAR_SUFFIX.sub("", (name or "").strip())
    return _NON_ALNUM.sub(" ", name.lower()).strip()


class JellyfinError(RuntimeError):
    pass


class JellyfinLibrary:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-Emby-Token": api_key},
            timeout=timeout,
        )

    def has_episode(
        self,
        show_id: str,
        external_id: str | None,
        show_name: str,
        season: int,
        episode: int,
    ) -> bool:
        try:
            series = self._find_series(show_name)
            if series is None:
                return False
            episodes = self._get_episodes(series["Id"], season)
        except httpx.HTTPError as exc:
            # Never silently return False on a transport failure — that would
            # look identical to "genuinely not in the library" to the caller.
            # The engine's gate handling (REQ-SG-023) is what decides to retry.
            raise JellyfinError(f"Jellyfin lookup failed for {show_name!r}: {exc}") from exc

        return any(
            ep.get("IndexNumber") == episode and ep.get("LocationType") == "FileSystem"
            for ep in episodes
        )

    def series_path(self, show_name: str) -> str | None:
        """The series' existing WHOLE-SERIES library folder, or None if
        there isn't one yet (REQ-SG-028 — save-path resolution then creates
        a fresh folder). Deliberately stricter than has_episode()'s
        matching: a folder whose name carries a single release's own
        markers (episode code, resolution, encode tag —
        core.paths.looks_like_release_folder) is a mis-registered
        individual episode, never the real series directory, and must
        never be reused as a save path. Live-testing found several such
        folders coexisting with the genuine one in this deployment's actual
        library (2026-07-12) — see looks_like_release_folder's docstring."""
        try:
            candidates = self._search_series(show_name)
        except httpx.HTTPError as exc:
            raise JellyfinError(f"Jellyfin series_path lookup failed for {show_name!r}: {exc}") from exc

        genuine = [
            c
            for c in candidates
            if c.get("Path") and not looks_like_release_folder(c["Path"].rsplit("/", 1)[-1])
        ]
        if not genuine:
            return None
        return min(genuine, key=lambda c: len(c["Path"]))["Path"]

    def test_connection(self) -> bool:
        try:
            resp = self._client.get("/System/Info")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise JellyfinError(f"Jellyfin connection test failed: {exc}") from exc
        return True

    def _find_series(self, show_name: str) -> dict | None:
        """Any single matching Series item — used by has_episode(), where it
        doesn't matter WHICH of several duplicate folder-level entries for
        the same show gets picked: Jellyfin's episode lookup is grouped by
        the show's underlying metadata identity, not by the specific item's
        id (confirmed live 2026-07-12 — querying /Shows/{id}/Episodes for
        three differently-Path'd "American Dad!" entries returned identical
        episode lists). series_path() below needs a stricter rule and does
        its own filtering on top of _search_series()."""
        matches = self._search_series(show_name)
        return matches[0] if matches else None

    def _search_series(self, show_name: str) -> list[dict]:
        """Raises httpx.HTTPError on transport failure — callers decide how
        to wrap it; never swallowed to a value indistinguishable from
        'genuinely not found' (REQ-SG-019, REQ-SG-028)."""
        resp = self._client.get(
            "/Items",
            params={
                "IncludeItemTypes": "Series",
                "Recursive": "true",
                "SearchTerm": show_name,
                "Fields": "Path",
            },
        )
        resp.raise_for_status()
        items = resp.json().get("Items", [])
        if not items:
            return []
        target = _normalize(show_name)
        return [i for i in items if _normalize(i.get("Name", "")) == target] or items

    def _get_episodes(self, series_id: str, season: int) -> list[dict]:
        resp = self._client.get(f"/Shows/{series_id}/Episodes", params={"season": season})
        resp.raise_for_status()
        return resp.json().get("Items", [])
