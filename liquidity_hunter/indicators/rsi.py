"""Relative Strength Index — the momentum oscillator, Wilder's original.

Where an EMA reports *where* price has been recently, the RSI reports *how*
it got there: the average up-move divided by the total average move over a
window, rescaled to 0-100. It says nothing about level and everything about
the balance between the two sides of the tape over the last `period` candles.

Like the EMA and the session VWAP, its value here is that it is a Schelling
point — period 14 on the close, computed identically by everyone who looks —
and not that 30 and 70 mean anything on their own. This project has already
measured that a widely watched line concentrates reactions
(`docs/block_reclaim.md`) and that a level nobody agrees on does not.

Descriptive only. A series, not an instruction.
"""

from __future__ import annotations

from collections.abc import Sequence

from liquidity_hunter.core.domain.candle import Candle

#: Wilder's period, and the one every charting package defaults to.
DEFAULT_PERIOD = 14


def rsi(values: Sequence[float], period: int = DEFAULT_PERIOD) -> list[float | None]:
    """The RSI of ``values``, 1:1 aligned with the input.

    Uses Wilder's smoothing (an EMA with ``alpha = 1/period``), seeded with
    the simple mean of the first ``period`` changes — the convention Pine and
    TradingView follow, so a value read here matches the value the user reads
    on the chart. Entries before the seed are ``None`` rather than a number
    the series cannot yet support.

    A window with no down-move at all gives 100 (and none up gives 0): the
    ratio is undefined there, and 100 is the limit the formula approaches
    rather than a special case invented for it.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gains = [max(0.0, values[i] - values[i - 1]) for i in range(1, len(values))]
    losses = [max(0.0, values[i - 1] - values[i]) for i in range(1, len(values))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def _value(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    out[period] = _value(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        out[i] = _value(avg_gain, avg_loss)
    return out


def rsi_series(
    candles: Sequence[Candle], period: int = DEFAULT_PERIOD
) -> list[float | None]:
    """The RSI of the candles' closes, 1:1 aligned with ``candles``."""
    return rsi([c.close for c in candles], period)
