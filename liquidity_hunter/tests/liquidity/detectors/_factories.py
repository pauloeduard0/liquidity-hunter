"""Test helpers for building candle series with known swing points."""

from datetime import UTC, datetime, timedelta

from liquidity_hunter.core.domain import Candle, TimeFrame

BASE_TIME = datetime(2024, 1, 1, tzinfo=UTC)


def make_candle(
    index: int,
    high: float,
    low: float,
    symbol: str = "BTCUSDT",
    close: float | None = None,
    taker_buy_volume: float = 0.5,
    volume: float = 1.0,
    timeframe: TimeFrame = TimeFrame.H1,
    interval: timedelta = timedelta(hours=1),
) -> Candle:
    """Build a valid `Candle` with the given high/low and a midpoint open.

    `close` defaults to the high/low midpoint. `taker_buy_volume` defaults
    to half of `volume`, i.e. a neutral (zero) volume delta. `timestamp` is
    `BASE_TIME + interval * index`.
    """
    mid = (high + low) / 2
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=BASE_TIME + interval * index,
        open=mid,
        high=high,
        low=low,
        close=mid if close is None else close,
        volume=volume,
        taker_buy_volume=taker_buy_volume,
    )


def make_series(
    highs: list[float],
    lows: list[float],
    symbol: str = "BTCUSDT",
    timeframe: TimeFrame = TimeFrame.H1,
    interval: timedelta = timedelta(hours=1),
) -> list[Candle]:
    """Build a chronological candle series from parallel high/low lists."""
    if len(highs) != len(lows):
        raise ValueError("highs and lows must have the same length")
    return [
        make_candle(i, high, low, symbol, timeframe=timeframe, interval=interval)
        for i, (high, low) in enumerate(zip(highs, lows, strict=True))
    ]
