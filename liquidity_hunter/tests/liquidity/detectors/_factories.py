"""Test helpers for building candle series with known swing points."""

from datetime import UTC, datetime, timedelta

from liquidity_hunter.core.domain import Candle, TimeFrame

BASE_TIME = datetime(2024, 1, 1, tzinfo=UTC)


def make_candle(
    index: int,
    high: float,
    low: float,
    symbol: str = "BTCUSDT",
    taker_buy_volume: float = 0.5,
) -> Candle:
    """Build a valid `Candle` with the given high/low and a midpoint open/close.

    `taker_buy_volume` defaults to half of `volume` (1.0), i.e. a neutral
    (zero) volume delta.
    """
    mid = (high + low) / 2
    return Candle(
        symbol=symbol,
        timeframe=TimeFrame.H1,
        timestamp=BASE_TIME + timedelta(hours=index),
        open=mid,
        high=high,
        low=low,
        close=mid,
        volume=1.0,
        taker_buy_volume=taker_buy_volume,
    )


def make_series(highs: list[float], lows: list[float], symbol: str = "BTCUSDT") -> list[Candle]:
    """Build a chronological candle series from parallel high/low lists."""
    if len(highs) != len(lows):
        raise ValueError("highs and lows must have the same length")
    return [
        make_candle(i, high, low, symbol)
        for i, (high, low) in enumerate(zip(highs, lows, strict=True))
    ]
