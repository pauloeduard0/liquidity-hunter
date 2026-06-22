"""Tests for leverage-liquidation domain entities."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from liquidity_hunter.core.domain import (
    LeverageLiquidationMap,
    LiquidationBand,
    LiquiditySide,
    RetailPositioning,
    TimeFrame,
)

START = datetime(2026, 6, 22, tzinfo=UTC)


def _band(**overrides: object) -> LiquidationBand:
    defaults: dict[str, object] = {
        "price_low": 90.0,
        "price_high": 91.0,
        "leverage": 10,
        "side": LiquiditySide.SELL_SIDE,
        "source_entry_price": 100.0,
        "intensity": 75.0,
        "start_time": START,
        "end_time": None,
    }
    defaults.update(overrides)
    return LiquidationBand(**defaults)


def test_liquidation_band_valid() -> None:
    band = _band()
    assert band.leverage == 10
    assert band.side is LiquiditySide.SELL_SIDE


def test_liquidation_band_rejects_inverted_range() -> None:
    with pytest.raises(ValidationError, match="price_high must be > price_low"):
        _band(price_low=91.0, price_high=90.0)


def test_liquidation_band_rejects_out_of_range_intensity() -> None:
    with pytest.raises(ValidationError):
        _band(intensity=101.0)


def test_liquidation_band_accepts_end_after_start() -> None:
    band = _band(end_time=START + timedelta(hours=3))
    assert band.end_time == START + timedelta(hours=3)


def test_liquidation_band_rejects_end_before_start() -> None:
    with pytest.raises(ValidationError, match="end_time must be >= start_time"):
        _band(end_time=START - timedelta(hours=1))


def test_leverage_liquidation_map_valid() -> None:
    m = LeverageLiquidationMap(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        current_price=105.0,
        dominant_leveraged_side=RetailPositioning.LONG,
        positioning_intensity=0.8,
        funding_rate=0.0005,
        open_interest_change_pct=0.2,
        long_short_ratio=1.85,
        bands=[_band()],
    )
    assert len(m.bands) == 1
    assert m.dominant_leveraged_side is RetailPositioning.LONG


def test_leverage_liquidation_map_rejects_out_of_range_intensity() -> None:
    with pytest.raises(ValidationError):
        LeverageLiquidationMap(
            symbol="BTCUSDT",
            timeframe=TimeFrame.H1,
            current_price=105.0,
            dominant_leveraged_side=RetailPositioning.NEUTRAL,
            positioning_intensity=1.5,
            funding_rate=0.0,
            open_interest_change_pct=0.0,
            long_short_ratio=1.0,
            bands=[],
        )
