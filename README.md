# showgrab

Watch a [showRSS](https://showrss.info) feed and download new TV episodes
through qBittorrent at a **preferred quality** — converging on that quality as
releases appear, in either direction. Unlike Sonarr (which only ever *upgrades*
quality), showgrab will also *downgrade*: if you prefer 720p and only a 1080p
release exists yet, it grabs that and swaps to the 720p when it shows up.

It also refuses to re-download old episodes that get republished to the feed,
checking the media library (Jellyfin) and the episode's air date (TVmaze)
before grabbing, and emails a digest when something needs a human.

> **Status: early development.** Phase 1 (headless decision engine), phase 2
> (real adapters), and phase 3 (persistent service: scheduler, SQLite-backed
> settings/ledger, dry-run mode, `/healthz`) are implemented and tested. The
> web UI and packaging are next — see `docs/` and the architecture doc.
> `showgrab-serve` runs the real service; `showgrab.cli` remains a
> stub-checks dry-run tool for eyeballing the engine against a feed.

## How it decides

For each episode it sees in the feed:

1. **Already in the library?** → skip, silently.
2. **Aired longer ago than the cutoff (default 180 days)?** → skip, note it in
   the digest email (catches old reposts).
3. **No air date found at all?** → flag for attention, don't download.
4. Otherwise **wait** until either the preferred quality is offered or a wait
   window elapses (default 6 h), then **grab** the release closest to your
   preferred quality (ties break toward the smaller file).
5. For a while after grabbing (default 7 days), if a **better-matching** release
   or a **REPACK** appears, **swap** to it (delete the old torrent + files via
   qBittorrent, add the new one).

Every threshold is configuration.

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

## Layout

```
src/showgrab/
  core/        decision engine — pure logic, no I/O (quality, release, engine, ledger,
               models, downloader protocol, digest formatting, save-path resolution)
  adapters/    edges that touch the outside world — feed parser, qBittorrent
               (Downloader), Jellyfin (LibraryChecker + series path lookup), TVmaze
               (MetadataResolver), SMTP digest notifier. Never hardcode credentials.
  store/       SQLite persistence — settings (env-seeded once), ledger, activity log
  service/     the runnable service — poll orchestration (dry-run/persistence-safety
               rules), execution against the downloader, APScheduler wiring,
               FastAPI app (/healthz), the showgrab-serve entrypoint
  cli.py       dry-run entry point (stubbed checks; separate from the real service)
docs/requirements.md   REQ-SG-* catalog; every test header cites the IDs it verifies
tests/                 unit tests — adapters against httpx.MockTransport / fake SMTP;
                        store against in-memory SQLite; service layer proves the
                        dry-run/execution-failure persistence rules against real stores
```

## Adapters

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

Every adapter has a `test_connection()` method for a later settings-page
"test connection" button.

## The service (phase 3)

`showgrab-serve` polls on a schedule (`SHOWGRAB_POLL_INTERVAL_MINUTES`,
rescheduled at runtime when settings change), runs the engine, and either:

- **dry-run** (`SHOWGRAB_DRY_RUN=true`, the safe default): runs the full
  engine against the real feed and real Jellyfin/TVmaze, emails what it
  *would* do, but never calls qBittorrent and never persists anything — the
  next poll re-derives the identical decision from scratch. This is the
  mechanism a production rollout uses to verify behavior against the real
  feed before going live.
- **live**: executes grabs/swaps against qBittorrent, and persists the
  ledger only if every action in that poll succeeded — a partial failure
  persists nothing and retries cleanly next poll (safe because both
  planning and the qBittorrent add/delete calls are idempotent).

Settings live in SQLite, seeded once from `SHOWGRAB_*` env vars on first
start; edits after that always win over env. See
`store/settings_store.py` for the full list of variables.

## License

TBD before the repo is made public.
