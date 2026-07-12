"""TVmaze API client — implements core.ledger.MetadataResolver (REQ-SG-020).

Free, keyless API. The feed's tv:external_id is the TVmaze show id directly
(verified against the live showRSS feed), so no separate show-search step is
needed when it's present.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

_DEFAULT_BASE_URL = "https://api.tvmaze.com"


class TvMazeError(RuntimeError):
    pass


class TvMazeMetadata:
    def __init__(
        self,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def airdate(
        self,
        external_id: str | None,
        show_name: str,
        season: int,
        episode: int,
    ) -> datetime | None:
        if not external_id:
            return None  # no TVmaze id to look up — REQ-SG-020, no network call

        try:
            resp = self._client.get(
                f"/shows/{external_id}/episodebynumber",
                params={"season": season, "number": episode},
            )
        except httpx.HTTPError as exc:
            raise TvMazeError(f"TVmaze lookup failed for show {external_id}: {exc}") from exc

        if resp.status_code == 404:
            return None  # definitively not in TVmaze's catalog — not a transient failure

        try:
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise TvMazeError(f"TVmaze lookup failed for show {external_id}: {exc}") from exc

        return _parse_airdate(resp.json())

    def test_connection(self) -> bool:
        try:
            resp = self._client.get("/shows/1")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise TvMazeError(f"TVmaze connection test failed: {exc}") from exc
        return True


def _parse_airdate(data: dict) -> datetime | None:
    airstamp = data.get("airstamp")
    if airstamp:
        try:
            return datetime.fromisoformat(airstamp.replace("Z", "+00:00"))
        except ValueError:
            pass
    airdate = data.get("airdate")
    if airdate:
        try:
            return datetime.strptime(airdate, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None
