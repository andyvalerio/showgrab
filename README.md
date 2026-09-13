# showgrab

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/andyvalerio/showgrab)](https://github.com/andyvalerio/showgrab/releases/latest)

Watch a [showRSS](https://showrss.info) feed and automatically download new TV
episodes through qBittorrent, converging on a **preferred quality** as
releases appear — in either direction. Unlike Sonarr (which only ever
*upgrades*), showgrab also *downgrades*: if you prefer 720p and only a 1080p
release exists yet, it grabs that now and swaps to 720p the moment it shows
up.

It won't re-download old episodes that get republished to the feed — it
checks your media library (Jellyfin) and the episode's real air date (TVmaze)
before ever grabbing anything — and emails a digest whenever something needs
a human to look at it.

Runs as a single small container: no database server, no message queue, just
SQLite and a web UI.

## Features

- **Converging quality** — set a preferred quality once; showgrab grabs
  whatever's available now and swaps up *or* down as better releases appear.
- **Duplicate-safe** — checks Jellyfin before grabbing, so a rescan or a
  republished RSS item never triggers a redundant download.
- **Old-episode filter** — cross-checks TVmaze air dates so old reposts in
  the feed get skipped, not re-grabbed.
- **Download follow-through** — doesn't stop caring once the magnet is handed
  over. Every poll checks the transfer, and an episode that isn't finishing —
  a dead swarm, or a torrent that vanished from qBittorrent — is flagged
  `stuck` and emailed once, then clears itself silently if it recovers.
- **Dry-run mode** — runs the full decision engine against your real feed and
  library, emails you exactly what it *would* do, and touches nothing. Use it
  to sanity-check your config before going live.
- **Web UI** — dashboard, settings, and an activity log with manual
  grab/retry/ignore/swap actions. No YAML file to hand-edit.
- **Every threshold is configuration** — quality, wait times, swap window,
  old-episode cutoff, paths — nothing is hardcoded.

## Quick start (Docker)

```bash
docker run -d \
  --name showgrab \
  -p 8989:8989 \
  -v showgrab-config:/config \
  -e SHOWGRAB_FEED_URL="https://showrss.info/user/<your-id>.rss?magnets=true&namespaces=true" \
  -e SHOWGRAB_QBITTORRENT_URL="http://qbittorrent.local:8080" \
  -e SHOWGRAB_QBITTORRENT_USERNAME="admin" \
  -e SHOWGRAB_QBITTORRENT_PASSWORD="changeme" \
  -e SHOWGRAB_JELLYFIN_URL="http://jellyfin.local:8096" \
  -e SHOWGRAB_JELLYFIN_API_KEY="your-jellyfin-api-key" \
  -e SHOWGRAB_TV_ROOT="/downloads/tv_series" \
  -e SHOWGRAB_DRY_RUN="true" \
  ghcr.io/andyvalerio/showgrab:latest
```

Open `http://localhost:8989`. It starts in **dry-run mode** by default —
watch the Activity tab and your inbox for a poll cycle or two, confirm the
decisions look right, then flip dry-run off from the Settings page (or set
`SHOWGRAB_DRY_RUN=false` before first start).

Everything above is also editable later from the Settings page — env vars
only *seed* the config on first start; once the container has a config file,
the UI is the source of truth.

Prefer Compose? Same env vars under `environment:`, same `/config` volume,
same image.

### Getting a version

Every [release](https://github.com/andyvalerio/showgrab/releases) is a tagged
image on GHCR: `ghcr.io/andyvalerio/showgrab:v0.1.0`, etc. `:latest` always
tracks the newest release. If you deploy with Argo CD, point
[Argo CD Image Updater](https://argocd-image-updater.readthedocs.io/) at
`ghcr.io/andyvalerio/showgrab` with the `semver` update strategy and new
releases roll out on their own — no redeploy step required.

## How it decides

For each episode it sees in the feed:

1. **Already in the library?** → skip, silently.
2. **Aired longer ago than the cutoff (default 180 days)?** → skip, noted in
   the digest email (catches old reposts).
3. **No air date found at all?** → flag for attention, don't download.
4. Otherwise **wait** until either the preferred quality is offered or a wait
   window elapses (default 6 h), then **grab** the release closest to your
   preferred quality (ties break toward the smaller file).
5. For a while after grabbing (default 7 days), if a **better-matching**
   release or a **REPACK** appears, **swap** to it (delete the old torrent +
   files via qBittorrent, add the new one).

## Configuration reference

All configuration is `SHOWGRAB_*` environment variables, seeded into SQLite
once on first start. After that, edits made in the Settings page always win
over the environment.

| Variable | Default | Notes |
|---|---|---|
| `SHOWGRAB_DB_PATH` | `/config/showgrab.db` | Set by the Docker image; rarely needs changing. |
| `SHOWGRAB_PORT` | `8989` | Web UI / `/healthz` port. |
| `SHOWGRAB_FEED_URL` | *(required)* | Your showRSS feed URL. |
| `SHOWGRAB_DRY_RUN` | `true` | Plan and email, but never grab or persist. |
| `SHOWGRAB_POLL_INTERVAL_MINUTES` | `120` | How often the feed is polled. |
| `SHOWGRAB_PREFERRED_QUALITY` | `720p` | The quality showgrab converges toward. |
| `SHOWGRAB_WAIT_HOURS` | `6` | How long to hold out for the preferred quality before grabbing the closest available. |
| `SHOWGRAB_SWAP_WINDOW_DAYS` | `7` | How long after a grab a better release/REPACK can still trigger a swap. |
| `SHOWGRAB_OLD_CUTOFF_DAYS` | `180` | Episodes that aired longer ago than this are skipped, not grabbed. |
| `SHOWGRAB_QBITTORRENT_URL` | *(required)* | qBittorrent WebUI base URL. |
| `SHOWGRAB_QBITTORRENT_USERNAME` | *(required)* | |
| `SHOWGRAB_QBITTORRENT_PASSWORD` | *(required)* | |
| `SHOWGRAB_QBITTORRENT_CATEGORY` | `showgrab` | Category assigned to torrents showgrab adds. |
| `SHOWGRAB_JELLYFIN_URL` | *(required)* | Jellyfin base URL, used for the already-have check. |
| `SHOWGRAB_JELLYFIN_API_KEY` | *(required)* | |
| `SHOWGRAB_SMTP_HOST` | *(optional)* | Leave unset to disable digest emails entirely. |
| `SHOWGRAB_SMTP_PORT` | `587` | |
| `SHOWGRAB_SMTP_USERNAME` | *(optional)* | |
| `SHOWGRAB_SMTP_PASSWORD` | *(optional)* | |
| `SHOWGRAB_SMTP_FROM` | *(optional)* | |
| `SHOWGRAB_SMTP_TO` | *(optional)* | Comma-separated list of recipients. |
| `SHOWGRAB_SMTP_USE_TLS` | `true` | STARTTLS. |
| `SHOWGRAB_SMTP_USE_SSL` | `false` | Implicit TLS (port 465-style); mutually exclusive with STARTTLS. |
| `SHOWGRAB_TV_ROOT` | `/downloads/tv_series` | Where new series folders are created, dot-separated (`Doctor.Who`). |
| `SHOWGRAB_PATH_MAPPINGS` | `[]` | JSON list of `[from, to]` pairs mapping Jellyfin library paths to showgrab's own filesystem view, e.g. `[["/media1", "/downloads/tv_series"]]`. |

Timestamps in the web UI render in your browser's local timezone
automatically — there's no timezone setting.

## The web UI

- **Dashboard** (`/`) — every tracked episode with its status and variants;
  per-episode actions where they apply: **Grab now**, **Retry**
  (needs-attention → discovered), **Swap to** a specific known variant, and
  **Ignore** (permanent, silences future notifications for that episode).
  Stays empty while dry-run is on — see `/activity` and the digest email
  instead.
- **Settings** (`/settings`) — every value above, plus a test-connection
  button per adapter (qBittorrent, Jellyfin, SMTP) that checks the values
  currently in the form, not just what's saved.
- **Activity** (`/activity`) — every poll's result, most recent first, with
  the full digest expandable per row.

## Develop

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest            # run the test suite

# Dry-run the engine over a feed (URL or file), stubbed checks — no adapters:
PYTHONPATH=src .venv/bin/python -m showgrab.cli tests/fixtures/feed_sample.xml --preferred 720p

# Run the real service (reads config from SHOWGRAB_* env on first start,
# persists to SQLite thereafter — see store/settings_store.py for every var):
SHOWGRAB_DB_PATH=/tmp/showgrab.db SHOWGRAB_FEED_URL=... SHOWGRAB_JELLYFIN_URL=... \
  .venv/bin/showgrab-serve   # serves :8989, GET /healthz
```

Manual live-verification scripts (never part of `pytest` — real services,
real credentials via env only, nothing committed):
`scripts/live_smoke_test.py` (each adapter's `test_connection`/one real call)
and `scripts/live_dry_run_test.py` (one full dry-run poll cycle against the
real feed + Jellyfin — the exact safety mechanism the production rollout
depends on, provable end-to-end with zero side effects).

## Architecture

```
src/showgrab/
  core/        decision engine — pure logic, no I/O (quality, release, engine, ledger,
               models, downloader protocol, digest formatting, save-path resolution)
  adapters/    edges that touch the outside world — feed parser, qBittorrent
               (Downloader), Jellyfin (LibraryChecker + series path lookup), TVmaze
               (MetadataResolver), SMTP digest notifier. Never hardcode credentials.
  store/       SQLite persistence — settings (env-seeded once), ledger, activity log
  service/     the runnable service
    wiring.py    builds real adapters from Settings — one definition shared by the
                 poll loop and the web UI, rebuilt fresh from current settings
                 every time (a settings-page edit takes effect without a restart)
    poll.py      the automatic per-poll cycle (dry-run/persistence-safety rules)
    actions.py   user-initiated manual overrides (ignore/retry/grab-now/swap) —
                 same execute_actions machinery poll.py uses, one-off instead
    executor.py  wires engine (or manual) actions into real Downloader calls
    scheduler.py, app.py   APScheduler + FastAPI (/healthz), showgrab-serve entrypoint
    web/         dashboard, settings, activity log — FastAPI + Jinja2 + HTMX
  cli.py       dry-run entry point (stubbed checks; separate from the real service)
docs/requirements.md   REQ-SG-* catalog; every test header cites the IDs it verifies
tests/                 unit tests — adapters against httpx.MockTransport / fake SMTP;
                        store against in-memory SQLite; service layer proves the
                        dry-run/execution-failure persistence rules against real stores;
                        web routes driven through FastAPI's TestClient
```

Each adapter takes its config/credentials via constructor args — nothing is
read from the environment or hardcoded, so the same code works for any
deployment (the `service/` layer is what wires env/DB-sourced config in):

- `adapters.qbittorrent.QbittorrentDownloader(base_url, username, password)`
- `adapters.jellyfin.JellyfinLibrary(base_url, api_key)` — also resolves a
  series' real library folder, filtering out mis-registered single-episode
  folders (see `core.paths.looks_like_release_folder`)
- `adapters.tvmaze.TvMazeMetadata()` — free, keyless
- `adapters.notify.SmtpNotifier(SmtpConfig(...))` — any SMTP account (STARTTLS
  or implicit TLS)

`showgrab-serve` polls on a schedule (rescheduled at runtime when settings
change), runs the engine, and either:

- **dry-run**: runs the full engine against the real feed and real
  Jellyfin/TVmaze, emails what it *would* do, but never calls qBittorrent and
  never persists anything — the next poll re-derives the identical decision
  from scratch.
- **live**: executes grabs/swaps against qBittorrent, and persists the
  ledger only if every action in that poll succeeded — a partial failure
  persists nothing and retries cleanly next poll (safe because both planning
  and the qBittorrent add/delete calls are idempotent).

## License

[MIT](LICENSE)
