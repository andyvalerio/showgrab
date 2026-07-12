"""Web UI routes: dashboard, settings, activity log, manual episode actions
(REQ-SG-033..040). FastAPI + Jinja2 + HTMX, no JS framework.

Deliberately plain-HTML-forms-first: every action works as a normal
POST-redirect-GET with no JavaScript required (simple to test via
TestClient, robust without a browser), with HTMX layered on top only where
it earns its keep — the settings page's test-connection buttons, which swap
in a small result badge without leaving the form.
"""

from __future__ import annotations

import html
import json
import os

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ...store.settings_store import Settings
from .. import actions
from ..wiring import build_downloader, build_jellyfin

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _escape(text: str) -> str:
    return html.escape(text)


def _ok(message: str) -> HTMLResponse:
    return HTMLResponse(f'<span class="test-ok">{_escape(message)}</span>')


def _fail(message: str) -> HTMLResponse:
    return HTMLResponse(f'<span class="test-fail">Failed: {_escape(message)}</span>')


def build_router(*, settings_store, ledger_store, activity_log, poll_fn) -> APIRouter:
    router = APIRouter()

    # --- dashboard ----------------------------------------------------

    @router.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        entries = sorted(ledger_store.load().all(), key=lambda e: e.first_seen, reverse=True)
        return templates.TemplateResponse(request, "dashboard.html", {"entries": entries})

    @router.post("/poll/run")
    def run_poll_now():
        poll_fn()
        return RedirectResponse("/", status_code=303)

    # --- episode actions (REQ-SG-033..036) -----------------------------

    @router.post("/episode/ignore")
    def episode_ignore(key: str = Form(...)):
        ledger = ledger_store.load()
        if actions.ignore(ledger, _parse_key(key)):
            ledger_store.save(ledger)
        return RedirectResponse("/", status_code=303)

    @router.post("/episode/retry")
    def episode_retry(key: str = Form(...)):
        ledger = ledger_store.load()
        if actions.retry(ledger, _parse_key(key)):
            ledger_store.save(ledger)
        return RedirectResponse("/", status_code=303)

    @router.post("/episode/grab-now")
    def episode_grab_now(key: str = Form(...)):
        settings = settings_store.load()
        ledger = ledger_store.load()
        jellyfin = build_jellyfin(settings)
        downloader = build_downloader(settings)
        if actions.grab_now(ledger, _parse_key(key), settings, downloader, jellyfin):
            ledger_store.save(ledger)
        return RedirectResponse("/", status_code=303)

    @router.post("/episode/swap")
    def episode_swap(key: str = Form(...), infohash: str = Form(...)):
        settings = settings_store.load()
        ledger = ledger_store.load()
        jellyfin = build_jellyfin(settings)
        downloader = build_downloader(settings)
        if actions.manual_swap(ledger, _parse_key(key), infohash, settings, downloader, jellyfin):
            ledger_store.save(ledger)
        return RedirectResponse("/", status_code=303)

    # --- settings (REQ-SG-037, REQ-SG-038) ------------------------------

    @router.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        return templates.TemplateResponse(request, "settings.html", {"settings": settings_store.load()})

    @router.post("/settings")
    async def save_settings(request: Request):
        form = await request.form()
        settings = _settings_from_form(form)
        settings_store.save(settings)
        scheduler = request.app.state.scheduler
        if scheduler.running:
            scheduler.reschedule(settings.poll_interval_minutes)
        return RedirectResponse("/settings", status_code=303)

    @router.post("/settings/test/qbittorrent", response_class=HTMLResponse)
    async def test_qbittorrent(request: Request):
        form = await request.form()
        from ...adapters.qbittorrent import QbittorrentDownloader, QbittorrentError

        try:
            dl = QbittorrentDownloader(
                str(form.get("qbittorrent_url", "")),
                str(form.get("qbittorrent_username", "")),
                str(form.get("qbittorrent_password", "")),
            )
            dl.test_connection()
            return _ok("Connected")
        except Exception as exc:  # noqa: BLE001 — diagnostic UI: show the user what failed, whatever it was
            return _fail(str(exc))

    @router.post("/settings/test/jellyfin", response_class=HTMLResponse)
    async def test_jellyfin(request: Request):
        form = await request.form()
        from ...adapters.jellyfin import JellyfinError, JellyfinLibrary

        try:
            lib = JellyfinLibrary(str(form.get("jellyfin_url", "")), str(form.get("jellyfin_api_key", "")))
            lib.test_connection()
            return _ok("Connected")
        except Exception as exc:  # noqa: BLE001
            return _fail(str(exc))

    @router.post("/settings/test/smtp", response_class=HTMLResponse)
    async def test_smtp(request: Request):
        form = await request.form()
        from ...adapters.notify import SmtpConfig, SmtpError, SmtpNotifier

        try:
            use_ssl = form.get("smtp_tls_mode") == "ssl"
            notifier = SmtpNotifier(
                SmtpConfig(
                    host=str(form.get("smtp_host", "")),
                    port=int(form.get("smtp_port") or 587),
                    username=str(form.get("smtp_username", "")),
                    password=str(form.get("smtp_password", "")),
                    from_addr=str(form.get("smtp_from", "")),
                    to_addrs=[a.strip() for a in str(form.get("smtp_to", "")).split(",") if a.strip()],
                    use_tls=not use_ssl,
                    use_ssl=use_ssl,
                )
            )
            notifier.test_connection()
            return _ok("Connected")
        except Exception as exc:  # noqa: BLE001
            return _fail(str(exc))

    # --- activity log (REQ-SG-040) ---------------------------------------

    @router.get("/activity", response_class=HTMLResponse)
    def activity_page(request: Request):
        return templates.TemplateResponse(request, "activity.html", {"records": activity_log.recent(limit=100)})

    return router


