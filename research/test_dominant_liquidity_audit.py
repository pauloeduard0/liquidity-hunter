"""D0 ranking pathologies and no-forward-data selection."""

from liquidity_hunter.tests.scoring._factories import make_zone
from research.dominant_liquidity_audit import measure


def test_far_strong_zone_can_beat_near_weak_zone():
    result = measure([make_zone(110, strength=1), make_zone(100.5, strength=0)], 100, 1, [])
    assert result["winner"]["midpoint"] == 110
    assert result["nearest"]["midpoint"] == 100.5
    assert result["winner"]["distance_score"] == 0
    assert not result["winner_is_nearest"]
    assert result["winner"]["contact20"] is None


def test_distance_saturation_and_stable_tie_order():
    result = measure([make_zone(130, strength=1), make_zone(106, strength=1)], 100, 1, [])
    assert result["top_score_ties"] == 2
    assert result["all_distance_scores_zero"]
    assert result["winner"]["midpoint"] == 130


def test_consumed_zones_cannot_win():
    used = make_zone(100, strength=1).model_copy(update={"is_mitigated": True})
    assert measure([used], 100, 1, []) == {"active": 0}
