# Verifies: REQ-SG-005 (rank by distance from preferred, ties break toward the
#   lower tier; a plain tier-T release beats a REMUX at tier T when preferred=T)
#   and REQ-SG-006 (best variant = min by (distance, rank)).
# Scenario: score a spread of tiers/remux flags against several preferred tiers
#   and assert the ordering and the winner.

from showgrab.core.quality import Quality, from_label, rank, score


def test_rank_orders_tiers_and_places_remux_between_tiers():
    assert rank(Quality.HD720) < rank(Quality.HD1080)
    # REMUX sits above its plain tier but below the next tier up.
    assert rank(Quality.HD1080) < rank(Quality.HD1080, is_remux=True) < rank(Quality.UHD2160)


def test_plain_tier_beats_remux_at_preferred_tier():
    # preferred = 1080p: a plain 1080p is a closer match than a 1080p REMUX.
    plain = score(Quality.HD1080, False, Quality.HD1080)
    remux = score(Quality.HD1080, True, Quality.HD1080)
    assert plain < remux


def test_ties_break_toward_lower_tier():
    # preferred = 720p, offered 480p and 1080p: both distance 1, lower wins.
    sd = score(Quality.SD, False, Quality.HD720)
    hd = score(Quality.HD1080, False, Quality.HD720)
    assert sd[0] == hd[0]  # equal distance
    assert sd < hd  # SD wins the tie


def test_best_of_offered_set_matches_preferred_when_present():
    preferred = Quality.HD720
    offered = [
        (Quality.UHD2160, False),
        (Quality.HD1080, False),
        (Quality.HD720, False),
    ]
    best = min(offered, key=lambda qr: score(qr[0], qr[1], preferred))
    assert best == (Quality.HD720, False)


def test_best_picks_nearest_when_preferred_absent():
    preferred = Quality.HD720
    offered = [(Quality.UHD2160, False), (Quality.HD1080, False)]
    best = min(offered, key=lambda qr: score(qr[0], qr[1], preferred))
    assert best == (Quality.HD1080, False)


def test_from_label_roundtrip():
    assert from_label("720p") is Quality.HD720
    assert from_label("4K") is Quality.UHD2160
