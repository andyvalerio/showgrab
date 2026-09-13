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
    STUCK = "stuck"  # magnet handed over, but the transfer isn't finishing
    SETTLED = "settled"
    SKIPPED_HAVE = "skipped-have"
    SKIPPED_OLD = "skipped-old"
    NEEDS_ATTENTION = "needs-attention"
    IGNORED = "ignored"  # user-initiated (web UI), never set by the engine itself


#: Statuses that never produce further download actions.
TERMINAL = {
    Status.SETTLED,
    Status.SKIPPED_HAVE,
    Status.SKIPPED_OLD,
    Status.NEEDS_ATTENTION,
    Status.IGNORED,
}

#: Statuses where a magnet is in qBittorrent's hands and its transfer is worth
#: following up on (REQ-SG-045). STUCK is deliberately in here and NOT in
#: TERMINAL: a stalled transfer that recovers must be able to leave the state.
IN_FLIGHT = {
    Status.GRABBED,
    Status.SWAPPED,
    Status.STUCK,
}

#: Terminal statuses that warrant exactly one notify event when an entry
#: reaches them. Deliberately narrower than TERMINAL: reaching a terminal
#: state stops downloads, which is not the same as being worth an email.
#: The other three are silent by design — `skipped-have` (REQ-SG-008),
#: `settled` (REQ-SG-012: settling is a state change, nothing happened to
#: report), and `ignored` (the user chose it). REQ-SG-021 fixes the digest's
#: kinds to exactly the four that correspond to these two plus grabbed/swapped.
NOTIFIABLE = {
    Status.SKIPPED_OLD,
    Status.NEEDS_ATTENTION,
}


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
    # --- transfer follow-up (REQ-SG-045..048) ---
    #: One stuck notify per download attempt; reset when a new magnet is issued.
    stuck_notified: bool = False
    #: What to restore on recovery, so a stuck swap doesn't come back as a grab.
    pre_stuck_status: Status | None = None
    #: Last observed transfer progress, 0.0-1.0, for the digest and dashboard.
    download_progress: float | None = None

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
