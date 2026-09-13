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
    """The downloader doubles as the transfer tracker (REQ-SG-045): the engine
    only ever reads from it through transfer_status(), so this adds no new
    write path and no second qBittorrent session.

    Except in dry-run, which gets no tracker at all (REQ-SG-050). REQ-SG-026
    is absolute — a dry-run poll MUST NOT call the downloader, and
    scripts/live_dry_run_test.py enforces exactly that with a PoisonDownloader
    that raises on any method call, read-only or not. Nothing is lost: dry-run
    never persists the ledger, so a stuck flag raised there could not survive
    to the next poll anyway."""
    transfers = None if settings.dry_run else build_downloader(settings)
    return engine.Checks(build_jellyfin(settings), build_tvmaze(), transfers=transfers)
