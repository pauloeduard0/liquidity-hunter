"""Tests for ``VolumeSpreadAnalyzer``."""

from datetime import UTC, datetime, timedelta

from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    TimeFrame,
    VSAPattern,
)
from liquidity_hunter.indicators import volume_delta_series
from liquidity_hunter.psychology import VolumeSpreadAnalyzer

SYMBOL = "BTCUSDT"
TF = TimeFrame.H1
T0 = datetime(2024, 6, 1, tzinfo=UTC)


def _ts(hours: int) -> datetime:
    return T0 + timedelta(hours=hours)


def _candle(
    hour: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100.0,
    taker_buy_volume: float = 50.0,
) -> Candle:
    return Candle(
        symbol=SYMBOL,
        timeframe=TF,
        timestamp=_ts(hour),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        taker_buy_volume=taker_buy_volume,
    )


def _baseline(n: int) -> list[Candle]:
    """`n` unremarkable candles: spread 2, volume 100, balanced delta."""
    return [
        _candle(i, 100.0, 101.0, 99.0, 100.0, volume=100.0, taker_buy_volume=50.0)
        for i in range(n)
    ]


def _run(candles: list[Candle]) -> list:
    vd = volume_delta_series(candles)
    return VolumeSpreadAnalyzer(lookback=7).analyze(candles, vd)


def test_empty_and_short_series_return_nothing():
    assert VolumeSpreadAnalyzer().analyze([], []) == []
    assert _run(_baseline(2)) == []


def test_flat_baseline_produces_no_signals():
    # Every candle equals the baseline mean → no spread/volume anomaly.
    assert _run(_baseline(12)) == []


def test_selling_climax_detected():
    candles = _baseline(8)
    # Wide down-bar, extreme volume, long lower wick, close recovers off low.
    candles.append(
        _candle(8, 100.0, 100.5, 92.0, 98.0, volume=400.0, taker_buy_volume=60.0)
    )
    signals = _run(candles)
    kinds = {s.pattern for s in signals}
    assert VSAPattern.SELLING_CLIMAX in kinds
    climax = next(s for s in signals if s.pattern == VSAPattern.SELLING_CLIMAX)
    assert climax.direction == MarketDirection.BULLISH
    assert climax.timestamp == _ts(8)


def test_buying_climax_detected():
    candles = _baseline(8)
    # Wide up-bar, extreme volume, long upper wick, close fades off high.
    candles.append(
        _candle(8, 100.0, 108.0, 99.5, 102.0, volume=400.0, taker_buy_volume=250.0)
    )
    signals = _run(candles)
    climax = next(s for s in signals if s.pattern == VSAPattern.BUYING_CLIMAX)
    assert climax.direction == MarketDirection.BEARISH


def test_down_thrust_bullish_pin_detected():
    candles = _baseline(8)
    # Lower-wick rejection, close near the high, above-average volume.
    candles.append(
        _candle(8, 100.0, 101.0, 96.0, 100.8, volume=160.0, taker_buy_volume=110.0)
    )
    signals = _run(candles)
    thrust = next(s for s in signals if s.pattern == VSAPattern.DOWN_THRUST)
    assert thrust.direction == MarketDirection.BULLISH


def test_up_thrust_bearish_pin_detected():
    candles = _baseline(8)
    # Upper-wick rejection, close near the low, above-average volume.
    candles.append(
        _candle(8, 100.0, 104.0, 99.0, 99.2, volume=160.0, taker_buy_volume=50.0)
    )
    signals = _run(candles)
    thrust = next(s for s in signals if s.pattern == VSAPattern.UP_THRUST)
    assert thrust.direction == MarketDirection.BEARISH


def test_no_supply_detected():
    candles = _baseline(8)
    # Narrow down-bar on very low volume — sellers absent.
    candles.append(
        _candle(8, 100.0, 100.3, 99.8, 99.9, volume=30.0, taker_buy_volume=15.0)
    )
    signals = _run(candles)
    ns = next(s for s in signals if s.pattern == VSAPattern.NO_SUPPLY)
    assert ns.direction == MarketDirection.BULLISH


def test_no_demand_detected():
    candles = _baseline(8)
    # Narrow up-bar on very low volume — buyers absent.
    candles.append(
        _candle(8, 100.0, 100.2, 99.7, 100.1, volume=30.0, taker_buy_volume=15.0)
    )
    signals = _run(candles)
    nd = next(s for s in signals if s.pattern == VSAPattern.NO_DEMAND)
    assert nd.direction == MarketDirection.BEARISH


def test_signal_fields_are_populated():
    candles = _baseline(8)
    candles.append(
        _candle(8, 100.0, 100.5, 92.0, 98.0, volume=400.0, taker_buy_volume=60.0)
    )
    signal = _run(candles)[0]
    assert signal.symbol == SYMBOL
    assert signal.timeframe == TF
    assert 0.0 <= signal.close_position <= 1.0
    assert signal.spread_ratio > 1.0
    assert signal.volume_ratio > 1.0
    assert 0.0 <= signal.confidence <= 100.0
    assert signal.description
