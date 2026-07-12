# Verifies: REQ-SG-007 (parse showRSS namespaced RSS into FeedItems; extract
#   show_id/external_id/show_name/episode_id/raw_title/magnet/pubDate; skip
#   malformed items instead of crashing).
# Scenario: parse the sample feed fixture and a deliberately broken document.

from datetime import timezone

from showgrab.adapters.feed import parse_feed


def test_parses_all_valid_items(sample_feed_xml):
    items = parse_feed(sample_feed_xml)
    # 7 items in the fixture, all minimally valid (show_id + title present).
    assert len(items) == 7


def test_extracts_namespaced_fields(sample_feed_xml):
    items = parse_feed(sample_feed_xml)
    silo_2160 = next(i for i in items if i.infohash == "AAA2160")
    assert silo_2160.show_id == "1675"
    assert silo_2160.external_id == "38052"
    assert silo_2160.show_name == "Silo"
    assert silo_2160.episode_id == "241120"
    assert silo_2160.magnet.startswith("magnet:?xt=urn:btih:AAA2160")
    assert silo_2160.pub_date.tzinfo is not None
    assert silo_2160.pub_date.astimezone(timezone.utc).year == 2026


def test_infohash_is_uppercased(sample_feed_xml):
    items = parse_feed(sample_feed_xml)
    # fixture stores tv:info_hash lowercase; parser normalizes to upper.
    assert any(i.infohash == "AAA2160" for i in items)


def test_item_without_show_id_is_skipped():
    xml = """<rss xmlns:tv="https://showrss.info"><channel>
      <item><title>Has title but no show id</title><link>magnet:x</link></item>
      <item><title>Good S01E01 720p</title><link>magnet:y</link>
        <tv:show_id>5</tv:show_id><tv:raw_title>Good S01E01 720p</tv:raw_title></item>
    </channel></rss>"""
    items = parse_feed(xml)
    assert len(items) == 1
    assert items[0].show_id == "5"
