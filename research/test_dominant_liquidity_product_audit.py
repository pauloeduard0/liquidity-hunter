"""D4: the pre-registered tie-break, the two architectures, width-neutral reaction."""

from datetime import UTC, datetime, timedelta

import pytest
from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.scoring.engine import LiquidityScoringEngine
from liquidity_hunter.tests.scoring._factories import make_zone
from research.dominant_liquidity_product_audit import (
    ALT_WINDOW,
    CURVE,
    best,
    distance_score,
    legacy_score,
    midpoint_reaction,
    observe,
    order_key,
    picks,
)

START_AT = datetime(2024, 1, 1, tzinfo=UTC)


def candle(i: int, high: float, low: float, close: float | None = None) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=START_AT + timedelta(hours=i),
        open=(high + low) / 2,
        high=high,
        low=low,
        close=high if close is None else close,
        volume=10.0,
        taker_buy_volume=5.0,
    )


def candidate(family="SW", d_atr=1.0, strength=0.1, above=True, tag="a"):
    return {
        "family": family,
        "type": "swing_high" if family == "SW" else "equal_highs",
        "above": above,
        "d_atr": d_atr,
        "strength": strength,
        "strength_alt": strength,
        "legacy_distance_score": 50.0,
        "timeframe_score": 65.0,
        "legacy_score": 0.0,
        "fallback": (tag, 0.0, 0.0),
        "level": f"{family}-{tag}",
    }


# --- D4.2: the tie-break, exactly as pre-registered -------------------------


def test_distance_decides_before_strength_always():
    near_weak = candidate(d_atr=0.5, strength=0.0)
    far_strong = candidate(d_atr=4.9, strength=1.0, tag="b")
    assert best([far_strong, near_weak]) is near_weak


def test_inside_the_saturated_zone_the_atr_distance_still_decides():
    """Both past 5 ATR: the curve flattened them to zero, step 2 has to rule."""
    far = candidate(d_atr=40.0, strength=1.0)
    less_far = candidate(d_atr=7.0, strength=0.0, tag="b")
    assert distance_score(far) == distance_score(less_far) == 0.0
    assert best([far, less_far]) is less_far


def test_strength_only_breaks_an_exact_distance_tie():
    weak = candidate(d_atr=2.0, strength=0.01)
    strong = candidate(d_atr=2.0, strength=0.90, tag="b")
    assert best([weak, strong]) is strong


def test_a_total_tie_falls_back_deterministically_not_on_emission_order():
    first = candidate(d_atr=2.0, strength=0.5, tag="aaa")
    second = candidate(d_atr=2.0, strength=0.5, tag="bbb")
    assert best([first, second]) is best([second, first])
    assert best([first, second])["fallback"] == ("bbb", 0.0, 0.0)  # highest key, not first seen


def test_order_key_has_the_four_pre_registered_steps_in_order():
    key = order_key(candidate(d_atr=2.0, strength=0.3, tag="z"))
    assert key[0] == distance_score(candidate(d_atr=2.0))
    assert key[1] == -2.0
    assert key[2] == 0.3
    assert key[3] == ("z", 0.0, 0.0)


def test_the_curve_is_the_d2_survivor():
    assert CURVE == "LINEAR_5_ATR"
    assert distance_score(candidate(d_atr=0.0)) == 100.0
    assert distance_score(candidate(d_atr=5.0)) == 0.0


# --- D4.1/D4.5: the architectures -------------------------------------------


def test_dual_never_makes_the_families_compete():
    far_eq = candidate("EQ", d_atr=6.0, strength=0.9, tag="eq")
    near_swing = candidate("SW", d_atr=0.4, strength=0.01, tag="sw")
    result = picks([far_eq, near_swing])
    assert result["single"] is near_swing
    assert result["best_eq"] is far_eq
    assert result["best_swing"] is near_swing


def test_dual_reports_none_for_a_family_with_no_candidate():
    result = picks([candidate("SW", d_atr=1.0)])
    assert result["best_eq"] is None
    assert result["best_swing"] is not None


