"""FastAPI app: GET /healthz (REQ-SG-029) plus scheduler lifecycle
(REQ-SG-030, REQ-SG-031)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Callable

from fastapi import FastAPI

from .scheduler import PollScheduler


def create_app(
    *,
    poll_fn: Callable[[], object],
    poll_interval_minutes: float,
    run_scheduler: bool = True,
) -> FastAPI:
    scheduler = PollScheduler(poll_fn)
    state: dict = {"started_at": None}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state["started_at"] = datetime.now(timezone.utc)
        if run_scheduler:
            scheduler.start(poll_interval_minutes)
        yield
        if run_scheduler:
            scheduler.stop()

    app = FastAPI(lifespan=lifespan)
    app.state.scheduler = scheduler

    @app.get("/healthz")
    def healthz() -> dict:
        # REQ-SG-029: liveness only — no external service reachability check.
        return {
            "status": "ok",
            "started_at": state["started_at"].isoformat() if state["started_at"] else None,
            "scheduler_running": scheduler.running if run_scheduler else None,
        }

    return app
