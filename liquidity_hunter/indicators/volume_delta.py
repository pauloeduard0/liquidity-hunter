"""Volume delta: per-candle taker buy/sell aggression imbalance."""

import itertools
from collections.abc import Sequence

from liquidity_hunter.core.domain import Candle


def volume_delta(candle: Candle) -> float:
    """Net taker aggression for `candle`.

    `2 * taker_buy_volume - volume` is positive when takers bought more
    than they sold (net buy aggression) and negative when they sold more
    (net sell aggression), ranging from `-volume` (all taker sells) to
    `+volume` (all taker buys).
    """
    return 2 * candle.taker_buy_volume - candle.volume


def volume_delta_series(candles: Sequence[Candle]) -> list[float]:
    """`volume_delta` for each candle in `candles`, in the same order."""
    return [volume_delta(candle) for candle in candles]


def cumulative_volume_delta(candles: Sequence[Candle]) -> list[float]:
    """Running sum of `volume_delta` over `candles` (the CVD series).

    Cumulative Volume Delta tracks the *accumulated* net taker aggression: a
    rising CVD means buyers have been the aggressors over the run, a falling
    CVD means sellers. It is 1:1 aligned with `candles`; the first element is
    the first candle's own delta. Divergences between the CVD slope and price
    reveal who is actually initiating (hitting bid/ask) versus who is merely
    moving price passively.
    """
    return list(itertools.accumulate(volume_delta(candle) for candle in candles))
