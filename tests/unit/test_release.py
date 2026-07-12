# Verifies: REQ-SG-001 (episode identity from show_id + SxxEyy), REQ-SG-002
#   (quality tier + REMUX/REPACK flags), REQ-SG-003 (embedded year without
#   mistaking a resolution for a year), REQ-SG-004 (unparsable quality → none).
# Scenario: parse a spread of real-world-shaped titles and assert each field.

from showgrab.core.quality import Quality
from showgrab.core.release import parse_release

from tests.conftest import make_item


def test_basic_episode_and_quality():
    r = parse_release(make_item("Silo S03E02 Its All Good 720p ATVP WEB DL", show_id="1675"))
    assert r.season == 3 and r.episode == 2
    assert r.episode_key == ("1675", 3, 2)
    assert r.quality is Quality.HD720
    assert r.parsable


def test_uppercase_resolution_token():
    r = parse_release(make_item("Silo S03E02 2160P ATVP WEB DL H 265"))
    assert r.quality is Quality.UHD2160


def test_repack_flag_and_tier():
    r = parse_release(make_item("House of the Dragon S03E02 REPACK 720p HEVC x265"))
    assert r.is_repack
    assert r.quality is Quality.HD720


def test_remux_flag_independent_of_tier():
    r = parse_release(make_item("Some Show S01E05 1080p REMUX FLAC AVC"))
    assert r.is_remux
    assert r.quality is Quality.HD1080


def test_embedded_year_not_confused_with_resolution():
    r = parse_release(make_item("Avatar The Last Airbender 2024 S01E01 Aang 2160p NF WEB DL"))
    assert r.year == 2024
    assert r.quality is Quality.UHD2160  # 2160p not read as a year
    assert r.season == 1 and r.episode == 1


def test_no_year_when_absent():
    r = parse_release(make_item("Silo S03E02 720p ATVP"))
    assert r.year is None


def test_unparsable_quality_is_none():
    r = parse_release(make_item("Weird Release S01E05 With No Resolution Token"))
    assert r.quality is None
    assert not r.parsable  # has an episode key but no quality


def test_missing_episode_marker_has_no_key():
    r = parse_release(make_item("Weird Release With No Episode Marker 1080p"))
    assert r.episode_key is None
    assert not r.parsable
