"""Domain model for the decision engine: config, ledger, actions, events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .quality import Quality


class Status(str, Enum):
    DISCOVERED = "discovered"
    WAITING = "waiting"
    GRABBED = "grabbed"
    SWAPPED = "swapped"
    SETTLED = "settled"
    SKIPPED_HAVE = "skipped-have"
    SKIPPED_OLD = "skipped-old"
    NEEDS_ATTENTION = "needs-attention"


#: Statuses that never produce further download actions.
TERMINAL = {Status.SETTLED, Status.SKIPPED_HAVE, Status.SKIPPED_OLD, Status.NEEDS_ATTENTION}


@dataclass
class Config:
    """User-tunable thresholds (all editable in the UI in later phases)."""

    preferred_quality: Quality = Quality.HD720
    wait_hours: float = 6.0
    swap_window_days: float = 7.0
    old_cutoff_days: float = 180.0


@dataclass(frozen=True)
class Variant:
    """One downloadable release of an episode, keyed in the ledger by infohash."""

    quality: Quality
    is_remux: bool
    is_repack: bool
    infohash: str
    magnet: str
    pub_date: datetime
    episode_id: str


@dataclass
class LedgerEntry:
    """Everything known about one episode across all its variants."""

    key: tuple
    show_id: str
    show_name: str
    external_id: str | None
    first_seen: datetime
    variants: dict[str, Variant] = field(default_factory=dict)  # by infohash
    status: Status = Status.DISCOVERED
    chosen_infohash: str | None = None
    grabbed_at: datetime | None = None
    notified: bool = False

    @property
    def is_episode(self) -> bool:
        """False for orphan entries that had no parsable SxxEyy."""
        return len(self.key) == 3 and isinstance(self.key[2], int)

    @property
    def season(self) -> int | None:
        return self.key[1] if self.is_episode else None

    @property
    def episode(self) -> int | None:
        return self.key[2] if self.is_episode else None

    @property
    def chosen(self) -> Variant | None:
        if self.chosen_infohash is None:
            return None
        return self.variants.get(self.chosen_infohash)


# --- actions the engine asks the downstream (qBittorrent) to perform ---


@dataclass(frozen=True)
class GrabAction:
    key: tuple
    show_name: str
    variant: Variant


@dataclass(frozen=True)
class SwapAction:
    key: tuple
    show_name: str
    old_infohash: str
    new_variant: Variant


# --- notify events (fed to the digest email / activity log) ---


@dataclass(frozen=True)
class NotifyEvent:
    kind: str  # grabbed | swapped | skipped-old | needs-attention
    key: tuple
    show_name: str
    detail: str


@dataclass
class PlanResult:
    actions: list = field(default_factory=list)
    events: list = field(default_factory=list)
