"""Builds real adapters from Settings — the single place both the poll loop
and the web UI construct live adapter instances, so there is exactly one
definition of what a Settings object means at the adapter level (and a
settings edit takes effect for both without a restart, since callers rebuild
from fresh settings on every use rather than caching an instance)."""

from __future__ import annotations

from ..adapters.jellyfin import JellyfinLibrary
from ..adapters.notify import SmtpConfig, SmtpNotifier
from ..adapters.qbittorrent import QbittorrentDownloader
from ..adapters.tvmaze import TvMazeMetadata
from ..core import engine
from ..store.settings_store import Settings


def build_jellyfin(settings: Settings) -> JellyfinLibrary:
    return JellyfinLibrary(settings.jellyfin_url, settings.jellyfin_api_key)


def build_tvmaze() -> TvMazeMetadata:
    return TvMazeMetadata()


def build_downloader(settings: Settings) -> QbittorrentDownloader:
    return QbittorrentDownloader(
        settings.qbittorrent_url, settings.qbittorrent_username, settings.qbittorrent_password
    )


def build_notifier(settings: Settings) -> SmtpNotifier | None:
    if not settings.smtp_host:
        return None
    return SmtpNotifier(
        SmtpConfig(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            from_addr=settings.smtp_from,
            to_addrs=settings.smtp_to,
            use_tls=settings.smtp_use_tls,
            use_ssl=settings.smtp_use_ssl,
        )
    )


def build_checks(settings: Settings) -> engine.Checks:
    return engine.Checks(build_jellyfin(settings), build_tvmaze())
