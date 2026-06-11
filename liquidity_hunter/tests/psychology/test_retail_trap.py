"""Tests for `RetailTrapAnalyzer`."""

from datetime import UTC, datetime, timedelta

import pytest

from liquidity_hunter.core.domain import (
    LiquiditySide,
    LiquidityZoneType,
    MarketDirection,
    RetailPositioning,
    StructureEvent,
)
from liquidity_hunter.psychology import RetailTrapAnalyzer
from liquidity_hunter.tests.psychology._factories import make_structure_event, make_zone

CURRENT_PRICE = 100.0


def test_counter_trend_choch_matches_worked_example() -> None:
    """Higher TF bearish + lower TF bullish CHOCH -> retail likely buying a perceived bottom."""
    analyzer = RetailTrapAnalyzer()

    estimate = analyzer.analyze(
        symbol="BTCUSDT",
        higher_timeframe_direction=MarketDirection.BEARISH,
        market_structure_events=[
            make_structure_event(StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH)
        ],
        liquidity_zones=[
            make_zone(
                95.0,
                side=LiquiditySide.SELL_SIDE,
                zone_type=LiquidityZoneType.EQUAL_LOWS,
                strength=0.1,
            )
        ],
        current_price=CURRENT_PRICE,
    )

    assert estimate.dominant_side == RetailPositioning.LONG
    assert estimate.confidence == pytest.approx(82.0)
    assert "buy a perceived bottom against the higher timeframe trend" in estimate.explanation
    assert "equal lows zone acting as perceived support" in estimate.explanation


def test_trend_aligned_continuation_has_lower_confidence() -> None:
    analyzer = RetailTrapAnalyzer()

    estimate = analyzer.analyze(
        symbol="BTCUSDT",
        higher_timeframe_direction=MarketDirection.BULLISH,
        market_structure_events=[
            make_structure_event(StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH)
        ],
        liquidity_zones=[],
        current_price=CURRENT_PRICE,
    )

    assert estimate.dominant_side == RetailPositioning.LONG
    assert estimate.confidence == pytest.approx(50.0)
    assert "buy a perceived bottom with the higher timeframe trend" in estimate.explanation


def test_short_bias_against_trend_with_resistance_zone() -> None:
    analyzer = RetailTrapAnalyzer()

    estimate = analyzer.analyze(
        symbol="BTCUSDT",
        higher_timeframe_direction=MarketDirection.BULLISH,
        market_structure_events=[
            make_structure_event(StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH)
        ],
        liquidity_zones=[
            make_zone(
                105.0,
                side=LiquiditySide.BUY_SIDE,
                zone_type=LiquidityZoneType.EQUAL_HIGHS,
                strength=0.5,
            )
        ],
        current_price=CURRENT_PRICE,
    )

    assert estimate.dominant_side == RetailPositioning.SHORT
    assert estimate.confidence == pytest.approx(90.0)
    assert "sell a perceived top against the higher timeframe trend" in estimate.explanation
    assert "equal highs zone acting as perceived resistance" in estimate.explanation


def test_no_structure_events_falls_back_to_higher_timeframe_trend() -> None:
    analyzer = RetailTrapAnalyzer()

    estimate = analyzer.analyze(
        symbol="BTCUSDT",
        higher_timeframe_direction=MarketDirection.BULLISH,
        market_structure_events=[],
        liquidity_zones=[],
        current_price=CURRENT_PRICE,
    )

    assert estimate.dominant_side == RetailPositioning.LONG
    assert estimate.confidence == pytest.approx(20.0)


def test_no_signal_yields_neutral_bias() -> None:
    analyzer = RetailTrapAnalyzer()

    estimate = analyzer.analyze(
        symbol="BTCUSDT",
        higher_timeframe_direction=MarketDirection.NEUTRAL,
        market_structure_events=[],
        liquidity_zones=[],
        current_price=CURRENT_PRICE,
    )

    assert estimate.dominant_side == RetailPositioning.NEUTRAL
    assert "mixed or flat" in estimate.explanation


def test_uses_most_recent_structure_event() -> None:
    analyzer = RetailTrapAnalyzer()

    older = make_structure_event(
        StructureEvent.LOWER_LOW,
        MarketDirection.BEARISH,
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
    )
    newer = make_structure_event(
        StructureEvent.CHANGE_OF_CHARACTER,
        MarketDirection.BULLISH,
        timestamp=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=1),
    )

    estimate = analyzer.analyze(
        symbol="BTCUSDT",
        higher_timeframe_direction=MarketDirection.BEARISH,
        market_structure_events=[older, newer],
        liquidity_zones=[],
        current_price=CURRENT_PRICE,
    )

    assert estimate.dominant_side == RetailPositioning.LONG
    assert estimate.generated_at == newer.timestamp


def test_invalid_current_price_raises() -> None:
    analyzer = RetailTrapAnalyzer()

    with pytest.raises(ValueError, match="current_price"):
        analyzer.analyze(
            symbol="BTCUSDT",
            higher_timeframe_direction=MarketDirection.NEUTRAL,
            market_structure_events=[],
            liquidity_zones=[],
            current_price=0.0,
        )
