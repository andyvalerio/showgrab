"""Pure formatting of notify events into a digest email body (REQ-SG-021).

Kept separate from the SMTP adapter so the grouping/formatting logic is
testable without touching a network — and reusable later by the web UI's
activity log.
"""

from __future__ import annotations

from .models import NotifyEvent

_TITLES = {
    "grabbed": "Grabbed",
    "swapped": "Swapped",
    "skipped-old": "Skipped (too old)",
    "needs-attention": "Needs attention",
}
_ORDER = ["grabbed", "swapped", "skipped-old", "needs-attention"]


def build_digest(events: list[NotifyEvent], *, dry_run: bool = False) -> tuple[str, str] | None:
    """Returns (subject, body), or None when there is nothing to report.

    dry_run marks the subject unambiguously (REQ-SG-026) so a review-window
    digest can never be mistaken for one describing real downloads."""
    if not events:
        return None

    by_kind: dict[str, list[NotifyEvent]] = {}
    for e in events:
        by_kind.setdefault(e.kind, []).append(e)

    lines: list[str] = []
    if dry_run:
        lines.append("DRY RUN — nothing below was actually downloaded or deleted.")
        lines.append("")
    seen_kinds = list(_ORDER) + [k for k in by_kind if k not in _ORDER]
    for kind in seen_kinds:
        group = by_kind.get(kind)
        if not group:
            continue
        lines.append(f"{_TITLES.get(kind, kind)} ({len(group)})")
        for e in sorted(group, key=lambda x: x.show_name):
            lines.append(f"  - {e.show_name}: {e.detail}")
        lines.append("")

    count = len(events)
    prefix = "[DRY RUN] " if dry_run else ""
    subject = f"{prefix}showgrab digest — {count} event{'s' if count != 1 else ''}"
    body = "\n".join(lines).rstrip() + "\n"
    return subject, body
