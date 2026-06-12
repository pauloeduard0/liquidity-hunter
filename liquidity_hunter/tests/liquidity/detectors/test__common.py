"""Tests for `liquidity_hunter.liquidity.detectors._common`."""

from datetime import timedelta

from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.liquidity.detectors._common import has_volume_spike, is_confirmed_break
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle

M30 = TimeFrame.M30
THIRTY_MIN = timedelta(minutes=30)


def _finer_candles(spike_index: int, *, count: int = 6, normal_volume: float = 1.0) -> list[Candle]:
    """`count` M30 candles, 30 minutes apart, all with `normal_volume` except
    `spike_index`, which has 10x that volume.
    """
    return [
        make_candle(
            i,
            100.0,
            99.0,
            volume=10.0 * normal_volume if i == spike_index else normal_volume,
            timeframe=M30,
            interval=THIRTY_MIN,
        )
        for i in range(count)
    ]


def test_has_volume_spike_detects_spike_within_window() -> None:
    finer_candles = _finer_candles(spike_index=3)

    # Candle 3's timestamp is BASE_TIME + 1h30; an H1 window starting there
    # covers it.
    window_start = finer_candles[3].timestamp
    window_end = window_start + timedelta(hours=1)

    assert has_volume_spike(finer_candles, window_start, window_end, lookback=2, multiplier=1.5)


def test_has_volume_spike_false_when_no_spike_in_window() -> None:
    finer_candles = _finer_candles(spike_index=3)

    # A window that doesn't include candle 3.
    window_start = finer_candles[0].timestamp
    window_end = finer_candles[0].timestamp + timedelta(hours=1)

    assert not has_volume_spike(
        finer_candles, window_start, window_end, lookback=2, multiplier=1.5
    )


def test_has_volume_spike_false_when_below_lookback() -> None:
    """A candle within `lookback` of the start of `finer_candles` has no
    preceding average to compare against, so it can never be a spike."""
    finer_candles = _finer_candles(spike_index=1)

    window_start = finer_candles[0].timestamp
    window_end = finer_candles[1].timestamp + timedelta(minutes=30)

    assert not has_volume_spike(
        finer_candles, window_start, window_end, lookback=2, multiplier=1.5
    )


def test_has_volume_spike_false_when_below_multiplier() -> None:
    finer_candles = _finer_candles(spike_index=3)

    window_start = finer_candles[3].timestamp
    window_end = window_start + timedelta(hours=1)

    # 10x the average clears a 1.5x multiplier but not a 20x one.
    assert not has_volume_spike(
        finer_candles, window_start, window_end, lookback=2, multiplier=20.0
    )


def test_is_confirmed_break_volume_spike_confirms_despite_low_volume_delta_ratio() -> None:
    # close beyond 100, but balanced taker volume (ratio 0) -- normally not confirmed.
    candle = make_candle(0, 105.0, 95.0, close=101.0, taker_buy_volume=0.5)

    assert not is_confirmed_break(candle, 100.0, bullish=True, min_volume_delta_ratio=0.2)
    assert is_confirmed_break(
        candle, 100.0, bullish=True, min_volume_delta_ratio=0.2, volume_spike=True
    )


def test_is_confirmed_break_volume_spike_does_not_bypass_close_beyond() -> None:
    # close does NOT clear 100, regardless of volume_spike.
    candle = make_candle(0, 105.0, 95.0, close=99.0, taker_buy_volume=0.5)

    assert not is_confirmed_break(
        candle, 100.0, bullish=True, min_volume_delta_ratio=0.2, volume_spike=True
    )
