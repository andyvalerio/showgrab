"""Jellyfin API client — implements core.ledger.LibraryChecker (REQ-SG-019).

Credentials (API key) are always supplied by the caller; see
core/ledger.py:LibraryChecker for the interface this satisfies.
"""

from __future__ import annotations

import re

import httpx

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
            series_id = self._find_series_id(show_name)
            if series_id is None:
                return False
            episodes = self._get_episodes(series_id, season)
        except httpx.HTTPError as exc:
            # Never silently return False on a transport failure — that would
            # look identical to "genuinely not in the library" to the caller.
            # The engine's gate handling (REQ-SG-023) is what decides to retry.
            raise JellyfinError(f"Jellyfin lookup failed for {show_name!r}: {exc}") from exc

        return any(
            ep.get("IndexNumber") == episode and ep.get("LocationType") == "FileSystem"
            for ep in episodes
        )

    def test_connection(self) -> bool:
        try:
            resp = self._client.get("/System/Info")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise JellyfinError(f"Jellyfin connection test failed: {exc}") from exc
        return True

    def _find_series_id(self, show_name: str) -> str | None:
        resp = self._client.get(
            "/Items",
            params={"IncludeItemTypes": "Series", "Recursive": "true", "SearchTerm": show_name},
        )
        resp.raise_for_status()
        items = resp.json().get("Items", [])
        target = _normalize(show_name)
        for item in items:
            if _normalize(item.get("Name", "")) == target:
                return item.get("Id")
        # Loose fallback: best search hit, in case of a near-miss title.
        return items[0].get("Id") if items else None

    def _get_episodes(self, series_id: str, season: int) -> list[dict]:
        resp = self._client.get(f"/Shows/{series_id}/Episodes", params={"season": season})
        resp.raise_for_status()
        return resp.json().get("Items", [])
