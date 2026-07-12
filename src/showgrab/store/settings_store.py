"""Persistent settings: single-row table, seeded once from env (REQ-SG-024).

Every value a user might reasonably want to change is here — nothing an
adapter needs is hardcoded anywhere else in the codebase.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from ..core.models import Config
from ..core.quality import from_label
from .models import SettingsRow

_SETTINGS_ROW_ID = 1


@dataclass
class Settings:
    feed_url: str
    poll_interval_minutes: float
    dry_run: bool

    preferred_quality: str
    wait_hours: float
    swap_window_days: float
    old_cutoff_days: float

    qbittorrent_url: str
    qbittorrent_username: str
    qbittorrent_password: str
    qbittorrent_category: str | None

    jellyfin_url: str
    jellyfin_api_key: str

    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from: str
    smtp_to: list[str]
    smtp_use_tls: bool
    smtp_use_ssl: bool

    tv_root: str
    path_mappings: list[tuple[str, str]]

    def engine_config(self) -> Config:
        return Config(
            preferred_quality=from_label(self.preferred_quality),
            wait_hours=self.wait_hours,
            swap_window_days=self.swap_window_days,
            old_cutoff_days=self.old_cutoff_days,
        )


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_bool(name: str, default: str) -> bool:
    return _env(name, default).strip().lower() in ("1", "true", "yes", "on")


def _seed_row_from_env() -> SettingsRow:
    return SettingsRow(
        id=_SETTINGS_ROW_ID,
        feed_url=_env("SHOWGRAB_FEED_URL", ""),
        poll_interval_minutes=float(_env("SHOWGRAB_POLL_INTERVAL_MINUTES", "30")),
        dry_run=_env_bool("SHOWGRAB_DRY_RUN", "true"),
        preferred_quality=_env("SHOWGRAB_PREFERRED_QUALITY", "720p"),
        wait_hours=float(_env("SHOWGRAB_WAIT_HOURS", "6")),
        swap_window_days=float(_env("SHOWGRAB_SWAP_WINDOW_DAYS", "7")),
        old_cutoff_days=float(_env("SHOWGRAB_OLD_CUTOFF_DAYS", "180")),
        qbittorrent_url=_env("SHOWGRAB_QBITTORRENT_URL", ""),
        qbittorrent_username=_env("SHOWGRAB_QBITTORRENT_USERNAME", ""),
        qbittorrent_password=_env("SHOWGRAB_QBITTORRENT_PASSWORD", ""),
        qbittorrent_category=_env("SHOWGRAB_QBITTORRENT_CATEGORY", "showgrab") or None,
        jellyfin_url=_env("SHOWGRAB_JELLYFIN_URL", ""),
        jellyfin_api_key=_env("SHOWGRAB_JELLYFIN_API_KEY", ""),
        smtp_host=_env("SHOWGRAB_SMTP_HOST", ""),
        smtp_port=int(_env("SHOWGRAB_SMTP_PORT", "587")),
        smtp_username=_env("SHOWGRAB_SMTP_USERNAME", ""),
        smtp_password=_env("SHOWGRAB_SMTP_PASSWORD", ""),
        smtp_from=_env("SHOWGRAB_SMTP_FROM", ""),
        smtp_to=_env("SHOWGRAB_SMTP_TO", ""),
        smtp_use_tls=_env_bool("SHOWGRAB_SMTP_USE_TLS", "true"),
        smtp_use_ssl=_env_bool("SHOWGRAB_SMTP_USE_SSL", "false"),
        tv_root=_env("SHOWGRAB_TV_ROOT", "/downloads/tv_series"),
        path_mappings_json=_env("SHOWGRAB_PATH_MAPPINGS", "[]"),
    )


def _row_to_settings(row: SettingsRow) -> Settings:
    return Settings(
        feed_url=row.feed_url,
        poll_interval_minutes=row.poll_interval_minutes,
        dry_run=row.dry_run,
        preferred_quality=row.preferred_quality,
        wait_hours=row.wait_hours,
        swap_window_days=row.swap_window_days,
        old_cutoff_days=row.old_cutoff_days,
        qbittorrent_url=row.qbittorrent_url,
        qbittorrent_username=row.qbittorrent_username,
        qbittorrent_password=row.qbittorrent_password,
        qbittorrent_category=row.qbittorrent_category,
        jellyfin_url=row.jellyfin_url,
        jellyfin_api_key=row.jellyfin_api_key,
        smtp_host=row.smtp_host,
        smtp_port=row.smtp_port,
        smtp_username=row.smtp_username,
        smtp_password=row.smtp_password,
        smtp_from=row.smtp_from,
        smtp_to=[a.strip() for a in row.smtp_to.split(",") if a.strip()],
        smtp_use_tls=row.smtp_use_tls,
        smtp_use_ssl=row.smtp_use_ssl,
        tv_root=row.tv_root,
        path_mappings=[tuple(pair) for pair in json.loads(row.path_mappings_json)],
    )


def _apply_settings_to_row(row: SettingsRow, settings: Settings) -> None:
    row.feed_url = settings.feed_url
    row.poll_interval_minutes = settings.poll_interval_minutes
    row.dry_run = settings.dry_run
    row.preferred_quality = settings.preferred_quality
    row.wait_hours = settings.wait_hours
    row.swap_window_days = settings.swap_window_days
    row.old_cutoff_days = settings.old_cutoff_days
    row.qbittorrent_url = settings.qbittorrent_url
    row.qbittorrent_username = settings.qbittorrent_username
    row.qbittorrent_password = settings.qbittorrent_password
    row.qbittorrent_category = settings.qbittorrent_category
    row.jellyfin_url = settings.jellyfin_url
    row.jellyfin_api_key = settings.jellyfin_api_key
    row.smtp_host = settings.smtp_host
    row.smtp_port = settings.smtp_port
    row.smtp_username = settings.smtp_username
    row.smtp_password = settings.smtp_password
    row.smtp_from = settings.smtp_from
    row.smtp_to = ",".join(settings.smtp_to)
    row.smtp_use_tls = settings.smtp_use_tls
    row.smtp_use_ssl = settings.smtp_use_ssl
    row.tv_root = settings.tv_root
    row.path_mappings_json = json.dumps([list(pair) for pair in settings.path_mappings])


class SettingsStore:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def load(self) -> Settings:
        """Reads the DB row; seeds it from env once if the table is empty
        (REQ-SG-024). Never re-seeds over an existing row."""
        with self._session_factory() as session:
            row = session.get(SettingsRow, _SETTINGS_ROW_ID)
            if row is None:
                row = _seed_row_from_env()
                session.add(row)
                session.commit()
            return _row_to_settings(row)

    def save(self, settings: Settings) -> None:
        with self._session_factory() as session:
            row = session.get(SettingsRow, _SETTINGS_ROW_ID)
            if row is None:
                row = SettingsRow(id=_SETTINGS_ROW_ID)
                session.add(row)
            _apply_settings_to_row(row, settings)
            session.commit()