def test_best_above_and_below_split_on_the_side_not_the_family():
    above = candidate("EQ", d_atr=2.0, above=True, tag="up")
    below = candidate("SW", d_atr=3.0, above=False, tag="down")
    result = picks([above, below])
    assert result["best_above"] is above
    assert result["best_below"] is below


def test_legacy_score_reproduces_the_production_engine():
    zones = [make_zone(110, strength=0.8), make_zone(101, strength=0.05)]
    scored = LiquidityScoringEngine().score(zones, 100.0)
    rows = observe(zones, 100.0, atr=1.0, future=[], span=50.0, alt_span=50.0)
    for produced, row in zip(scored, rows, strict=True):
        assert legacy_score(row) == pytest.approx(produced.score)


# --- D4.15: window dependence ------------------------------------------------


def test_a_shared_rescale_cannot_move_the_single_or_dual_pick():
    """Every swing divides by the same price_range, so the order is preserved."""
    rows = [
        candidate("SW", d_atr=1.0, strength=0.010, tag="a"),
        candidate("SW", d_atr=1.0, strength=0.004, tag="b"),
    ]
    for row in rows:
        row["strength_alt"] = row["strength"] * 0.37
    assert picks(rows, "strength")["single"] is picks(rows, "strength_alt")["single"]
    assert ALT_WINDOW == 600


def test_the_legacy_composite_does_move_with_the_window():
    row = candidate("SW", d_atr=1.0, strength=0.30)
    row["strength_alt"] = 0.60
    assert legacy_score(row, "strength_alt") > legacy_score(row, "strength")


# --- D4.9: the width-neutral reaction ---------------------------------------


def test_midpoint_reaction_judges_both_families_from_the_midpoint():
    band = make_zone(112, price_low=108)  # midpoint 110, 4 wide
    point = make_zone(110, price_low=110)
    future = [candle(1, 110, 108)] + [candle(i, 114, 100, close=111) for i in range(2, 12)]
    banded = midpoint_reaction(band, future, 1, atr=2.0)
    pointed = midpoint_reaction(point, future, 1, atr=2.0)
    assert banded["rejection_atr"] == pointed["rejection_atr"] == (110 - 100) / 2.0
    assert banded["through_atr"] == pointed["through_atr"] == (114 - 110) / 2.0
    assert banded["closed_through"] == pointed["closed_through"]


def test_midpoint_reaction_requires_real_clearance_to_call_it_through():
    zone = make_zone(110, price_low=110)
    grazing = [candle(1, 110, 109)] + [candle(i, 111, 109, close=110.4) for i in range(2, 12)]
    assert midpoint_reaction(zone, grazing, 1, atr=2.0)["closed_through"] is False
    decisive = [candle(1, 110, 109)] + [candle(i, 112, 109, close=111.0) for i in range(2, 12)]
    assert midpoint_reaction(zone, decisive, 1, atr=2.0)["closed_through"] is True


def test_midpoint_reaction_needs_the_whole_window():
    assert midpoint_reaction(make_zone(110), [candle(1, 110, 108)], 1, atr=2.0) is None
    assert midpoint_reaction(make_zone(110), [], None, atr=2.0) is None


# --- D4.3: causality ---------------------------------------------------------


def test_the_selection_never_reads_the_future():
    zones = [make_zone(110, strength=0.8), make_zone(100.5, strength=0.0)]
    future = [candle(i, 200, 50) for i in range(1, 20)]
    blind = observe(zones, 100.0, atr=1.0, future=[], span=50.0, alt_span=50.0)
    seeing = observe(zones, 100.0, atr=1.0, future=future, span=50.0, alt_span=50.0)
    fields = ["d_atr", "strength", "strength_alt", "legacy_distance_score", "family"]
    assert [[c[f] for f in fields] for c in blind] == [[c[f] for f in fields] for c in seeing]
    assert picks(blind)["single"]["level"] == picks(seeing)["single"]["level"]
    assert blind[0]["contact"] is None and seeing[0]["contact"] == 1
