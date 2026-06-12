"""Construction and validation tests for core domain entities."""

from datetime import datetime, timezone

import pytest

from liquidity_hunter.core.domain import (
    BiasSource,
    Candle,
    LiquiditySide,
    LiquidityZone,
    LiquidityZoneType,
    MarketDirection,
    MarketStructure,
    RetailBias,
    StructureEvent,
    StructureScope,
    TimeFrame,
)

NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_candle_valid_construction() -> None:
    candle = Candle(
        symbol="EURUSD",
        timeframe=TimeFrame.H1,
        timestamp=NOW,
        open=1.10,
        high=1.12,
        low=1.09,
        close=1.11,
        volume=1000,
        taker_buy_volume=600,
    )
    assert candle.high >= candle.open
    assert candle.low <= candle.close


def test_candle_rejects_inconsistent_high() -> None:
    with pytest.raises(ValueError):
        Candle(
            symbol="EURUSD",
            timeframe=TimeFrame.H1,
            timestamp=NOW,
            open=1.10,
            high=1.05,  # lower than open/close/low -> invalid
            low=1.04,
            close=1.11,
            volume=1000,
            taker_buy_volume=600,
        )


def test_candle_rejects_taker_buy_volume_exceeding_volume() -> None:
    with pytest.raises(ValueError, match="taker_buy_volume must be <= volume"):
        Candle(
            symbol="EURUSD",
            timeframe=TimeFrame.H1,
            timestamp=NOW,
            open=1.10,
            high=1.12,
            low=1.09,
            close=1.11,
            volume=1000,
            taker_buy_volume=1001,
        )


def test_liquidity_zone_valid_construction() -> None:
    zone = LiquidityZone(
        symbol="EURUSD",
        timeframe=TimeFrame.H4,
        zone_type=LiquidityZoneType.EQUAL_HIGHS,
        side=LiquiditySide.SELL_SIDE,
        price_high=1.12,
        price_low=1.118,
        formed_at=NOW,
        strength=0.75,
    )
    assert zone.price_high >= zone.price_low
    assert not zone.is_mitigated


def test_liquidity_zone_rejects_inverted_range() -> None:
    with pytest.raises(ValueError):
        LiquidityZone(
            symbol="EURUSD",
            timeframe=TimeFrame.H4,
            zone_type=LiquidityZoneType.EQUAL_LOWS,
            side=LiquiditySide.BUY_SIDE,
            price_high=1.10,
            price_low=1.12,
            formed_at=NOW,
        )


def test_market_structure_valid_construction() -> None:
    structure = MarketStructure(
        symbol="EURUSD",
        timeframe=TimeFrame.D1,
        timestamp=NOW,
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=MarketDirection.BULLISH,
        price_level=1.15,
    )
    assert structure.direction is MarketDirection.BULLISH
    assert structure.scope is StructureScope.MAJOR


def test_market_structure_accepts_internal_scope() -> None:
    structure = MarketStructure(
        symbol="EURUSD",
        timeframe=TimeFrame.D1,
        timestamp=NOW,
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=MarketDirection.BULLISH,
        price_level=1.15,
        scope=StructureScope.INTERNAL,
    )
    assert structure.scope is StructureScope.INTERNAL


def test_retail_bias_valid_construction() -> None:
    bias = RetailBias(
        symbol="EURUSD",
        timestamp=NOW,
        source=BiasSource.RETAIL_POSITIONING,
        direction=MarketDirection.BEARISH,
        sentiment_score=-0.4,
        confidence=0.8,
    )
    assert -1.0 <= bias.sentiment_score <= 1.0
    assert 0.0 <= bias.confidence <= 1.0
