"""Internal helpers shared by liquidity zone detectors."""

from collections.abc import Sequence

from liquidity_hunter.core.domain import Candle


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
