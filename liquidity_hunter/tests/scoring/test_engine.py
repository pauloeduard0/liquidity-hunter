"""Tests for `LiquidityScoringEngine`."""

import pytest

from liquidity_hunter.core.domain import LiquidityZoneType, TimeFrame
from liquidity_hunter.scoring import LiquidityScoringEngine
from liquidity_hunter.tests.scoring._factories import make_zone

CURRENT_PRICE = 100.0


def test_closer_zone_scores_higher() -> None:
    engine = LiquidityScoringEngine()

    near = make_zone(100.5, strength=0.5, timeframe=TimeFrame.H1)  # 0.5% away
    far = make_zone(104.0, strength=0.5, timeframe=TimeFrame.H1)  # 4% away

    near_score, far_score = engine.score([near, far], CURRENT_PRICE)

    assert near_score.zone is near
    assert near_score.score > far_score.score
    assert near_score.distance_score == pytest.approx(90.0)
    assert far_score.distance_score == pytest.approx(20.0)


def test_more_touches_scores_higher() -> None:
    engine = LiquidityScoringEngine()

    strong = make_zone(100.0, strength=0.9, zone_type=LiquidityZoneType.EQUAL_LOWS)
    weak = make_zone(100.0, strength=0.1, zone_type=LiquidityZoneType.EQUAL_LOWS)

    strong_score, weak_score = engine.score([strong, weak], CURRENT_PRICE)

    assert strong_score.zone is strong
    assert strong_score.score > weak_score.score
    assert strong_score.touch_score == pytest.approx(90.0)
    assert weak_score.touch_score == pytest.approx(10.0)


def test_higher_timeframe_scores_higher() -> None:
    engine = LiquidityScoringEngine()

    weekly = make_zone(100.0, strength=0.5, timeframe=TimeFrame.W1)
    minute = make_zone(100.0, strength=0.5, timeframe=TimeFrame.M1)

    weekly_score, minute_score = engine.score([weekly, minute], CURRENT_PRICE)

    assert weekly_score.zone is weekly
    assert weekly_score.score > minute_score.score
    assert weekly_score.timeframe_score == pytest.approx(100.0)
    assert minute_score.timeframe_score == pytest.approx(10.0)


def test_distance_beyond_max_clips_to_zero() -> None:
    engine = LiquidityScoringEngine(max_distance_pct=0.05)

    zone = make_zone(200.0, strength=1.0, timeframe=TimeFrame.W1)  # 100% away

    [scored] = engine.score([zone], CURRENT_PRICE)

    assert scored.distance_score == 0.0
    assert scored.score == pytest.approx(60.0)  # 0*0.4 + 100*0.4 + 100*0.2


def test_score_is_within_bounds() -> None:
    engine = LiquidityScoringEngine()

    best = make_zone(CURRENT_PRICE, strength=1.0, timeframe=TimeFrame.W1)
    worst = make_zone(CURRENT_PRICE * 2, strength=0.0, timeframe=TimeFrame.M1)

    best_score, worst_score = engine.score([best, worst], CURRENT_PRICE)

    assert best_score.score == pytest.approx(100.0)
    assert 0.0 <= worst_score.score <= 100.0


def test_results_sorted_by_descending_score() -> None:
    engine = LiquidityScoringEngine()

    low = make_zone(110.0, strength=0.1, timeframe=TimeFrame.M1)
    high = make_zone(100.0, strength=1.0, timeframe=TimeFrame.W1)

    scored = engine.score([low, high], CURRENT_PRICE)

    assert [s.zone for s in scored] == [high, low]
    assert scored[0].score >= scored[1].score


@pytest.mark.parametrize(
    "kwargs",
    [
        {"distance_weight": 0.5, "touch_weight": 0.5, "timeframe_weight": 0.5},  # sums to 1.5
        {"distance_weight": -0.1, "touch_weight": 0.5, "timeframe_weight": 0.6},
        {"max_distance_pct": 0.0},
    ],
)
def test_invalid_engine_config_raises(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        LiquidityScoringEngine(**kwargs)


def test_invalid_current_price_raises() -> None:
    engine = LiquidityScoringEngine()

    with pytest.raises(ValueError, match="current_price"):
        engine.score([make_zone(100.0)], current_price=0.0)
