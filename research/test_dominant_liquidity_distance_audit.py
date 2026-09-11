"""D2: pre-registered curves, arm keys, causality of the ATR scale."""

from datetime import UTC, datetime, timedelta

import pytest
from liquidity_hunter.core.domain import Candle, LiquidityZoneType, TimeFrame
from liquidity_hunter.tests.scoring._factories import make_zone
from research.dominant_liquidity_distance_audit import (
    ARMS,
    CURVES,
    arm_key,
    atr14,
    distance_score,
    observe,
    pick,
    reaction,
    swing_key,
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


def candidate(**overrides):
    base = {
        "family": "SW",
        "d_atr": 1.0,
        "distance_score": 50.0,
        "touch_score": 10.0,
        "timeframe_score": 65.0,
    }
    return {**base, **overrides}


# --- D2.0: the curves are what they say they are ---------------------------


def test_linear_curves_hit_their_pre_registered_zero():
    assert CURVES["LINEAR_5_ATR"](0.0) == 100.0
    assert CURVES["LINEAR_5_ATR"](2.5) == pytest.approx(50.0)
    assert CURVES["LINEAR_5_ATR"](5.0) == 0.0
    assert CURVES["LINEAR_5_ATR"](50.0) == 0.0
    assert CURVES["LINEAR_3_ATR"](3.0) == 0.0
    assert CURVES["LINEAR_3_ATR"](1.5) == pytest.approx(50.0)


def test_soft_decay_is_parameter_free_and_never_saturates():
    assert CURVES["SOFT_DECAY"](0.0) == 100.0
    assert CURVES["SOFT_DECAY"](1.0) == 50.0
    assert CURVES["SOFT_DECAY"](1000.0) > 0.0


def test_every_curve_is_monotone_non_increasing():
    for name, curve in CURVES.items():
        if curve is None:
            continue
        values = [curve(d / 10) for d in range(0, 200)]
        assert all(a >= b for a, b in zip(values, values[1:], strict=False)), name


def test_legacy_curve_reads_the_production_distance_score():
    assert distance_score(candidate(distance_score=37.0), "LEGACY_PCT") == 37.0
    assert distance_score(candidate(), None) == 0.0


# --- D2.1: the arms differ only where they are meant to --------------------


def test_arm_modes_credit_only_their_own_family():
    eq = candidate(family="EQ", touch_score=100.0)
    sw = candidate(family="SW", touch_score=100.0)
    for zone in (eq, sw):
        assert arm_key(zone, "LINEAR_5_ATR", "all") > arm_key(zone, "LINEAR_5_ATR", "none")
    assert arm_key(eq, "LINEAR_5_ATR", "eq") == arm_key(eq, "LINEAR_5_ATR", "all")
    assert arm_key(sw, "LINEAR_5_ATR", "eq") == arm_key(sw, "LINEAR_5_ATR", "none")


def test_distance_only_arm_is_pure_geometry_in_atr():
    near, far = candidate(d_atr=0.5, touch_score=0.0), candidate(d_atr=4.0, touch_score=100.0)
    assert arm_key(near, None, "none") > arm_key(far, None, "none")


def test_arm_list_covers_the_pre_registered_grid():
    names = {name for name, _, _ in ARMS}
    assert "LEGACY" in names and "DISTANCE_ONLY" in names
    assert len(ARMS) == 11  # legacy + 3 curves x 3 strength modes + distance-only


def test_no_strength_arm_reduces_to_the_nearest_level_when_nothing_is_zeroed():
    zones = [candidate(d_atr=3.0), candidate(d_atr=0.4), candidate(d_atr=1.2)]
    assert pick(zones, "LINEAR_5_ATR", "none") == 1
    assert pick(zones, None, "none") == 1


# --- D2.16: tie-break simulation -------------------------------------------


def test_ties_keep_entry_order_unless_proximity_is_requested():
    # Both beyond 5 ATR: LINEAR_5_ATR zeroes both, so only entry order separates.
    zones = [candidate(d_atr=9.0, touch_score=0.0), candidate(d_atr=6.0, touch_score=0.0)]
    assert pick(zones, "LINEAR_5_ATR", "none") == 0
    assert pick(zones, "LINEAR_5_ATR", "none", by_proximity=True) == 1


def test_proximity_tie_break_only_ranges_over_the_tied_candidates():
    zones = [
        candidate(d_atr=9.0, touch_score=100.0),
        candidate(d_atr=0.2, touch_score=0.0),
        candidate(d_atr=8.0, touch_score=100.0),
    ]
    assert pick(zones, "LINEAR_5_ATR", "all", by_proximity=True) == 2


# --- D2.11: reaction ---------------------------------------------------------


def test_reaction_separates_rejection_from_through_on_both_sides():
    buy = make_zone(110, price_low=110)
    future = [candle(1, 110, 108)] + [candle(i, 114, 100, close=111) for i in range(2, 12)]
    result = reaction(buy, future, 1, atr=2.0)
    assert result["closed_through"] is True
    assert result["rejection_atr"] == (110 - 100) / 2.0
    assert result["through_atr"] == (114 - 110) / 2.0
    assert result["ratio"] == pytest.approx(2.5)

    sell = make_zone(90, price_low=90, zone_type=LiquidityZoneType.SWING_LOW).model_copy(
        update={"side": buy.side.__class__.SELL_SIDE}
    )
    down = [candle(1, 92, 90)] + [candle(i, 96, 88, close=95) for i in range(2, 12)]
    result = reaction(sell, down, 1, atr=2.0)
    assert result["closed_through"] is False
    assert result["rejection_atr"] == (96 - 90) / 2.0
    assert result["through_atr"] == (90 - 88) / 2.0


def test_reaction_needs_the_whole_window():
    assert reaction(make_zone(110), [candle(1, 110, 108)], 1, atr=1.0) is None
    assert reaction(make_zone(110), [], None, atr=1.0) is None


# --- D2.18: causality --------------------------------------------------------


def test_atr_is_causal_future_bars_cannot_change_it():
    bars = [candle(i, 100 + i, 95 + i) for i in range(40)]
    later = bars + [candle(i, 500, 400) for i in range(40, 60)]
    assert atr14(bars) == atr14(later[: len(bars)])


def test_selection_is_fixed_before_the_future_is_read():
    zones = [make_zone(110, strength=1.0), make_zone(100.5, strength=0.0)]
    future = [candle(i, 200, 50) for i in range(1, 20)]
    blind = observe(zones, 100.0, atr=1.0, future=[])
    seeing = observe(zones, 100.0, atr=1.0, future=future)
    ranking = ["d_atr", "distance_score", "touch_score", "timeframe_score", "family"]
    assert [[c[k] for k in ranking] for c in blind] == [[c[k] for k in ranking] for c in seeing]
    assert blind[0]["contact"] is None and seeing[0]["contact"] == 1


# --- D2.17: the window-dependence probe keys on the pivot, not the window ---


def test_swing_key_identifies_the_same_pivot_across_prefixes():
    zone = make_zone(110, strength=0.4)
    assert swing_key(zone) == swing_key(zone.model_copy(update={"strength": 0.9}))
    assert swing_key(zone) != swing_key(make_zone(111, strength=0.4))