def _parse_key(raw: str) -> tuple:
    return tuple(json.loads(raw))


def _settings_from_form(form) -> Settings:
    def s(name: str, default: str = "") -> str:
        return str(form.get(name, default) or default)

    def f(name: str, default: float) -> float:
        try:
            return float(form.get(name, default))
        except (TypeError, ValueError):
            return default

    def i(name: str, default: int) -> int:
        try:
            return int(form.get(name, default))
        except (TypeError, ValueError):
            return default

    path_mappings: list[tuple[str, str]] = []
    for line in s("path_mappings").splitlines():
        line = line.strip()
        if not line or "->" not in line:
            continue
        frm, to = line.split("->", 1)
        path_mappings.append((frm.strip(), to.strip()))

    tls_mode = s("smtp_tls_mode", "starttls")

    return Settings(
        feed_url=s("feed_url"),
        poll_interval_minutes=f("poll_interval_minutes", 120.0),
        dry_run=form.get("dry_run") == "on",
        preferred_quality=s("preferred_quality", "720p"),
        wait_hours=f("wait_hours", 6.0),
        swap_window_days=f("swap_window_days", 7.0),
        old_cutoff_days=f("old_cutoff_days", 180.0),
        qbittorrent_url=s("qbittorrent_url"),
        qbittorrent_username=s("qbittorrent_username"),
        qbittorrent_password=s("qbittorrent_password"),
        qbittorrent_category=s("qbittorrent_category") or None,
        jellyfin_url=s("jellyfin_url"),
        jellyfin_api_key=s("jellyfin_api_key"),
        smtp_host=s("smtp_host"),
        smtp_port=i("smtp_port", 587),
        smtp_username=s("smtp_username"),
        smtp_password=s("smtp_password"),
        smtp_from=s("smtp_from"),
        smtp_to=[a.strip() for a in s("smtp_to").split(",") if a.strip()],
        smtp_use_tls=tls_mode == "starttls",
        smtp_use_ssl=tls_mode == "ssl",
        tv_root=s("tv_root", "/downloads/tv_series"),
        path_mappings=path_mappings,
    )
