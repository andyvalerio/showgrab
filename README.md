# showgrab

Watch a [showRSS](https://showrss.info) feed and download new TV episodes
through qBittorrent at a **preferred quality** — converging on that quality as
releases appear, in either direction. Unlike Sonarr (which only ever *upgrades*
quality), showgrab will also *downgrade*: if you prefer 720p and only a 1080p
release exists yet, it grabs that and swaps to the 720p when it shows up.

It also refuses to re-download old episodes that get republished to the feed,
checking the media library (Jellyfin) and the episode's air date (TVmaze)
before grabbing, and emails a digest when something needs a human.

> **Status: early development.** Phase 1 (the headless decision engine) is
> implemented and tested. Adapters, scheduler, web UI, and packaging are on the
> roadmap — see `docs/` and the architecture doc.

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
  core/        decision engine — pure logic, no I/O (quality, release, engine, ledger, models)
  adapters/    edges that touch the outside world (feed parser; qB/Jellyfin/TVmaze/SMTP to come)
  store/       persistence (SQLite — to come)
  cli.py       dry-run entry point
docs/requirements.md   REQ-SG-* catalog; every test header cites the IDs it verifies
tests/                 unit tests (integration + e2e layers to come)
```

## License

TBD before the repo is made public.
