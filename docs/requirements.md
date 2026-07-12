# showgrab requirements (REQ-SG-*)

Every feature is traceable to a requirement here, and every test file header
names the REQ-SG IDs it verifies (see the testing mandate in the architecture
doc). IDs are stable once assigned; new work appends.

Quality tiers referenced below: `SD (480p) < 720p < 1080p < 2160p`. A `REMUX`
release ranks a half-step **above** its plain resolution.

## Phase 1 — core engine (headless)

### Release title parsing

- **REQ-SG-001** — MUST identify an episode from a feed item as
  `(show_id, season, episode)`, taking `show_id` from the feed's `tv:show_id`
  and `season`/`episode` from the `SxxEyy` token in the raw title
  (case-insensitive, 1–2 digits each).
- **REQ-SG-002** — MUST parse the quality tier from the raw title
  (`2160p`/`4K`/`UHD` → 2160; `1080p` → 1080; `720p` → 720; `480p`/`SD` → SD)
  and MUST flag `REMUX` and `REPACK` tokens (case-insensitive), independently
  of the tier.
- **REQ-SG-003** — MUST extract an embedded 4-digit year (e.g. "Avatar The
  Last Airbender **2024** S01E01") when present, without mistaking a resolution
  (e.g. `2160p`) for a year. The year is informational (remake disambiguation).
- **REQ-SG-004** — When no quality tier can be parsed, the release MUST be
  marked unparsable (tier = none) and MUST NOT be assigned a guessed tier.

### Quality ranking & preference scoring

- **REQ-SG-005** — MUST rank releases by distance from a configured
  **preferred quality**, breaking ties toward the **lower** tier. A plain
  release at tier T MUST rank closer to preferred=T than a `REMUX` at tier T.
- **REQ-SG-006** — Given a set of variants for one episode, MUST select the
  best variant as the minimum by `(distance_to_preferred, rank)`.

### Feed parsing

- **REQ-SG-007** — MUST parse the showRSS namespaced RSS into feed items,
  extracting `tv:show_id`, `tv:external_id` (TVmaze id, may be absent),
  `tv:show_name`, `tv:episode_id`, `tv:raw_title`, the magnet link, and
  `pubDate`. Malformed/incomplete items MUST be skipped, not crash the parse.

### Decision engine

- **REQ-SG-008** — On first sight of an episode, if the media-library check
  reports it already present, the episode MUST be marked `skipped-have` and
  produce no download.
- **REQ-SG-009** — On first sight, if the episode's air date is older than the
  configured `old_cutoff` (default 180 days), it MUST be marked `skipped-old`
  and MUST emit exactly one notify event.
- **REQ-SG-010** — On first sight, if no air date can be resolved (no metadata
  match), the episode MUST be marked `needs-attention` and emit one notify
  event; it MUST NOT be downloaded.
- **REQ-SG-011** — An episode past the gates MUST wait until either the
  preferred quality is offered or `wait_hours` (default 6 h) has elapsed since
  first sight, then grab the best-scoring available variant (one grab action).
- **REQ-SG-012** — Within `swap_window` (default 7 days) of the grab, if a
  strictly better-scoring variant appears, the engine MUST emit a swap action
  (delete-with-files old + add new) and a notify event; after the window the
  episode settles and no longer swaps.
- **REQ-SG-013** — A `REPACK` of the currently-chosen tier within the swap
  window MUST trigger a replace even when the tier is unchanged.
- **REQ-SG-014** — All decisions MUST be idempotent against the ledger:
  re-running a poll over the same feed items produces no duplicate actions or
  duplicate notify events.

### CLI

- **REQ-SG-015** — MUST provide a `--dry-run` CLI that parses a feed (URL or
  file) and prints the decision for each episode without performing downloads.

## Phase 2 — adapters

Credentials (qBittorrent user/pass, Jellyfin API key, SMTP user/pass) are
always supplied by the caller via config objects/env — no adapter ever
hardcodes or guesses a secret.

### qBittorrent (Downloader)

- **REQ-SG-016** — MUST authenticate against the qBittorrent WebUI API
  (cookie-based session) and add a magnet with an explicit save path (and
  optional category), with `autoTMM` disabled so the save path is honored.
  MUST accept both the legacy plain-text contract (`200` + `"Ok."`/`"Fails."`)
  and the modern one (`204` empty body on success, `401` on failure,
  `torrents/add` returning `200` with a JSON summary that can itself report
  `failure_count > 0`) — confirmed by live-testing against a real qBittorrent
  5.2.3 instance, which uses the modern contract exclusively; docs/tutorials
  describing the old contract are stale for current versions.
- **REQ-SG-017** — MUST delete a torrent **with its downloaded files** given
  an infohash (the only deletion mode this adapter exposes — showgrab never
  deletes torrents without files, since a stale torrent-only delete would
  leave an orphaned file the swap logic doesn't know about).
- **REQ-SG-018** — On a session expiry (401/403 on an authenticated request)
  MUST re-authenticate and retry the request **exactly once**; a second
  failure MUST raise, never loop.

### Jellyfin (MediaServer / LibraryChecker)

- **REQ-SG-019** — `has_episode` MUST match the series by name (matching
  case/whitespace-insensitively and ignoring a trailing `(YYYY)` disambiguator
  on either side, since showRSS and Jellyfin don't always agree on including
  it) and MUST count an episode as present only when Jellyfin reports it as an
  actual file (`LocationType: FileSystem`), not a virtual/placeholder entry
  for an unaired or missing episode.
- Transport/API failures MUST raise a typed error (`JellyfinError`), never
  silently return `False` — see REQ-SG-023, which governs how the engine
  treats that raise.

### TVmaze (MetadataResolver)

- **REQ-SG-020** — `airdate` MUST return `None` without making a network call
  when `external_id` is absent, MUST return `None` on a 404 (episode not in
  TVmaze's catalog for that show/season/number), and MUST raise a typed error
  (`TvMazeError`) on any other transport/HTTP failure — never silently return
  `None` for a transient failure (that would permanently misclassify the
  episode; see REQ-SG-023).

### Notifications (digest email)

- **REQ-SG-021** — Building a digest from a list of notify events MUST group
  them by kind (grabbed / swapped / skipped-old / needs-attention) and MUST
  produce no digest (no email sent) when the event list is empty.
- **REQ-SG-022** — The SMTP notifier MUST support both STARTTLS (explicit TLS
  on a plaintext-then-upgrade port, e.g. Gmail's 587) and implicit TLS/SSL
  (e.g. port 465), selected by config — not hardcoded to one provider's setup,
  since this is meant to work for any SMTP account after open-sourcing.

### Gate resilience

Real adapters are fallible (network blips, a restarting Jellyfin pod); the
engine's first-sight gates must not let that turn into silent data loss.

- **REQ-SG-023** — If the library check or the metadata check raises during
  the first-sight gates, the episode MUST remain (or return to) `discovered`,
  MUST NOT emit a notify event, and MUST NOT be misclassified as
  skipped/needs-attention; the same episode is re-evaluated on the next poll
  (bounded retry via the natural poll cadence, no in-process retry loop, no
  swallowing a transient failure into a wrong permanent state).

## Phase 3 — service-ification

Turns the headless engine + adapters into a runnable, persistent service: a
scheduler that polls on a cadence, settings and the ledger surviving process
restarts, real execution against qBittorrent, and a health endpoint.

### Persistence

- **REQ-SG-024** — Settings MUST persist in SQLite and seed once from
  environment variables into an empty settings table; on every subsequent
  start the DB values MUST win (env is a first-run seed, never an override
  of a user's saved edits).
- **REQ-SG-025** — The ledger MUST persist across process restarts
  (SQLite-backed) preserving every field the engine depends on (status, all
  variants, chosen infohash, `notified`, timestamps), with no change to the
  idempotency guarantees already proven for the in-memory ledger
  (REQ-SG-014): loading, planning, and saving back must round-trip losslessly.

### Save-path resolution

- **REQ-SG-028** — Resolving where a grab is saved MUST prefer the media
  server's existing **whole-series** folder for that series (via Jellyfin),
  translated through user-configured path mappings; when no such folder
  exists yet, MUST fall back to a sanitized folder name under a configured
  TV root. Path mappings and the TV root are settings, not hardcoded — this
  must work for any installation's mount layout, not just this deployment's.
  New folder names MUST follow this library's existing dot-separated
  convention (e.g. "American Dad!" -> `American.Dad`, "Doctor Who" ->
  `Doctor.Who`) — punctuation stripped, words joined by dots.
  - **Live finding (2026-07-12):** a real, messy library was found where a
    flat one-file-per-folder TV layout makes Jellyfin register individual
    episode release folders as their own bogus `Series` entries alongside
    (or instead of) the genuine series folder — "American Dad!" had six
    same-named `Series` entries, five really a single mis-parsed
    episode-release folder and only one the real series directory; "Doctor
    Who" had *only* mis-parsed release folders, no genuine series folder at
    all. Episode-count-based disambiguation does not work here: Jellyfin
    groups `/Shows/{id}/Episodes` by the show's underlying metadata
    identity, not by the specific folder item, so every duplicate — junk or
    genuine — reports the identical full episode list.
  - **Rule (Andy's correction, 2026-07-12):** judge purely by the
    **candidate folder's name**, never by querying its contents. A folder
    name carrying a release's own markers (an `SxxEyy` code, a resolution
    like `1080p`, an encode tag like `x265`/`HEVC`/`WEB-DL`) is a
    mis-registered single episode and MUST NEVER be picked as the series
    folder, regardless of how many other candidates exist. If no candidate
    survives this filter, the series is treated as not-yet-in-the-library
    (return `None`) — a fresh folder is created, never a release folder
    reused.

### Dry-run mode (the safety net replacing a dev environment)

There is no dev cluster to rehearse against (see the home-server architecture
doc) — the first production rollout instead runs with dry-run mode on, and
these requirements are what make that safe.

- **REQ-SG-026** — In dry-run mode, a poll MUST run the full engine (feed
  fetch, gates, planning) and MUST send a digest of what it *would* do
  (clearly marked as dry-run in the subject), but MUST NOT call the
  downloader and MUST NOT persist any ledger mutation — the next poll must
  see the exact same starting state and re-derive the same decisions, so
  turning dry-run off never skips or double-processes anything.
- **REQ-SG-027** — In live mode, a poll's ledger mutations MUST be persisted
  only if every action in that poll executed without error; on any execution
  error, nothing from that poll is persisted, an error notify event is sent,
  and the next poll retries from the last-known-good state. This relies on
  the already-proven idempotency of both planning (REQ-SG-014) and the
  qBittorrent adapter's add/delete calls (safe to repeat) — a known tradeoff
  of this all-or-nothing scheme is that one failing action in a large poll
  causes redundant (but harmless) re-execution of that poll's other,
  already-succeeded actions next cycle.

### Scheduler and health

- **REQ-SG-029** — The service MUST expose `GET /healthz` returning 200 for
  basic liveness without depending on any external service being reachable.
- **REQ-SG-030** — Shutdown MUST stop the scheduler cleanly (no orphaned
  timers/threads) without corrupting in-progress state.
- **REQ-SG-031** — Poll cadence MUST be a setting, reschedulable at runtime
  without a process restart.
- **REQ-SG-032** — An unhandled exception anywhere in a poll (a feed fetch
  timeout, an adapter bug, anything not already caught by REQ-SG-023/027)
  MUST NOT crash the scheduler or kill future polls; it is recorded to the
  activity log as a failed poll, a best-effort notification is attempted,
  and the process continues to the next scheduled poll. This generalizes the
  gate-level resilience principle (REQ-SG-023) to the whole poll cycle.

### Live verification (2026-07-12)

The mocked unit tests above prove the code does what *we assumed* the real
APIs do — not that the assumption was correct. `scripts/live_smoke_test.py`
exists specifically to close that gap: a manual, env-var-configured script
that talks to real services. Run against the actual deployment target
(qBittorrent, Jellyfin, TVmaze):

- TVmaze: `test_connection()` and a real `airdate()` lookup (Silo S03E02) —
  passed as expected, no surprises.
- Jellyfin: `test_connection()`, `has_episode()` false for a nonexistent
  series, and **true** for a real already-downloaded episode (American Dad!
  S22E10) — passed.
- qBittorrent: `test_connection()`, then a real `add_magnet()` +
  `delete_with_files()` round-trip using a legal public-domain test torrent
  (Sintel) in an isolated save path/category — **initially failed**. The
  live server (qBittorrent 5.2.3) uses response contracts different from
  what REQ-SG-016 originally assumed (see above); the adapter and its mocks
  were both updated to match reality, and the fix was re-verified live
  before being trusted. One specific magnet hash (a different, equally
  legal public-domain torrent) was separately found to get a hard `409` from
  this server for reasons unrelated to showgrab (likely a blocklist/tracker
  interaction) — noted in the script, not investigated further.
- SMTP: live-tested against Gmail (`andy@valerio.nu`, app password,
  `smtp.gmail.com:587`, STARTTLS) — `test_connection()` and a real
  `send_digest()` both passed on the first attempt, delivering an actual
  digest email. No contract surprises this time (unlike qBittorrent above).

Takeaway worth keeping in mind for every future adapter: **mocks encode
assumptions, and assumptions about a real API's exact status codes/response
bodies are exactly the kind of thing that's wrong until proven otherwise.**
Live-test before trusting, the same way this pass caught a real bug in
minutes that could otherwise have silently broken grabs in production.

**All four phase-2 adapters are now live-verified** (qBittorrent, Jellyfin,
TVmaze, SMTP/Gmail). No real credentials are stored anywhere in this repo —
they were passed as environment variables for a single manual run of
`scripts/live_smoke_test.py` and are not persisted.
