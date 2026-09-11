"""D1: the audit's own primitives — buckets, contact, reaction, counterfactuals."""

from datetime import UTC, datetime, timedelta

import pytest
from liquidity_hunter.core.domain import Candle, LiquidityZoneType, TimeFrame
from liquidity_hunter.tests.scoring._factories import make_zone
from research.dominant_liquidity_strength_audit import (
    bucket_of,
    family,
    first_contact,
    observe,
    reaction,
    spearman,
    stratified_split,
)

START = datetime(2024, 1, 1, tzinfo=UTC)


def candle(i: int, high: float, low: float, close: float | None = None) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=START + timedelta(hours=i),
        open=(high + low) / 2,
        high=high,
        low=low,
        close=high if close is None else close,
        volume=10.0,
        taker_buy_volume=5.0,
    )


def test_family_splits_eq_from_swing():
    assert family(make_zone(100, zone_type=LiquidityZoneType.EQUAL_HIGHS)) == "EQ"
    assert family(make_zone(100, zone_type=LiquidityZoneType.SWING_HIGH)) == "SW"


def test_bucket_edges_are_half_open_and_overflow():
    assert [bucket_of(v) for v in (0.0, 0.49, 0.5, 1.0, 2.5, 3.0, 4.9, 5.0, 99.0)] == [
        0, 0, 1, 2, 3, 4, 4, 5, 5
    ]


def test_first_contact_is_band_intersection_and_one_based():
    zone = make_zone(110, price_low=109)
    future = [candle(1, 105, 100), candle(2, 109.5, 104), candle(3, 120, 110)]
    assert first_contact(zone, future) == 2
    assert first_contact(make_zone(200), future) is None


def test_reaction_reports_through_and_rejection_for_both_sides():
    buy = make_zone(110, price_low=110)
    future = [candle(1, 110, 108)] + [candle(i, 112, 100, close=111) for i in range(2, 12)]
    through, rejection = reaction(buy, future, 1, atr=2.0)
    assert through is True
    assert rejection == (110 - 100) / 2.0

    sell = make_zone(90, price_low=90, zone_type=LiquidityZoneType.SWING_LOW)
    sell = sell.model_copy(update={"side": buy.side.__class__.SELL_SIDE})
    down = [candle(1, 92, 90)] + [candle(i, 96, 89, close=95) for i in range(2, 12)]
    through, rejection = reaction(sell, down, 1, atr=2.0)
    assert through is False
    assert rejection == (96 - 90) / 2.0


def test_reaction_is_none_without_a_full_window():
    zone = make_zone(110)
    assert reaction(zone, [candle(1, 110, 108)], 1, atr=1.0) == (None, None)
    assert reaction(zone, [], None, atr=1.0) == (None, None)


def test_observe_reproduces_the_production_ranking_and_flags_the_far_winner():
    zones = [make_zone(110, strength=1.0), make_zone(100.5, strength=0.0)]
    result = observe(zones, 100.0, atr=1.0, future=[])
    assert result["candidates"][0]["d_atr"] == 10.0
    assert result["candidates"][0]["distance_score"] == 0
    assert not result["winner_is_nearest"]
    assert result["reason"] == "B_strength"
    # Dropping strength hands the pick to the near level; distance-only agrees.
    assert result["counterfactuals"]["no_strength"] == 1
    assert result["counterfactuals"]["distance_only"] == 1
    assert result["counterfactuals"]["current"] == 0


def test_counterfactual_ablations_only_credit_their_own_family():
    eq = make_zone(130, strength=1.0, zone_type=LiquidityZoneType.EQUAL_HIGHS)
    swing = make_zone(101, strength=0.2, zone_type=LiquidityZoneType.SWING_HIGH)
    result = observe([eq, swing], 100.0, atr=1.0, future=[])
    assert result["candidates"][0]["family"] == "EQ"
    assert result["counterfactuals"]["eq_only"] == 0
    assert result["counterfactuals"]["sw_only"] == 1


def test_ties_keep_detector_entry_order_and_expose_the_proximity_alternative():
    zones = [make_zone(130, strength=1.0), make_zone(106, strength=1.0)]
    result = observe(zones, 100.0, atr=1.0, future=[])
    assert result["ties"] == 2
    assert result["reason"] == "D_tie"
    assert result["all_distance_scores_zero"]
    assert result["tie_break_nearest"] == 1


def test_mitigated_zones_are_excluded():
    used = make_zone(100, strength=1.0).model_copy(update={"is_mitigated": True})
    assert observe([used], 100.0, atr=1.0, future=[]) is None


def test_spearman_is_rank_based_and_tolerates_ties():
    assert spearman([(1, 1), (2, 2), (3, 3), (4, 4)]) == pytest.approx(1.0)
    assert spearman([(1, 4), (2, 3), (3, 2), (4, 1)]) == pytest.approx(-1.0)
    assert spearman([(1, 1), (1, 2)]) != spearman([(1, 1), (1, 2), (2, 3)])


def test_stratified_split_compares_only_inside_a_stratum():
    # Two strata with opposite level effects but the same within-stratum ordering:
    # pooling would cancel, stratifying must not.
    rows = []
    for stratum, base in (("a", 1.0), ("b", 0.0)):
        for i in range(4):
            rows.append({"k": stratum, "strength": i, "hit": base})
        rows[-1]["hit"] = base + 0.0
    for row in rows:
        row["hit"] = 1.0 if row["strength"] >= 2 else 0.0
    result = stratified_split(rows, lambda r: r["hit"], [lambda r: r["k"]], min_stratum=4)
    assert result["gap"] == 1.0
    assert (result["wins"], result["losses"], result["strata"]) == (2, 0, 2)


def test_stratified_split_drops_thin_and_degenerate_strata():
    thin = [{"k": "a", "strength": i, "hit": 1.0} for i in range(3)]
    assert stratified_split(thin, lambda r: r["hit"], [lambda r: r["k"]]) is None
    flat = [{"k": "a", "strength": 1.0, "hit": 1.0} for _ in range(8)]
    assert stratified_split(flat, lambda r: r["hit"], [lambda r: r["k"]]) is None
