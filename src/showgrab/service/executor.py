"""Executes engine actions against a real Downloader (wires REQ-SG-016/017
into the decision engine's output).

All-or-nothing per poll by design: every action is attempted (a failure on
one doesn't skip the rest — earlier successes this poll are real qBittorrent
side effects and should happen regardless of what fails later), but the
caller (service/poll.py) decides whether to persist the ledger based on
whether *any* action failed (REQ-SG-027).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..core.downloader import Downloader
from ..core.models import GrabAction, SwapAction


@dataclass
class ActionFailure:
    action: object
    error: str


@dataclass
class ExecutionOutcome:
    succeeded: list = field(default_factory=list)
    failed: list[ActionFailure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


def execute_actions(
    actions: list,
    downloader: Downloader,
    resolve_save_path: Callable[[str], str],
    category: str | None = None,
) -> ExecutionOutcome:
    outcome = ExecutionOutcome()
    for action in actions:
        try:
            if isinstance(action, GrabAction):
                save_path = resolve_save_path(action.show_name)
                downloader.add_magnet(action.variant.magnet, save_path, category=category)
            elif isinstance(action, SwapAction):
                downloader.delete_with_files(action.old_infohash)
                save_path = resolve_save_path(action.show_name)
                downloader.add_magnet(action.new_variant.magnet, save_path, category=category)
            else:
                raise TypeError(f"unknown action type: {type(action)!r}")
        except Exception as exc:
            outcome.failed.append(ActionFailure(action=action, error=str(exc)))
        else:
            outcome.succeeded.append(action)
    return outcome
