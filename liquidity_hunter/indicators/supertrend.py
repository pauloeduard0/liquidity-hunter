"""Supertrend: ATR-banded trailing trend, a port of the classic Pine study.

Faithful to the widely used TradingView "Supertrend" script (KivancOzbilgic
lineage): bands at ``hl2 ± multiplier × ATR``, each ratcheting in the trend's
direction only, and a trend flip when a close crosses the *previous* candle's
opposing band.
"""

from collections.abc import Sequence

from liquidity_hunter.core.domain import Candle, MarketDirection, SupertrendPoint

DEFAULT_PERIODS = 10
DEFAULT_MULTIPLIER = 3.0


def true_range_series(candles: Sequence[Candle]) -> list[float]:
    """True range per candle, 1:1 aligned with `candles`.

    The first candle has no previous close, so its true range is its own
    high-low spread (Pine's `tr` behaves the same way).
    """
    ranges: list[float] = []
    for index, candle in enumerate(candles):
        spread = candle.high - candle.low
        if index == 0:
            ranges.append(spread)
            continue
        prev_close = candles[index - 1].close
        ranges.append(
            max(spread, abs(candle.high - prev_close), abs(candle.low - prev_close))
        )
    return ranges


def _rma(values: Sequence[float], length: int) -> list[float | None]:
    """Wilder's smoothing (Pine's `rma`), seeded with the first SMA.

    Elements before the seed are `None` (Pine's `na`).
    """
    out: list[float | None] = [None] * len(values)
    if len(values) < length:
        return out
    seed = sum(values[:length]) / length
    out[length - 1] = seed
    running = seed
    for index in range(length, len(values)):
        running = (running * (length - 1) + values[index]) / length
        out[index] = running
    return out


def _sma(values: Sequence[float], length: int) -> list[float | None]:
    """Simple moving average, `None` until `length` values are available."""
    out: list[float | None] = [None] * len(values)
    if len(values) < length:
        return out
    window = sum(values[:length])
    out[length - 1] = window / length
    for index in range(length, len(values)):
        window += values[index] - values[index - length]
        out[index] = window / length
    return out


def supertrend(
    candles: Sequence[Candle],
    *,
    periods: int = DEFAULT_PERIODS,
    multiplier: float = DEFAULT_MULTIPLIER,
    change_atr: bool = True,
) -> list[SupertrendPoint]:
    """Supertrend readings for `candles`, one point per candle with an ATR.

    Parameters mirror the Pine study's inputs: `periods` (ATR length),
    `multiplier` (band width in ATRs), and `change_atr` — `True` uses Wilder's
    ATR (Pine's `atr()`), `False` the simple mean of true range (`sma(tr, n)`),
    the script's "Change ATR Calculation Method?" toggle.

    The returned list starts at the first candle whose ATR is defined (index
    `periods - 1`), so it is shorter than `candles`; each point carries its own
    timestamp. An empty list is returned when the series is too short.
    """
    if periods <= 0:
        raise ValueError("periods must be positive")
    if len(candles) < periods:
        return []

    ranges = true_range_series(candles)
    atr = _rma(ranges, periods) if change_atr else _sma(ranges, periods)

    points: list[SupertrendPoint] = []
    prev_upper: float | None = None
    prev_lower: float | None = None
    trend = MarketDirection.BULLISH  # the script starts with `trend = 1`

    for index, candle in enumerate(candles):
        atr_value = atr[index]
        if atr_value is None:
            continue

        source = (candle.high + candle.low) / 2
        lower = source - multiplier * atr_value
        upper = source + multiplier * atr_value

        # Each band only ratchets toward price while price holds on its side:
        # the floor rises while the previous close stayed above it, the ceiling
        # falls while the previous close stayed below it.
        prev_close = candles[index - 1].close if index > 0 else candle.close
        if prev_lower is not None and prev_close > prev_lower:
            lower = max(lower, prev_lower)
        if prev_upper is not None and prev_close < prev_upper:
            upper = min(upper, prev_upper)

        # The flip is judged against the *previous* candle's bands, as in Pine
        # (`up1`/`dn1`); on the first reading there are none, so the trend
        # keeps its seed.
        previous_trend = trend
        if prev_upper is not None and prev_lower is not None:
            if trend is MarketDirection.BEARISH and candle.close > prev_upper:
                trend = MarketDirection.BULLISH
            elif trend is MarketDirection.BULLISH and candle.close < prev_lower:
                trend = MarketDirection.BEARISH

        points.append(
            SupertrendPoint(
                timestamp=candle.timestamp,
                value=lower if trend is MarketDirection.BULLISH else upper,
                direction=trend,
                flip=bool(points) and trend is not previous_trend,
                upper_band=upper,
                lower_band=lower,
            )
        )

        prev_upper = upper
        prev_lower = lower

    return points
