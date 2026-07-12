"""FastAPI app: GET /healthz (REQ-SG-029), scheduler lifecycle (REQ-SG-030,
REQ-SG-031), and — when stores are provided — the web UI (phase 4,
REQ-SG-033..040)."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Callable

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .scheduler import PollScheduler


def create_app(
    *,
    poll_fn: Callable[[], object],
    poll_interval_minutes: float,
    run_scheduler: bool = True,
    settings_store=None,
    ledger_store=None,
    activity_log=None,
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

    # Web UI is optional: existing callers (tests, a headless deployment)
    # that only need /healthz keep working exactly as before by omitting
    # the store arguments.
    if settings_store is not None and ledger_store is not None and activity_log is not None:
        from .web.routes import build_router

        app.include_router(
            build_router(
                settings_store=settings_store,
                ledger_store=ledger_store,
                activity_log=activity_log,
                poll_fn=poll_fn,
            )
        )
        static_dir = os.path.join(os.path.dirname(__file__), "web", "static")
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    return app
