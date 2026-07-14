"""Tests for the `ConsolidationRange` domain entity."""

from datetime import UTC, datetime

import pytest

from liquidity_hunter.core.domain import (
    ConsolidationRange,
    ConsolidationStatus,
    MarketDirection,
    TimeFrame,
)

_START = datetime(2026, 7, 4, 18, tzinfo=UTC)
_END = datetime(2026, 7, 10, tzinfo=UTC)


def test_active_range_valid_construction() -> None:
    r = ConsolidationRange(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        start_timestamp=_START,
        price_low=61297.0,
        price_high=64691.9,
        candle_count=127,
    )
    assert r.status is ConsolidationStatus.ACTIVE
    assert r.end_timestamp is None
    assert r.resolved_direction is None


def test_resolved_range_requires_end_and_direction() -> None:
    with pytest.raises(ValueError, match="RESOLVED"):
        ConsolidationRange(
            symbol="BTCUSDT",
            timeframe=TimeFrame.H1,
            start_timestamp=_START,
            price_low=61297.0,
            price_high=64691.9,
            status=ConsolidationStatus.RESOLVED,
            candle_count=127,
        )


def test_active_range_must_not_carry_resolution_fields() -> None:
    with pytest.raises(ValueError, match="ACTIVE"):
        ConsolidationRange(
            symbol="BTCUSDT",
            timeframe=TimeFrame.H1,
            start_timestamp=_START,
            end_timestamp=_END,
            price_low=61297.0,
            price_high=64691.9,
            candle_count=127,
        )


def test_end_must_not_precede_start() -> None:
    with pytest.raises(ValueError, match="precede"):
        ConsolidationRange(
            symbol="BTCUSDT",
            timeframe=TimeFrame.H1,
            start_timestamp=_END,
            end_timestamp=_START,
            price_low=61297.0,
            price_high=64691.9,
            status=ConsolidationStatus.RESOLVED,
            resolved_direction=MarketDirection.BULLISH,
            candle_count=127,
        )


def test_price_high_must_exceed_price_low() -> None:
    with pytest.raises(ValueError, match="price_high"):
        ConsolidationRange(
            symbol="BTCUSDT",
            timeframe=TimeFrame.H1,
            start_timestamp=_START,
            price_low=64691.9,
            price_high=61297.0,
            candle_count=127,
        )
