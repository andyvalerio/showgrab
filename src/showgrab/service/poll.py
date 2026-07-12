"""The poll cycle: fetch -> plan -> (execute or dry-run) -> persist -> notify
-> log. Where every phase-1/2 piece and the phase-3 persistence/dry-run rules
(REQ-SG-024..032) come together.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from ..adapters.jellyfin import JellyfinLibrary
from ..core import engine
from ..core.digest import build_digest
from ..core.downloader import Downloader
from ..core.models import NotifyEvent
from ..core.release import Release
from ..store.activity_store import ActivityLogStore
from ..store.ledger_store import LedgerStore
from ..store.settings_store import Settings
from .executor import execute_actions
from .paths_resolver import resolve_save_path


@dataclass
class PollOutcome:
    dry_run: bool
    episode_count: int
    grabbed: int
    swapped: int
    execution_ok: bool
    events: list = field(default_factory=list)


def run_poll(
    *,
    settings: Settings,
    ledger_store: LedgerStore,
    activity_log: ActivityLogStore,
    fetch_releases: Callable[[str], list[Release]],
    checks: engine.Checks,
    downloader: Downloader,
    jellyfin: JellyfinLibrary,
    notifier=None,
    now: datetime,
) -> PollOutcome:
    """Never raises (REQ-SG-032) — any unhandled failure is caught, logged,
    best-effort notified, and turned into a failed-but-quiet PollOutcome so
    the scheduler always lives to run the next poll."""
    try:
        return _run_poll_inner(
            settings=settings,
            ledger_store=ledger_store,
            activity_log=activity_log,
            fetch_releases=fetch_releases,
            checks=checks,
            downloader=downloader,
            jellyfin=jellyfin,
            notifier=notifier,
            now=now,
        )
    except Exception as exc:
        _safe_log_failure(activity_log, now, settings.dry_run, exc)
        _safe_notify_failure(notifier, settings.dry_run, exc)
        return PollOutcome(
            dry_run=settings.dry_run, episode_count=0, grabbed=0, swapped=0, execution_ok=False
        )


def _run_poll_inner(
    *,
    settings: Settings,
    ledger_store: LedgerStore,
    activity_log: ActivityLogStore,
    fetch_releases: Callable[[str], list[Release]],
    checks: engine.Checks,
    downloader: Downloader,
    jellyfin: JellyfinLibrary,
    notifier,
    now: datetime,
) -> PollOutcome:
    config = settings.engine_config()
    ledger = ledger_store.load()
    releases = fetch_releases(settings.feed_url)
    result = engine.plan(ledger, releases, now, config, checks)

    execution_ok = True
    events = list(result.events)

    if settings.dry_run:
        # REQ-SG-026: full engine run, but never touch the downloader and
        # never persist — next poll must see the exact same starting state.
        pass
    else:
        def resolve(show_name: str) -> str:
            return resolve_save_path(show_name, jellyfin, settings.path_mappings, settings.tv_root)

        outcome = execute_actions(result.actions, downloader, resolve, settings.qbittorrent_category)
        execution_ok = outcome.ok
        if not execution_ok:
            detail = "; ".join(f"{type(f.action).__name__}: {f.error}" for f in outcome.failed)
            events.append(
                NotifyEvent(kind="execution-error", key=(), show_name="showgrab", detail=detail)
            )
        else:
            # REQ-SG-027: persist only on full success; on failure, nothing
            # from this poll is saved and the next poll retries from
            # last-known-good state (safe: planning and the qBittorrent
            # add/delete calls are both idempotent).
            ledger_store.save(ledger)

    digest = build_digest(events, dry_run=settings.dry_run)
    digest_body = digest[1] if digest else None
    previous = activity_log.recent(limit=1)
    previous_digest_body = previous[0].details.get("digest_body") if previous else None
    emailed = False
    if digest is not None and _should_send_digest(settings.dry_run, digest_body, previous_digest_body):
        if notifier is not None:
            notifier.send_digest(events, dry_run=settings.dry_run)
            emailed = True

    grabbed = sum(1 for e in events if e.kind == "grabbed")
    swapped = sum(1 for e in events if e.kind == "swapped")
    summary = (
        f"{len(releases)} feed items, {len(ledger.all())} episodes tracked, "
        f"{grabbed} grabbed, {swapped} swapped"
    )
    if not execution_ok:
        summary += " — execution FAILED, nothing persisted this poll"
    activity_log.record(
        now,
        dry_run=settings.dry_run,
        ok=execution_ok,
        summary=summary,
        details={
            "grabbed": grabbed,
            "swapped": swapped,
            "events": len(events),
            "digest_body": digest_body,
            "emailed": emailed,
        },
    )

    return PollOutcome(
        dry_run=settings.dry_run,
        episode_count=len(ledger.all()),
        grabbed=grabbed,
        swapped=swapped,
        execution_ok=execution_ok,
        events=events,
    )


def _should_send_digest(dry_run: bool, current_body: str | None, previous_body: str | None) -> bool:
    """Only called when there IS a digest to consider (build_digest already
    returned None, and nothing gets sent, for a poll with zero events — that
    gate is identical in both modes and happens before this function is even
    reached). This function decides whether that non-empty digest should be
    suppressed as a duplicate.

    Live mode never suppresses: grabbed/swapped events already can't repeat
    poll-to-poll (ledger persistence means a settled episode stops producing
    events at all, not that it repeats one), so this rule only ever bites on
    an execution-error digest repeating identically — a real, ongoing
    failure that must keep alerting, never go silent.

    Dry-run mode never persists (REQ-SG-026 — that's what makes it safe to
    run before going live), so the exact same non-empty digest would
    otherwise be re-derived, and re-sent, every single poll forever. Skip
    sending when it's unchanged since the immediately preceding poll; the
    activity log still records every poll regardless (REQ-SG-041)."""
    if not dry_run:
        return True
    return current_body != previous_body


def _safe_log_failure(activity_log: ActivityLogStore, now: datetime, dry_run: bool, exc: Exception) -> None:
    try:
        activity_log.record(
            now, dry_run=dry_run, ok=False, summary=f"poll failed: {exc}", details={"error": str(exc)}
        )
    except Exception:
        pass  # even logging failed; nothing more we can safely do


def _safe_notify_failure(notifier, dry_run: bool, exc: Exception) -> None:
    if notifier is None:
        return
    try:
        notifier.send_digest(
            [NotifyEvent(kind="poll-error", key=(), show_name="showgrab", detail=str(exc))],
            dry_run=dry_run,
        )
    except Exception:
        pass
