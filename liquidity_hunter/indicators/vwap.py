"""VWAP: the average price paid since an anchor, weighted by volume.

Each candle contributes its typical price ``(high + low + close) / 3``
weighted by its volume; the reading at any candle is the running
``Σ(volume × typical) / Σ(volume)`` from the anchor. The bands are the
volume-weighted standard deviation of the typical prices around that average,
i.e. how widely the accumulation was spread, not a channel drawn by hand.

See `core.domain.vwap` for what the reading means and the fidelity a
kline-sourced VWAP achieves.
"""

import math
from collections.abc import Sequence
from datetime import datetime

from liquidity_hunter.core.domain import (
    Candle,
    TimeFrame,
    VWAPAnchor,
    VWAPPoint,
    VWAPSeries,
)

#: Standard-deviation multiples the bands are drawn at, the convention of the
#: classic anchored-VWAP studies (~68% and ~95% of a normal accumulation).
DEFAULT_BAND_MULTIPLIERS = (1.0, 2.0)


def typical_price(candle: Candle) -> float:
    """The price a candle's whole volume is attributed to (``hlc3``)."""
    return (candle.high + candle.low + candle.close) / 3


def _anchor_key(timestamp: datetime, anchor: VWAPAnchor) -> object:
    """The calendar bucket a candle belongs to, for the periodic anchors.

    A change in this key starts a fresh accumulation. Crypto has no exchange
    session, so the day boundary is 00:00 UTC — the same rollover Binance uses
    for its daily candle, which is what makes a "session VWAP" here line up
    with the daily bar a reader sees.
    """
    if anchor is VWAPAnchor.SESSION:
        return timestamp.date()
    if anchor is VWAPAnchor.WEEK:
        return timestamp.isocalendar()[:2]
    if anchor is VWAPAnchor.MONTH:
        return (timestamp.year, timestamp.month)
    return None


def _bands(
    value: float,
    sum_weight: float,
    sum_weighted_square: float,
    multipliers: Sequence[float],
) -> tuple[float | None, float | None, float | None, float | None]:
    """Volume-weighted standard-deviation bands around a running average.

    The variance comes from the accumulated first and second moments
    (``E[p²] − E[p]²``), so it costs no re-scan of the window. Floating-point
    cancellation can push a genuinely zero variance slightly negative, hence
    the clamp.
    """
    variance = max(sum_weighted_square / sum_weight - value * value, 0.0)
    if variance <= 0.0:
        return (None, None, None, None)
    deviation = math.sqrt(variance)
    first = multipliers[0] * deviation if len(multipliers) > 0 else None
    second = multipliers[1] * deviation if len(multipliers) > 1 else None
    return (
        value + first if first is not None else None,
        value - first if first is not None else None,
        value + second if second is not None else None,
        value - second if second is not None else None,
    )


def vwap(
    candles: Sequence[Candle],
    *,
    symbol: str,
    timeframe: TimeFrame,
    anchor: VWAPAnchor = VWAPAnchor.SESSION,
    anchor_timestamp: datetime | None = None,
    rolling_window: int | None = None,
    band_multipliers: Sequence[float] = DEFAULT_BAND_MULTIPLIERS,
    label: str = "",
) -> VWAPSeries | None:
    """VWAP readings over `candles`, one point per candle with volume behind it.

    `anchor` selects what the accumulation restarts on:

    - ``SESSION``/``WEEK``/``MONTH`` — a fresh accumulation at each calendar
      period (00:00 UTC boundaries), so the series holds several segments,
      delimited by each point's ``anchor_timestamp``.
    - ``EVENT`` — one accumulation starting at `anchor_timestamp` (required):
      the anchored VWAP of whatever that timestamp marks.
    - ``ROLLING`` — a trailing window of `rolling_window` candles (required),
      defined only once the window is full.

    Returns ``None`` when no reading is defined (an empty series, an anchor
    past the last candle, or a window with no traded volume) rather than
    raising — the caller renders nothing in that case.
    """
    multipliers = list(band_multipliers)
    if any(multiplier <= 0 for multiplier in multipliers):
        raise ValueError("band multipliers must be positive")

    if anchor is VWAPAnchor.ROLLING:
        points = _rolling_points(candles, rolling_window, multipliers)
    elif anchor is VWAPAnchor.EVENT:
        if anchor_timestamp is None:
            raise ValueError("an EVENT-anchored VWAP needs an anchor_timestamp")
        window = [c for c in candles if c.timestamp >= anchor_timestamp]
        points = _accumulated_points(window, anchor, multipliers)
    else:
        points = _accumulated_points(candles, anchor, multipliers)

    if not points:
        return None
    return VWAPSeries(
        symbol=symbol,
        timeframe=timeframe,
        anchor=anchor,
        anchor_timestamp=points[-1].anchor_timestamp,
        label=label,
        band_multipliers=multipliers,
        points=points,
    )


