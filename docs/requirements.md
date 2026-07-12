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
