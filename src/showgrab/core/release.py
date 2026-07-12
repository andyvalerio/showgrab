"""Parse a feed item's raw title into structured episode + quality info.

The show identity comes from the feed's ``tv:show_id`` (authoritative); the
title only supplies the season/episode numbers and the quality/flags. This is
the edge-case-heavy part of the engine — see the REQ-SG-001..004 tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from .quality import Quality

_SXXEYY = re.compile(r"S(\d{1,2})E(\d{1,2})", re.IGNORECASE)

# Highest tier first; a title normally carries exactly one resolution token.
_QUALITY_TOKENS: list[tuple[re.Pattern[str], Quality]] = [
    (re.compile(r"\b2160p\b|\b4k\b|\buhd\b", re.IGNORECASE), Quality.UHD2160),
    (re.compile(r"\b1080p\b", re.IGNORECASE), Quality.HD1080),
    (re.compile(r"\b720p\b", re.IGNORECASE), Quality.HD720),
    (re.compile(r"\b480p\b|\bsd\b", re.IGNORECASE), Quality.SD),
]

_REMUX = re.compile(r"\bREMUX\b", re.IGNORECASE)
_REPACK = re.compile(r"\bREPACK\b", re.IGNORECASE)
# A standalone 1900–2099 year, but not a resolution: the negative lookahead
# rejects a trailing 'p' (e.g. 1080p), and word boundaries keep it from biting
# into a longer digit run like 2160.
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b(?!p)", re.IGNORECASE)


@dataclass(frozen=True)
class FeedItem:
    """One ``<item>`` from the feed, before title parsing."""

    show_id: str
    show_name: str
    episode_id: str
    raw_title: str
    magnet: str
    infohash: str
    pub_date: datetime
    external_id: str | None = None


@dataclass(frozen=True)
class Release:
    """A FeedItem parsed into episode identity + quality (REQ-SG-001..004)."""

    item: FeedItem
    season: int | None
    episode: int | None
    quality: Quality | None
    is_remux: bool
    is_repack: bool
    year: int | None

    @property
    def episode_key(self) -> tuple[str, int, int] | None:
        if self.season is None or self.episode is None:
            return None
        return (self.item.show_id, self.season, self.episode)

    @property
    def parsable(self) -> bool:
        """True when we have both an episode identity and a usable quality."""
        return self.episode_key is not None and self.quality is not None


def parse_release(item: FeedItem) -> Release:
    title = item.raw_title or ""

    m = _SXXEYY.search(title)
    season = int(m.group(1)) if m else None
    episode = int(m.group(2)) if m else None

    quality: Quality | None = None
    for pattern, tier in _QUALITY_TOKENS:
        if pattern.search(title):
            quality = tier
            break

    ym = _YEAR.search(title)
    year = int(ym.group(1)) if ym else None

    return Release(
        item=item,
        season=season,
        episode=episode,
        quality=quality,
        is_remux=bool(_REMUX.search(title)),
        is_repack=bool(_REPACK.search(title)),
        year=year,
    )
