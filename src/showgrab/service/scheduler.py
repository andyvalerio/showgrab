"""APScheduler wiring: runs a poll function on a cadence (REQ-SG-031) and
stops cleanly on shutdown (REQ-SG-030).

A thread-based BackgroundScheduler, not an asyncio one — every adapter uses
synchronous httpx calls, and running those inside an asyncio event loop
(shared with FastAPI's request handling) would block the whole server for
the duration of each poll. A dedicated scheduler thread keeps poll I/O fully
decoupled from request handling.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

_JOB_ID = "showgrab-poll"


class PollScheduler:
    def __init__(self, poll_fn: Callable[[], object]) -> None:
        self._poll_fn = poll_fn
        self._scheduler = BackgroundScheduler()

    def start(self, interval_minutes: float) -> None:
        self._scheduler.add_job(
            self._poll_fn,
            trigger=IntervalTrigger(minutes=interval_minutes),
            id=_JOB_ID,
            replace_existing=True,
            next_run_time=datetime.now(timezone.utc),  # run once immediately, then on interval
        )
        self._scheduler.start()

    def reschedule(self, interval_minutes: float) -> None:
        """REQ-SG-031: cadence changes take effect without a restart."""
        self._scheduler.reschedule_job(_JOB_ID, trigger=IntervalTrigger(minutes=interval_minutes))

    def trigger_now(self) -> None:
        self._scheduler.modify_job(_JOB_ID, next_run_time=datetime.now(timezone.utc))

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=True)

    @property
    def running(self) -> bool:
        return self._scheduler.running
