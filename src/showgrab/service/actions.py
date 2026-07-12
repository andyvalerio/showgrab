"""User-initiated manual overrides: ignore, retry, grab now, manual swap
(REQ-SG-033..036).

Distinct from service/poll.py's automatic engine-decided actions — these are
one-off, user-decided, and mutate the ledger directly rather than going
through a poll cycle. They're built on the exact same execute_actions /
resolve_save_path machinery a real poll uses, so a manual action and an
automatic one behave identically against qBittorrent.

Every function here takes the ledger and adapters as plain arguments (no
hidden construction) so they're testable against fakes, matching the DI
pattern used throughout service/. The caller (the web route) is responsible
for loading the ledger, calling one of these, and persisting the result only
when it returns True.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..core import quality as quality_module
from ..core.downloader import Downloader
from ..core.ledger import Ledger
from ..core.models import GrabAction, Status, SwapAction
from ..store.settings_store import Settings
from .executor import execute_actions
from .paths_resolver import resolve_save_path


def ignore(ledger: Ledger, key: tuple) -> bool:
    """REQ-SG-033: terminal, and suppresses any future notify for it — a
    deliberate user decision should never later look like a surprise in a
    digest."""
    entry = ledger.get(key)
    if entry is None:
        return False
    entry.status = Status.IGNORED
    entry.notified = True
    return True


def retry(ledger: Ledger, key: tuple) -> bool:
    """REQ-SG-034: only meaningful for needs-attention; no-op otherwise."""
    entry = ledger.get(key)
    if entry is None or entry.status != Status.NEEDS_ATTENTION:
        return False
    entry.status = Status.DISCOVERED
    entry.notified = False
    return True


def grab_now(
    ledger: Ledger,
    key: tuple,
    settings: Settings,
    downloader: Downloader,
    jellyfin,
) -> bool:
    """REQ-SG-035: only for discovered/waiting with a known variant; picks
    the same best-scoring variant the automatic grab would; only marks
    grabbed if execution actually succeeded."""
    entry = ledger.get(key)
    if entry is None or not entry.variants or entry.status not in (Status.DISCOVERED, Status.WAITING):
        return False

    config = settings.engine_config()
    best = min(
        entry.variants.values(),
        key=lambda v: quality_module.score(v.quality, v.is_remux, config.preferred_quality),
    )
    action = GrabAction(key=entry.key, show_name=entry.show_name, variant=best)

    def on_success() -> None:
        entry.chosen_infohash = best.infohash
        entry.grabbed_at = datetime.now(timezone.utc)
        entry.status = Status.GRABBED

    return _execute_and_apply(action, settings, downloader, jellyfin, on_success)


def manual_swap(
    ledger: Ledger,
    key: tuple,
    infohash: str,
    settings: Settings,
    downloader: Downloader,
    jellyfin,
) -> bool:
    """REQ-SG-036: only for a variant already known on this episode and
    different from the one currently chosen; only applies if execution
    actually succeeded."""
    entry = ledger.get(key)
    if entry is None or infohash not in entry.variants or entry.chosen_infohash == infohash:
        return False

    new_variant = entry.variants[infohash]
    action = SwapAction(
        key=entry.key, show_name=entry.show_name, old_infohash=entry.chosen_infohash, new_variant=new_variant
    )

    def on_success() -> None:
        entry.chosen_infohash = infohash
        entry.status = Status.SWAPPED

    return _execute_and_apply(action, settings, downloader, jellyfin, on_success)


def _execute_and_apply(action, settings: Settings, downloader: Downloader, jellyfin, on_success) -> bool:
    def resolve(show_name: str) -> str:
        return resolve_save_path(show_name, jellyfin, settings.path_mappings, settings.tv_root)

    outcome = execute_actions([action], downloader, resolve, settings.qbittorrent_category)
    if outcome.ok:
        on_success()
        return True
    return False
