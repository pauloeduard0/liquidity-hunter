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


def find_wick_break_index(
    candles: Sequence[Candle],
    start_index: int,
    end_index: int,
    level_price: float,
    *,
    bullish: bool,
) -> int:
    """The first index in `candles[start_index:end_index + 1]` whose wick
    crosses `level_price` (`high > level_price` if `bullish`, else
    `low < level_price`).

    Falls back to `end_index` if none qualifies in range -- the caller has
    already established that `candles[end_index]` itself crosses
    `level_price`.
    """
    for index in range(start_index, end_index + 1):
        candle = candles[index]
        if bullish and candle.high > level_price:
            return index
        if not bullish and candle.low < level_price:
            return index
    return end_index


def find_sustained_break_index(
    candles: Sequence[Candle],
    start_index: int,
    end_index: int,
    level_price: float,
    *,
    bullish: bool,
    persistence_candles: int,
) -> int:
    """The first index in `candles[start_index:end_index + 1]` at which a
    sustained break of `level_price` begins (see `is_sustained_break`).

    Falls back to `end_index` if none qualifies in range -- the caller has
    already established that a sustained break begins at `end_index`.
    """
    for index in range(start_index, end_index + 1):
        if is_sustained_break(
            candles, index, level_price, bullish=bullish, persistence_candles=persistence_candles
        ):
            return index
    return end_index
