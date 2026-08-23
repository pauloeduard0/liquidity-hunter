"""Exponential moving average — the fastest of the shared reference lines.

Where a VWAP reports what a population *paid* since an anchor, an EMA reports
where price has been *recently*, weighted so the last candles dominate. It
carries no volume and no anchor: it is a smoothing of the close series and
nothing more.

The short periods (9, 21) are watched by enough participants to be Schelling
points in the sense `docs/block_reclaim.md` establishes for the session VWAP —
widely observed, computed identically by everyone, and therefore a place where
reactions concentrate regardless of whether anyone is positioned there.

Descriptive only. A line, not an instruction.
"""

from __future__ import annotations

from collections.abc import Sequence

from liquidity_hunter.core.domain.candle import Candle

#: The conventional fast period, and the one this project measures against.
DEFAULT_PERIOD = 9


def ema(values: Sequence[float], period: int = DEFAULT_PERIOD) -> list[float | None]:
    """The EMA of ``values``, 1:1 aligned with the input.

    Seeded with the simple mean of the first ``period`` values (Wilder's and
    Pine's convention), so entries before that index are ``None`` rather than a
    number the series cannot yet support. Returning ``None`` rather than
    back-filling matters here: a consumer that reads a warm-up value as a level
    is reading the first candle's close dressed up as an average.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    k = 2.0 / (period + 1)
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def ema_series(
    candles: Sequence[Candle], period: int = DEFAULT_PERIOD
) -> list[float | None]:
    """The EMA of the candles' closes, 1:1 aligned with ``candles``."""
    return ema([c.close for c in candles], period)
