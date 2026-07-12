# Verifies: REQ-SG-029 (GET /healthz returns 200 with no external dependency
#   check), REQ-SG-030 (the scheduler starts on app startup and stops
#   cleanly on shutdown, via FastAPI's lifespan).
# Scenario: build the app with a fake poll_fn (no real adapters), drive it
#   through FastAPI's TestClient (which runs the lifespan context manager)
#   with and without the scheduler enabled.

from fastapi.testclient import TestClient

from showgrab.service.app import create_app


def test_healthz_ok_without_scheduler():
    app = create_app(poll_fn=lambda: None, poll_interval_minutes=30, run_scheduler=False)
    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["scheduler_running"] is None
        assert body["started_at"] is not None


def test_healthz_reports_scheduler_running():
    app = create_app(poll_fn=lambda: None, poll_interval_minutes=60, run_scheduler=True)
    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.json()["scheduler_running"] is True


def test_scheduler_stops_after_app_context_exits():
    app = create_app(poll_fn=lambda: None, poll_interval_minutes=60, run_scheduler=True)
    with TestClient(app):
        assert app.state.scheduler.running is True
    assert app.state.scheduler.running is False
