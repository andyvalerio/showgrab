"""Quality tiers, ranking, and preference scoring.

Tiers: SD (480p) < 720p < 1080p < 2160p. A REMUX release ranks a half-step
above its plain resolution — higher quality, bigger file — but still below the
next tier up (REQ-SG-005).
"""

from __future__ import annotations

from enum import IntEnum

REMUX_STEP = 0.5


class Quality(IntEnum):
    SD = 1
    HD720 = 2
    HD1080 = 3
    UHD2160 = 4

    @property
    def label(self) -> str:
        return {1: "SD", 2: "720p", 3: "1080p", 4: "2160p"}[self.value]


_BY_LABEL = {
    "sd": Quality.SD,
    "480p": Quality.SD,
    "720p": Quality.HD720,
    "1080p": Quality.HD1080,
    "2160p": Quality.UHD2160,
    "4k": Quality.UHD2160,
    "uhd": Quality.UHD2160,
}


def from_label(text: str) -> Quality:
    """Parse a user-facing label like '720p' into a Quality (for config/CLI)."""
    key = text.strip().lower()
    if key not in _BY_LABEL:
        raise ValueError(f"unknown quality {text!r}; expected one of SD/720p/1080p/2160p")
    return _BY_LABEL[key]


def rank(quality: Quality, is_remux: bool = False) -> float:
    """Sortable quality rank; REMUX sits a half-step above its plain tier."""
    return float(quality.value) + (REMUX_STEP if is_remux else 0.0)


def distance(quality: Quality, is_remux: bool, preferred: Quality) -> float:
    """How far a variant is from the preferred tier; smaller is a better match."""
    return abs(rank(quality, is_remux) - float(preferred.value))


def score(quality: Quality, is_remux: bool, preferred: Quality) -> tuple[float, float]:
    """Selection sort key: minimize distance to preferred, then minimize rank so
    ties break toward the lower tier (REQ-SG-005, REQ-SG-006). Smaller is better.
    """
    return (distance(quality, is_remux, preferred), rank(quality, is_remux))
