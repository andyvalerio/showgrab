"""SQLAlchemy ORM tables.

All datetimes are stored as explicit ISO-8601 strings (not SQLAlchemy's
native DateTime type) — SQLite's driver-level timezone handling is
inconsistent across configurations, and the core engine relies on every
datetime being timezone-aware. Storing/parsing ISO strings ourselves removes
that ambiguity entirely rather than trusting an unverified assumption about
driver behavior (see the qBittorrent adapter's phase-2 postmortem for why
that trust has to be earned, not assumed).
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class SettingsRow(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    feed_url: Mapped[str] = mapped_column(String, default="")
    poll_interval_minutes: Mapped[float] = mapped_column(Float, default=120.0)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)

    preferred_quality: Mapped[str] = mapped_column(String, default="720p")
    wait_hours: Mapped[float] = mapped_column(Float, default=6.0)
    swap_window_days: Mapped[float] = mapped_column(Float, default=7.0)
    old_cutoff_days: Mapped[float] = mapped_column(Float, default=180.0)

    qbittorrent_url: Mapped[str] = mapped_column(String, default="")
    qbittorrent_username: Mapped[str] = mapped_column(String, default="")
    qbittorrent_password: Mapped[str] = mapped_column(String, default="")
    qbittorrent_category: Mapped[str | None] = mapped_column(String, nullable=True, default="showgrab")

    jellyfin_url: Mapped[str] = mapped_column(String, default="")
    jellyfin_api_key: Mapped[str] = mapped_column(String, default="")

    smtp_host: Mapped[str] = mapped_column(String, default="")
    smtp_port: Mapped[int] = mapped_column(Integer, default=587)
    smtp_username: Mapped[str] = mapped_column(String, default="")
    smtp_password: Mapped[str] = mapped_column(String, default="")
    smtp_from: Mapped[str] = mapped_column(String, default="")
    smtp_to: Mapped[str] = mapped_column(String, default="")  # comma-separated
    smtp_use_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    smtp_use_ssl: Mapped[bool] = mapped_column(Boolean, default=False)

    tv_root: Mapped[str] = mapped_column(String, default="/downloads/tv_series")
    path_mappings_json: Mapped[str] = mapped_column(String, default="[]")  # [[from, to], ...]


class LedgerEntryRow(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key_json: Mapped[str] = mapped_column(String, unique=True, index=True)
    show_id: Mapped[str] = mapped_column(String)
    show_name: Mapped[str] = mapped_column(String)
    external_id: Mapped[str | None] = mapped_column(String, nullable=True)
    first_seen: Mapped[str] = mapped_column(String)  # ISO-8601
    status: Mapped[str] = mapped_column(String)
    chosen_infohash: Mapped[str | None] = mapped_column(String, nullable=True)
    grabbed_at: Mapped[str | None] = mapped_column(String, nullable=True)  # ISO-8601
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    variants_json: Mapped[str] = mapped_column(String, default="{}")
    # --- transfer follow-up (REQ-SG-045..048). Added after the first release,
    # so each carries a server_default for the additive migration in db.py to
    # apply to rows that already exist (REQ-SG-049).
    stuck_notified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), nullable=False
    )
    pre_stuck_status: Mapped[str | None] = mapped_column(String, nullable=True)
    download_progress: Mapped[float | None] = mapped_column(Float, nullable=True)


class ActivityLogRow(Base):
    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[str] = mapped_column(String)  # ISO-8601
    dry_run: Mapped[bool] = mapped_column(Boolean)
    ok: Mapped[bool] = mapped_column(Boolean)
    summary: Mapped[str] = mapped_column(String)
    details_json: Mapped[str] = mapped_column(String, default="{}")
