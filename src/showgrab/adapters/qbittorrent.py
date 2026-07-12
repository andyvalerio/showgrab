"""qBittorrent WebUI API client — implements core.downloader.Downloader
(REQ-SG-016..018).

Credentials are always supplied by the caller (constructor args); this module
never reads env vars or hardcodes anything, so the same class works for any
qBittorrent instance, not just this deployment.
"""

from __future__ import annotations

import httpx


class QbittorrentError(RuntimeError):
    pass


class QbittorrentDownloader:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._username = username
        self._password = password
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self._authed = False

    def add_magnet(self, magnet: str, save_path: str, category: str | None = None) -> None:
        data = {"urls": magnet, "savepath": save_path, "autoTMM": "false"}
        if category:
            data["category"] = category
        self._authed_request("POST", "/api/v2/torrents/add", data=data)

    def delete_with_files(self, infohash: str) -> None:
        self._authed_request(
            "POST",
            "/api/v2/torrents/delete",
            data={"hashes": infohash.lower(), "deleteFiles": "true"},
        )

    def test_connection(self) -> bool:
        resp = self._authed_request("GET", "/api/v2/app/version")
        return resp.status_code == 200

    def _login(self) -> None:
        resp = self._client.post(
            "/api/v2/auth/login",
            data={"username": self._username, "password": self._password},
        )
        resp.raise_for_status()
        if resp.text.strip() != "Ok.":
            raise QbittorrentError(f"qBittorrent login rejected: {resp.text!r}")
        self._authed = True

    def _authed_request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if not self._authed:
            self._login()
        resp = self._client.request(method, path, **kwargs)
        if resp.status_code in (401, 403):
            # Session expired — re-authenticate and retry exactly once, never
            # loop (REQ-SG-018).
            self._authed = False
            self._login()
            resp = self._client.request(method, path, **kwargs)
        resp.raise_for_status()
        return resp
