"""Internal helpers shared by liquidity zone and market structure detectors."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from liquidity_hunter.core.domain import Candle
from liquidity_hunter.indicators import volume_delta
from liquidity_hunter.liquidity.detectors.base import LiquidityZoneDetector


def validate_candles(candles: Sequence[Candle]) -> None:
    """Ensure `candles` is non-empty and shares one symbol/timeframe."""
    if not candles:
        raise ValueError("candles must not be empty")

    symbol = candles[0].symbol
    timeframe = candles[0].timeframe
    for candle in candles:
        if candle.symbol != symbol or candle.timeframe != timeframe:
            raise ValueError("all candles must share the same symbol and timeframe")


def price_range(candles: Sequence[Candle]) -> float:
    """The full high/low range spanned by `candles`, used to normalize strength scores."""
    return max(c.high for c in candles) - min(c.low for c in candles)


@dataclass(frozen=True)
class Pivot:
    """A single swing high or low pivot: its price and formation timestamp."""

    price: float
    timestamp: datetime


def collect_pivots(
    candles: list[Candle],
    high_detector: LiquidityZoneDetector,
    low_detector: LiquidityZoneDetector,
) -> list[tuple[datetime, str, float]]:
    """Swing high/low pivots from `candles`, chronologically sorted.

    Each entry is `(formed_at, "high" | "low", price)`.
    """
    highs = high_detector.detect(candles)
    lows = low_detector.detect(candles)
    return sorted(
        [(zone.formed_at, "high", zone.price_high) for zone in highs]
        + [(zone.formed_at, "low", zone.price_low) for zone in lows],
        key=lambda pivot: pivot[0],
    )


def is_confirmed_break(
    candle: Candle,
    active_price: float,
    *,
    bullish: bool,
    min_volume_delta_ratio: float,
    volume_spike: bool = False,
) -> bool:
    """Whether `candle` confirms a counter-trend break of `active_price`.

    Requires `candle.close` to be beyond `active_price` (not just a wick) and
    either `volume_delta(candle)` to be at least `min_volume_delta_ratio` of
    `candle.volume` in the breakout direction (`bullish`), or `volume_spike`
    to be `True` -- a finer-timeframe volume spike observed during `candle`
    (see `has_volume_spike`), an alternative way to confirm a break whose
    `volume_delta` is inconclusive at this timeframe.
    """
    close_beyond = candle.close > active_price if bullish else candle.close < active_price
    if not close_beyond:
        return False
    if volume_spike:
        return True
    if candle.volume == 0:
        return False

    delta = volume_delta(candle)
    delta_in_direction = delta > 0 if bullish else delta < 0
    delta_ratio = abs(delta) / candle.volume
    return delta_in_direction and delta_ratio >= min_volume_delta_ratio


def has_volume_spike(
    finer_candles: Sequence[Candle],
    window_start: datetime,
    window_end: datetime,
    *,
    lookback: int,
    multiplier: float,
) -> bool:
    """Whether any candle in `finer_candles` within `[window_start, window_end)`
    has a volume spike: `volume >= multiplier * average volume of the
    `lookback` finer candles immediately preceding it`.

    `finer_candles` must be in chronological order. Used to confirm a break
    on a coarser timeframe via a volume spike on a finer one, when that
    coarser candle's own `volume_delta` is inconclusive.
    """
    for index, candle in enumerate(finer_candles):
        if not (window_start <= candle.timestamp < window_end):
            continue
        if index < lookback:
            continue
        preceding = finer_candles[index - lookback : index]
        average_volume = sum(c.volume for c in preceding) / lookback
        if average_volume > 0 and candle.volume >= multiplier * average_volume:
            return True
    return False
