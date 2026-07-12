# showgrab

Watch a [showRSS](https://showrss.info) feed and download new TV episodes
through qBittorrent at a **preferred quality** — converging on that quality as
releases appear, in either direction. Unlike Sonarr (which only ever *upgrades*
quality), showgrab will also *downgrade*: if you prefer 720p and only a 1080p
release exists yet, it grabs that and swaps to the 720p when it shows up.

It also refuses to re-download old episodes that get republished to the feed,
checking the media library (Jellyfin) and the episode's air date (TVmaze)
before grabbing, and emails a digest when something needs a human.

> **Status: early development.** Phase 1 (the headless decision engine) and
> phase 2 (real adapters: qBittorrent, Jellyfin, TVmaze, SMTP) are implemented
> and tested. The scheduler loop, web UI, and packaging are next — see `docs/`
> and the architecture doc. Adapters aren't wired into the CLI yet (still
> dry-run only, stubbed checks) — that wiring is phase 3.

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

# Dry-run the engine over a feed (URL or file) — prints decisions, downloads nothing:
PYTHONPATH=src .venv/bin/python -m showgrab.cli tests/fixtures/feed_sample.xml --preferred 720p
```

## Layout

```
src/showgrab/
  core/        decision engine — pure logic, no I/O (quality, release, engine, ledger,
               models, downloader protocol, digest formatting)
  adapters/    edges that touch the outside world — feed parser, qBittorrent
               (Downloader), Jellyfin (LibraryChecker), TVmaze (MetadataResolver),
               SMTP digest notifier. Never hardcode credentials — always config-in.
  store/       persistence (SQLite — to come)
  cli.py       dry-run entry point (stubbed checks; adapters not wired in yet)
docs/requirements.md   REQ-SG-* catalog; every test header cites the IDs it verifies
tests/                 unit tests, adapters tested against httpx.MockTransport /
                        fake SMTP doubles (integration-against-real-service + e2e layers
                        to come in later phases)
```

## Adapters (phase 2)

Each adapter takes its config/credentials via constructor args — nothing is
read from the environment or hardcoded, so the same code works for any
deployment:

- `adapters.qbittorrent.QbittorrentDownloader(base_url, username, password)`
- `adapters.jellyfin.JellyfinLibrary(base_url, api_key)`
- `adapters.tvmaze.TvMazeMetadata()` — free, keyless
- `adapters.notify.SmtpNotifier(SmtpConfig(...))` — any SMTP account (STARTTLS
  or implicit TLS)

Every adapter has a `test_connection()` method for a later settings-page
"test connection" button.

## License

TBD before the repo is made public.
