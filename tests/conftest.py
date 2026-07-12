"""Shared test fixtures and helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from showgrab.core.release import FeedItem

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_feed_xml() -> str:
    return (FIXTURES / "feed_sample.xml").read_text()


def make_item(
    raw_title: str,
    *,
    show_id: str = "1",
    show_name: str = "Test Show",
    episode_id: str | None = None,
    infohash: str | None = None,
    external_id: str | None = "1000",
    pub_date: datetime | None = None,
) -> FeedItem:
    """Build a FeedItem for tests; infohash defaults to the title so each
    distinct release is a distinct variant."""
    ih = infohash or raw_title
    return FeedItem(
        show_id=show_id,
        show_name=show_name,
        episode_id=episode_id or ih,
        raw_title=raw_title,
        magnet=f"magnet:?xt=urn:btih:{ih}",
        infohash=ih,
        pub_date=pub_date or datetime(2026, 7, 1, tzinfo=timezone.utc),
        external_id=external_id,
    )
