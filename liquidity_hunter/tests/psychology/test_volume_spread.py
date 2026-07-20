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
    # Gate disabled: these exercise pattern anatomy, not location.
    vd = volume_delta_series(candles)
    return VolumeSpreadAnalyzer(lookback=7, gate_extreme_lookback=0).analyze(candles, vd)


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


def test_dedup_collapses_a_run_of_same_pattern():
    candles = _baseline(8)
    # A run of 5 adjacent quiet low-volume narrow down-bars — all No Supply.
    for i in range(8, 13):
        candles.append(
            _candle(i, 100.0, 100.3, 99.8, 99.9, volume=30.0, taker_buy_volume=15.0)
        )
    signals = _run(candles)
    no_supply = [s for s in signals if s.pattern == VSAPattern.NO_SUPPLY]
    # Clustered within the lookback window → collapsed, not one per candle.
    assert len(no_supply) == 1


def test_dedup_keeps_distinct_patterns_and_separated_clusters():
    candles = _baseline(8)
    # A climax, then well-separated (> window) so it survives on its own.
    candles.append(
        _candle(8, 100.0, 100.5, 92.0, 98.0, volume=400.0, taker_buy_volume=60.0)
    )
    for i in range(9, 20):
        candles.append(_candle(i, 100.0, 101.0, 99.0, 100.0))
    # A separate up-thrust far past the climax's dedup window.
    candles.append(
        _candle(20, 100.0, 104.0, 99.0, 99.2, volume=160.0, taker_buy_volume=50.0)
    )
    signals = _run(candles)
    kinds = {s.pattern for s in signals}
    assert VSAPattern.SELLING_CLIMAX in kinds
    assert VSAPattern.UP_THRUST in kinds


def test_dedup_keeps_highest_confidence_in_cluster():
    candles = _baseline(8)
    # Two adjacent selling climaxes; the second has more extreme volume.
    candles.append(
        _candle(8, 100.0, 100.5, 92.0, 98.0, volume=300.0, taker_buy_volume=60.0)
    )
    candles.append(
        _candle(9, 98.0, 98.5, 90.0, 96.0, volume=600.0, taker_buy_volume=60.0)
    )
    signals = _run(candles)
    climaxes = [s for s in signals if s.pattern == VSAPattern.SELLING_CLIMAX]
    assert len(climaxes) == 1
    # The kept one is the stronger (higher-volume) second candle.
    assert climaxes[0].timestamp == _ts(9)


def test_gate_suppresses_no_supply_away_from_a_local_low():
    # A No Supply bar sitting above the trailing low (not a support test).
    candles = _baseline(24)  # baseline lows are 99.0
    candles.append(
        _candle(24, 100.0, 100.3, 99.8, 99.9, volume=30.0, taker_buy_volume=15.0)
    )
    vd = volume_delta_series(candles)
    gated = VolumeSpreadAnalyzer(lookback=7, gate_extreme_lookback=20).analyze(candles, vd)
    assert not any(s.pattern == VSAPattern.NO_SUPPLY for s in gated)


def test_gate_keeps_no_supply_at_a_fresh_local_low():
    # Same quiet bar, but now it makes a fresh trailing low → a real test.
    candles = _baseline(24)
    candles.append(
        _candle(24, 98.6, 98.7, 98.2, 98.3, volume=30.0, taker_buy_volume=15.0)
    )
    vd = volume_delta_series(candles)
    gated = VolumeSpreadAnalyzer(lookback=7, gate_extreme_lookback=20).analyze(candles, vd)
    assert any(s.pattern == VSAPattern.NO_SUPPLY for s in gated)


def test_gate_never_suppresses_a_climax():
    # Climax bypasses the location gate entirely (it is rare + self-evident).
    candles = _baseline(24)
    candles.append(
        _candle(24, 100.0, 100.5, 92.0, 98.0, volume=400.0, taker_buy_volume=60.0)
    )
    vd = volume_delta_series(candles)
    gated = VolumeSpreadAnalyzer(lookback=7, gate_extreme_lookback=20).analyze(candles, vd)
    assert any(s.pattern == VSAPattern.SELLING_CLIMAX for s in gated)


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
