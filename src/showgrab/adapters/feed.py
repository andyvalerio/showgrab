"""Parse the showRSS namespaced RSS into FeedItems (REQ-SG-007).

This is the one place that knows the feed's XML shape. Malformed or incomplete
items are skipped rather than allowed to crash the whole parse.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from ..core.release import FeedItem

TV_NS = "https://showrss.info"


def parse_feed(xml_text: str) -> list[FeedItem]:
    items: list[FeedItem] = []
    root = ET.fromstring(xml_text)
    for it in root.iter("item"):
        try:
            item = _parse_item(it)
        except Exception:
            continue
        if item is not None:
            items.append(item)
    return items


def _parse_item(it: ET.Element) -> FeedItem | None:
    def tv(tag: str) -> str | None:
        el = it.find(f"{{{TV_NS}}}{tag}")
        return el.text if el is not None else None

    def plain(tag: str) -> str | None:
        el = it.find(tag)
        return el.text if el is not None else None

    raw_title = tv("raw_title") or plain("title")
    show_id = tv("show_id")
    if not raw_title or not show_id:
        return None  # minimally required to be useful

    magnet = plain("link") or ""
    infohash = (tv("info_hash") or "").upper()
    episode_id = tv("episode_id") or infohash or magnet

    pd = plain("pubDate")
    pub = parsedate_to_datetime(pd) if pd else None
    if pub is None:
        pub = datetime.now(timezone.utc)
    elif pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)

    return FeedItem(
        show_id=show_id,
        show_name=tv("show_name") or "",
        episode_id=episode_id,
        raw_title=raw_title,
        magnet=magnet,
        infohash=infohash or episode_id or magnet,
        pub_date=pub,
        external_id=tv("external_id"),
    )
