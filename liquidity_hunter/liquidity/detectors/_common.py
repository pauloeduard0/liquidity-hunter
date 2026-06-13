"""Internal helpers shared by liquidity zone and market structure detectors."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from liquidity_hunter.core.domain import Candle
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


def is_sustained_break(
    candles: Sequence[Candle],
    pivot_index: int,
    active_price: float,
    *,
    bullish: bool,
    persistence_candles: int,
) -> bool:
    """Whether the break of `active_price` at `candles[pivot_index]` holds.

    True if `candles[pivot_index]` and the `persistence_candles` candles
    immediately following it all close beyond `active_price` in the
    `bullish` direction -- i.e. price did not immediately revert across the
    level (a "false break"). Returns `False` if there are not yet enough
    candles after `pivot_index` to evaluate the persistence window.
    """
    window_end = pivot_index + 1 + persistence_candles
    if window_end > len(candles):
        return False
    window = candles[pivot_index:window_end]
    if bullish:
        return all(candle.close > active_price for candle in window)
    return all(candle.close < active_price for candle in window)
