"""The decision engine — pure logic over the ledger.

``plan()`` folds the current feed into the ledger and returns the actions to
perform (grab / swap) plus notify events. Every state transition is guarded by
the entry's status, so re-running a plan over the same feed is a no-op
(idempotent — REQ-SG-014).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import quality
from .ledger import Ledger, LibraryChecker, MetadataResolver
from .models import (
    Config,
    GrabAction,
    LedgerEntry,
    NotifyEvent,
    PlanResult,
    Status,
    SwapAction,
    TERMINAL,
    Variant,
)
from .release import Release


class Checks:
    """Bundle of the two external checks the engine depends on."""

    def __init__(self, library: LibraryChecker, metadata: MetadataResolver) -> None:
        self.library = library
        self.metadata = metadata


def plan(
    ledger: Ledger,
    releases: list[Release],
    now: datetime,
    config: Config,
    checks: Checks,
) -> PlanResult:
    _ingest(ledger, releases, now)
    result = PlanResult()
    for entry in ledger.all():
        actions, events = _decide(entry, now, config, checks)
        result.actions.extend(actions)
        result.events.extend(events)
    return result


def _ingest(ledger: Ledger, releases: list[Release], now: datetime) -> None:
    """Fold releases into ledger entries. Parsable releases become variants;
    releases with no episode identity become orphan needs-attention entries."""
    for r in releases:
        item = r.item
        if r.episode_key is None:
            key = ("_noepisode", item.show_id, item.episode_id)
            if ledger.get(key) is None:
                ledger.put(
                    LedgerEntry(
                        key=key,
                        show_id=item.show_id,
                        show_name=item.show_name,
                        external_id=item.external_id,
                        first_seen=now,
                        status=Status.NEEDS_ATTENTION,
                    )
                )
            continue

        entry = ledger.get(r.episode_key)
        if entry is None:
            entry = LedgerEntry(
                key=r.episode_key,
                show_id=item.show_id,
                show_name=item.show_name,
                external_id=item.external_id,
                first_seen=now,
            )
            ledger.put(entry)

        if r.quality is not None:
            # Same infohash re-seen overwrites with identical data — idempotent.
            entry.variants[item.infohash] = Variant(
                quality=r.quality,
                is_remux=r.is_remux,
                is_repack=r.is_repack,
                infohash=item.infohash,
                magnet=item.magnet,
                pub_date=item.pub_date,
                episode_id=item.episode_id,
            )


def _decide(
    entry: LedgerEntry,
    now: datetime,
    config: Config,
    checks: Checks,
) -> tuple[list, list]:
    if entry.status in TERMINAL:
        # Emit the one-time notify for skip/attention outcomes if still pending.
        return [], _notify_once(entry)

    if entry.status == Status.DISCOVERED:
        events = _run_gates(entry, now, config, checks)
        if entry.status != Status.WAITING:
            return [], events  # gate ended in a terminal state
        # else fall through into the waiting logic this same pass

    if entry.status == Status.WAITING:
        return _try_grab(entry, now, config)

    if entry.status in (Status.GRABBED, Status.SWAPPED):
        return _try_swap(entry, now, config)

    return [], []


def _run_gates(entry: LedgerEntry, now: datetime, config: Config, checks: Checks) -> list:
    """First-sight gates: already-have, too-old, no-metadata (REQ-SG-008..010)."""
    season, episode = entry.season, entry.episode
    if season is None or episode is None:  # defensive; orphans start terminal
        entry.status = Status.NEEDS_ATTENTION
        return _notify_once(entry)

    if checks.library.has_episode(
        entry.show_id, entry.external_id, entry.show_name, season, episode
    ):
        entry.status = Status.SKIPPED_HAVE
        return []  # silent by design

    airdate = checks.metadata.airdate(entry.external_id, entry.show_name, season, episode)
    if airdate is None:
        entry.status = Status.NEEDS_ATTENTION
        return _notify_once(entry, detail="no metadata match for air date")

    if airdate < now - timedelta(days=config.old_cutoff_days):
        entry.status = Status.SKIPPED_OLD
        return _notify_once(entry, detail=f"aired {airdate.date()} (older than cutoff)")

    entry.status = Status.WAITING
    return []


def _try_grab(entry: LedgerEntry, now: datetime, config: Config) -> tuple[list, list]:
    best = _best(entry, config)
    if best is None:
        # No parsable variant yet. If we have waited long enough with nothing to
        # grab, flag it rather than waiting forever (REQ-SG-011 boundary).
        if now - entry.first_seen >= timedelta(hours=config.wait_hours):
            entry.status = Status.NEEDS_ATTENTION
            return [], _notify_once(entry, detail="no usable-quality release appeared")
        return [], []

    preferred_offered = any(
        v.quality == config.preferred_quality for v in entry.variants.values()
    )
    waited = now - entry.first_seen >= timedelta(hours=config.wait_hours)
    if not (preferred_offered or waited):
        return [], []  # keep holding out for the preferred quality

    entry.chosen_infohash = best.infohash
    entry.grabbed_at = now
    entry.status = Status.GRABBED
    action = GrabAction(key=entry.key, show_name=entry.show_name, variant=best)
    event = NotifyEvent(
        kind="grabbed",
        key=entry.key,
        show_name=entry.show_name,
        detail=f"{_ep(entry)} {best.quality.label}",
    )
    return [action], [event]


def _try_swap(entry: LedgerEntry, now: datetime, config: Config) -> tuple[list, list]:
    if entry.grabbed_at is None or now - entry.grabbed_at > timedelta(
        days=config.swap_window_days
    ):
        entry.status = Status.SETTLED
        return [], []

    chosen = entry.chosen
    if chosen is None:
        entry.status = Status.SETTLED
        return [], []

    replacement = _swap_target(entry, chosen, config)
    if replacement is None:
        return [], []

    old = entry.chosen_infohash
    entry.chosen_infohash = replacement.infohash
    entry.status = Status.SWAPPED
    action = SwapAction(
        key=entry.key,
        show_name=entry.show_name,
        old_infohash=old,
        new_variant=replacement,
    )
    detail = f"{_ep(entry)} {chosen.quality.label} → {replacement.quality.label}"
    if replacement.is_repack and replacement.quality == chosen.quality:
        detail = f"{_ep(entry)} {chosen.quality.label} → REPACK"
    event = NotifyEvent(kind="swapped", key=entry.key, show_name=entry.show_name, detail=detail)
    return [action], [event]


def _swap_target(entry: LedgerEntry, chosen: Variant, config: Config) -> Variant | None:
    """A strictly better-scoring variant, or a REPACK of the chosen tier
    (REQ-SG-012, REQ-SG-013). Returns None when nothing warrants a swap."""
    best = _best(entry, config)
    if best is not None and best.infohash != chosen.infohash:
        chosen_key = quality.score(chosen.quality, chosen.is_remux, config.preferred_quality)
        best_key = quality.score(best.quality, best.is_remux, config.preferred_quality)
        if best_key < chosen_key:
            return best

    if not chosen.is_repack:
        for v in entry.variants.values():
            if v.is_repack and v.quality == chosen.quality and v.infohash != chosen.infohash:
                return v
    return None


def _best(entry: LedgerEntry, config: Config) -> Variant | None:
    variants = list(entry.variants.values())
    if not variants:
        return None
    return min(
        variants,
        key=lambda v: quality.score(v.quality, v.is_remux, config.preferred_quality),
    )


def _notify_once(entry: LedgerEntry, detail: str = "") -> list:
    """Emit a single notify event for a skip/attention outcome, at most once
    per entry over its whole lifetime (REQ-SG-009/010/014)."""
    if entry.notified:
        return []
    entry.notified = True
    return [
        NotifyEvent(
            kind=entry.status.value,
            key=entry.key,
            show_name=entry.show_name,
            detail=detail or _ep(entry),
        )
    ]


def _ep(entry: LedgerEntry) -> str:
    if entry.is_episode:
        return f"S{entry.season:02d}E{entry.episode:02d}"
    return str(entry.key)
