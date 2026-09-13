"""The decision engine — pure logic over the ledger.

``plan()`` folds the current feed into the ledger and returns the actions to
perform (grab / swap) plus notify events. Every state transition is guarded by
the entry's status, so re-running a plan over the same feed is a no-op
(idempotent — REQ-SG-014).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import quality
from .downloader import TransferStatus
from .ledger import Ledger, LibraryChecker, MetadataResolver
from .models import (
    Config,
    GrabAction,
    IN_FLIGHT,
    LedgerEntry,
    NOTIFIABLE,
    NotifyEvent,
    PlanResult,
    Status,
    SwapAction,
    TERMINAL,
    Variant,
)
from .release import Release


class Checks:
    """Bundle of the external checks the engine depends on.

    `transfers` is optional so a caller with no download client to consult
    (the --dry-run CLI, most engine unit tests) simply skips transfer
    follow-up rather than having to supply a stub."""

    def __init__(
        self,
        library: LibraryChecker,
        metadata: MetadataResolver,
        transfers: object | None = None,
    ) -> None:
        self.library = library
        self.metadata = metadata
        self.transfers = transfers


def plan(
    ledger: Ledger,
    releases: list[Release],
    now: datetime,
    config: Config,
    checks: Checks,
) -> PlanResult:
    _ingest(ledger, releases, now)
    transfers = _fetch_transfers(ledger, checks)
    result = PlanResult()
    for entry in ledger.all():
        actions, events = _decide(entry, now, config, checks, transfers)
        result.actions.extend(actions)
        result.events.extend(events)
    return result


def _fetch_transfers(ledger: Ledger, checks: Checks) -> dict[str, TransferStatus] | None:
    """One batched transfer lookup per poll (REQ-SG-043).

    Returns None to mean "no opinion this poll" — no tracker configured, or
    the lookup raised. That is distinct from an empty dict, which is a
    SUCCESSFUL lookup that found none of the hashes and therefore does say
    something (REQ-SG-047). Only None suppresses the follow-up checks, so a
    qBittorrent blip can never manufacture a stuck alarm (REQ-SG-048)."""
    tracker = getattr(checks, "transfers", None)
    if tracker is None:
        return None
    hashes = [
        e.chosen_infohash for e in ledger.all() if e.status in IN_FLIGHT and e.chosen_infohash
    ]
    if not hashes:
        return {}
    try:
        return tracker.transfer_status(hashes)
    except Exception:
        return None


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
    transfers: dict[str, TransferStatus] | None = None,
) -> tuple[list, list]:
    if entry.status in TERMINAL:
        # Emit the one-time notify for skip/attention outcomes if still
        # pending — orphan entries enter NEEDS_ATTENTION at ingest without
        # going through a path that notifies, so this is where they report.
        # Only NOTIFIABLE statuses may do so: every terminal entry is revisited
        # on every poll, so notifying on all of TERMINAL emailed a `settled`
        # line for each episode a week after its grab, and a `skipped-have`
        # line for each episode already in the library (REQ-SG-008/012/021).
        if entry.status in NOTIFIABLE:
            return [], _notify_once(entry)
        return [], []

    if entry.status == Status.DISCOVERED:
        events = _run_gates(entry, now, config, checks)
        if entry.status != Status.WAITING:
            return [], events  # gate ended in a terminal state
        # else fall through into the waiting logic this same pass

    if entry.status == Status.WAITING:
        return _try_grab(entry, now, config)

    if entry.status in IN_FLIGHT:
        # Follow up on the transfer first, then run the unchanged swap/settle
        # logic — which still owns the status when the swap window closes, so
        # settling stays exactly as time-based as it has always been.
        events = _check_transfer(entry, transfers)
        actions, swap_events = _try_swap(entry, now, config)
        return actions, events + swap_events

    return [], []


def _run_gates(entry: LedgerEntry, now: datetime, config: Config, checks: Checks) -> list:
    """First-sight gates: already-have, too-old, no-metadata (REQ-SG-008..010)."""
    season, episode = entry.season, entry.episode
    if season is None or episode is None:  # defensive; orphans start terminal
        entry.status = Status.NEEDS_ATTENTION
        return _notify_once(entry)

    try:
        have = checks.library.has_episode(
            entry.show_id, entry.external_id, entry.show_name, season, episode
        )
    except Exception:
        # Transient adapter failure (network blip, Jellyfin restarting, ...).
        # Leave the entry DISCOVERED so the next poll retries the gates from
        # scratch, rather than misclassifying it or crashing the whole plan()
        # over one flaky check (REQ-SG-023).
        return []
    if have:
        entry.status = Status.SKIPPED_HAVE
        return []  # silent by design

    try:
        airdate = checks.metadata.airdate(entry.external_id, entry.show_name, season, episode)
    except Exception:
        return []  # same as above (REQ-SG-023)
    if airdate is None:
        entry.status = Status.NEEDS_ATTENTION
        return _notify_once(entry, detail=f"{_ep(entry)}: no metadata match for air date")

    if airdate < now - timedelta(days=config.old_cutoff_days):
        entry.status = Status.SKIPPED_OLD
        return _notify_once(entry, detail=f"{_ep(entry)} aired {airdate.date()} (older than cutoff)")

    entry.status = Status.WAITING
    return []


def _try_grab(entry: LedgerEntry, now: datetime, config: Config) -> tuple[list, list]:
    best = _best(entry, config)
    if best is None:
        # No parsable variant yet. If we have waited long enough with nothing to
        # grab, flag it rather than waiting forever (REQ-SG-011 boundary).
        if now - entry.first_seen >= timedelta(hours=config.wait_hours):
            entry.status = Status.NEEDS_ATTENTION
            return [], _notify_once(entry, detail=f"{_ep(entry)}: no usable-quality release appeared")
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
    _reset_transfer_state(entry)
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
    # A swap is a brand-new download, so it gets a fresh stuck budget even if
    # the variant it replaces had already alarmed (REQ-SG-046).
    _reset_transfer_state(entry)
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


def _reset_transfer_state(entry: LedgerEntry) -> None:
    """Called whenever a new magnet is handed to the downloader."""
    entry.stuck_notified = False
    entry.pre_stuck_status = None
    entry.download_progress = None


def _check_transfer(entry: LedgerEntry, transfers: dict[str, TransferStatus] | None) -> list:
    """Follow up on an in-flight transfer (REQ-SG-045..048).

    Runs only on polls AFTER the one that issued the grab: the grab path
    returns early from _decide, and the magnet doesn't reach qBittorrent until
    the executor runs after planning — so the first time an entry gets here,
    its torrent has had a full poll interval to finish."""
    if transfers is None:
        return []  # lookup unavailable this poll — no opinion, no alarm
    infohash = entry.chosen_infohash
    if infohash is None:
        return []

    status = transfers.get(infohash.lower())
    if status is not None and status.complete:
        entry.download_progress = 1.0
        if entry.status is Status.STUCK:
            # Recovered on its own. Silent by design: a finished download is
            # not news, and the alarm that preceded it already went out.
            entry.status = entry.pre_stuck_status or Status.GRABBED
            entry.pre_stuck_status = None
        return []

    if status is None:
        # Present in our ledger, absent from a SUCCESSFUL lookup: qBittorrent
        # no longer has it (deleted by hand, or the add never really took).
        detail = f"{_ep(entry)}: no longer in qBittorrent"
    else:
        entry.download_progress = status.progress
        detail = f"{_ep(entry)} stalled at {status.progress * 100:.0f}% ({status.state})"

    if entry.status is not Status.STUCK:
        entry.pre_stuck_status = entry.status
        entry.status = Status.STUCK
    if entry.stuck_notified:
        return []  # one alarm per download attempt, not one per poll
    entry.stuck_notified = True
    return [NotifyEvent(kind="stuck", key=entry.key, show_name=entry.show_name, detail=detail)]


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
