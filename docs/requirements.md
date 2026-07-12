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