def anchored_vwap(
    candles: Sequence[Candle],
    anchor_timestamp: datetime,
    *,
    symbol: str,
    timeframe: TimeFrame,
    label: str = "",
    band_multipliers: Sequence[float] = DEFAULT_BAND_MULTIPLIERS,
) -> VWAPSeries | None:
    """VWAP accumulated from one point in time — the anchored VWAP.

    Convenience wrapper over `vwap` with ``VWAPAnchor.EVENT``, for anchoring at
    an observation the platform already made (a sweep, a CHoCH, a range's
    breakout): the resulting line is the average price paid by everyone who
    traded since that event.
    """
    return vwap(
        candles,
        symbol=symbol,
        timeframe=timeframe,
        anchor=VWAPAnchor.EVENT,
        anchor_timestamp=anchor_timestamp,
        band_multipliers=band_multipliers,
        label=label,
    )


def _accumulated_points(
    candles: Sequence[Candle],
    anchor: VWAPAnchor,
    multipliers: Sequence[float],
) -> list[VWAPPoint]:
    """Running VWAP, restarting whenever the anchor's calendar bucket changes."""
    points: list[VWAPPoint] = []
    sum_weight = 0.0
    sum_weighted_price = 0.0
    sum_weighted_square = 0.0
    current_key: object = object()  # a sentinel no key can equal
    current_anchor: datetime | None = None

    for candle in candles:
        key = _anchor_key(candle.timestamp, anchor)
        if current_anchor is None or key != current_key:
            current_key = key
            current_anchor = candle.timestamp
            sum_weight = sum_weighted_price = sum_weighted_square = 0.0

        price = typical_price(candle)
        sum_weight += candle.volume
        sum_weighted_price += candle.volume * price
        sum_weighted_square += candle.volume * price * price
        # Until something has traded, the accumulation has no average to
        # report. Skipping is honest: a zero-volume candle at an anchor moves
        # nobody's break-even.
        if sum_weight <= 0.0:
            continue

        value = sum_weighted_price / sum_weight
        upper_1, lower_1, upper_2, lower_2 = _bands(
            value, sum_weight, sum_weighted_square, multipliers
        )
        points.append(
            VWAPPoint(
                timestamp=candle.timestamp,
                anchor_timestamp=current_anchor,
                value=value,
                upper_1=upper_1,
                lower_1=lower_1,
                upper_2=upper_2,
                lower_2=lower_2,
            )
        )
    return points


def _rolling_points(
    candles: Sequence[Candle],
    window: int | None,
    multipliers: Sequence[float],
) -> list[VWAPPoint]:
    """VWAP over a trailing fixed-length window, defined once it is full."""
    if window is None or window <= 0:
        raise ValueError("a ROLLING VWAP needs a positive rolling_window")
    if len(candles) < window:
        return []

    points: list[VWAPPoint] = []
    sum_weight = 0.0
    sum_weighted_price = 0.0
    sum_weighted_square = 0.0

    for index, candle in enumerate(candles):
        price = typical_price(candle)
        sum_weight += candle.volume
        sum_weighted_price += candle.volume * price
        sum_weighted_square += candle.volume * price * price
        if index >= window:
            stale = candles[index - window]
            stale_price = typical_price(stale)
            sum_weight -= stale.volume
            sum_weighted_price -= stale.volume * stale_price
            sum_weighted_square -= stale.volume * stale_price * stale_price
        if index < window - 1 or sum_weight <= 0.0:
            continue

        value = sum_weighted_price / sum_weight
        upper_1, lower_1, upper_2, lower_2 = _bands(
            value, sum_weight, sum_weighted_square, multipliers
        )
        points.append(
            VWAPPoint(
                timestamp=candle.timestamp,
                anchor_timestamp=candles[index - window + 1].timestamp,
                value=value,
                upper_1=upper_1,
                lower_1=lower_1,
                upper_2=upper_2,
                lower_2=lower_2,
            )
        )
    return points
