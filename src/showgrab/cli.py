"""Dry-run CLI: parse a feed and print the decision for each episode (REQ-SG-015).

Phase 1 has no real Jellyfin/TVmaze/qBittorrent adapters yet, so the external
checks are stubbed (assume nothing is in the library; treat every episode as
recent). This exists to eyeball the engine against the live feed; it never
downloads anything.
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from datetime import datetime, timezone

from .adapters.feed import parse_feed
from .core import engine, quality
from .core.ledger import Ledger
from .core.models import Config, Status
from .core.release import parse_release


class _StubLibrary:
    def has_episode(self, *args, **kwargs) -> bool:
        return False


class _StubMetadata:
    def airdate(self, external_id, show_name, season, episode):
        return datetime.now(timezone.utc)  # treat as recent → never skipped-old


def _load(source: str) -> str:
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    with open(source, encoding="utf-8") as f:
        return f.read()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="showgrab", description="Dry-run the showgrab engine over a feed.")
    p.add_argument("source", help="feed URL or path to an XML file")
    p.add_argument("--preferred", default="720p", help="preferred quality (SD/720p/1080p/2160p)")
    p.add_argument("--wait-hours", type=float, default=6.0)
    p.add_argument("--dry-run", action="store_true", default=True, help="(default, and the only mode in phase 1)")
    args = p.parse_args(argv)

    config = Config(
        preferred_quality=quality.from_label(args.preferred),
        wait_hours=args.wait_hours,
    )
    items = parse_feed(_load(args.source))
    releases = [parse_release(i) for i in items]
    ledger = Ledger()
    checks = engine.Checks(_StubLibrary(), _StubMetadata())
    result = engine.plan(ledger, releases, datetime.now(timezone.utc), config, checks)

    print(f"Parsed {len(items)} feed items → {len(ledger.all())} episodes "
          f"(preferred={config.preferred_quality.label}, wait={config.wait_hours}h)\n")
    for entry in sorted(ledger.all(), key=lambda e: (e.show_name, str(e.key))):
        variants = ", ".join(
            sorted(f"{v.quality.label}{'/REMUX' if v.is_remux else ''}{'/REPACK' if v.is_repack else ''}"
                   for v in entry.variants.values())
        )
        chosen = f" → {entry.chosen.quality.label}" if entry.chosen else ""
        ep = f"S{entry.season:02d}E{entry.episode:02d}" if entry.is_episode else "?"
        print(f"  [{entry.status.value:14}] {entry.show_name} {ep}  "
              f"[{variants or 'none'}]{chosen}")

    grabs = [a for a in result.actions if type(a).__name__ == "GrabAction"]
    swaps = [a for a in result.actions if type(a).__name__ == "SwapAction"]
    print(f"\nWould grab {len(grabs)}, swap {len(swaps)}; {len(result.events)} notify events.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
