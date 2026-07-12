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
        resp = self._authed_request("POST", "/api/v2/torrents/add", data=data)
        _check_add_result(resp)

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
        if resp.status_code in (401, 403):
            # Confirmed against a real qBittorrent 5.2.3: bad credentials get
            # a 401, not the old-API "200 + body 'Fails.'" contract.
            raise QbittorrentError(f"qBittorrent login rejected: {resp.text.strip() or resp.status_code}")
        resp.raise_for_status()
        if resp.text.strip() == "Fails.":
            # Older qBittorrent (<= v4.x) signals failure via 200 + "Fails.".
            raise QbittorrentError("qBittorrent login rejected: invalid credentials")
        # Success: v5.x returns 204 No Content (empty body); older versions
        # return 200 + "Ok.". Either way, no exception above means we're in.
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


def _check_add_result(resp: httpx.Response) -> None:
    """qBittorrent 5.x's torrents/add returns 200 with a JSON summary even for
    a per-item failure (confirmed live: a 409 covers outright rejection, but
    a 200 with failure_count>0 is also possible); older versions return plain
    200 "Ok." text with nothing to inspect. Only raise when a JSON body is
    present and reports a failure — the legacy text contract has no signal to
    check beyond the 2xx status already validated by the caller."""
    try:
        data = resp.json()
    except ValueError:
        return
    if isinstance(data, dict) and data.get("failure_count", 0):
        raise QbittorrentError(f"qBittorrent rejected the magnet: {data}")
