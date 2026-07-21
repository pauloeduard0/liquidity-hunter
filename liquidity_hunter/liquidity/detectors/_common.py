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


@dataclass(frozen=True)
class RangeReset:
    """A consolidation-range reference re-seed directive (the phase-3 cycle reset).

    Emitted by the consolidation scanner (`liquidity.detectors.consolidation`)
    the moment a range *confirms* -- and again whenever an absorbed candle
    expands a boundary -- carrying the box **as known at that candle** (never
    the final box, which would be lookahead). Consumed by
    `InternalStructureDetector.detect(range_resets=...)` on a second pass:
    at `candle_index` the box boundaries become the machine's live structural
    references (counter-trend CHoCH reference at the opposite boundary, BOS
    staircase and reported floor at the with-trend boundary), so a sustained
    boundary break is a *real* machine event instead of a level pinned far
    outside the box. The `*_formed_timestamp` fields are the candles whose
    extremes formed each boundary, anchoring the drawn reference lines.
    """

    candle_index: int
    price_low: float
    price_high: float
    low_formed_timestamp: datetime
    high_formed_timestamp: datetime


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


def find_close_break_index(
    candles: Sequence[Candle],
    start_index: int,
    end_index: int,
    level_price: float,
    *,
    bullish: bool,
) -> int | None:
    """The first index in `candles[start_index:end_index + 1]` where the
    candle's close crosses `level_price` (`close > level_price` if `bullish`,
    else `close < level_price`).

    Returns `None` if no candle in the range closes beyond the level -- a
    wick-only break that immediately reverted without a confirming close.
    """
    for index in range(start_index, end_index + 1):
        candle = candles[index]
        if bullish and candle.close > level_price:
            return index
        if not bullish and candle.close < level_price:
            return index
    return None


def resolve_break_origin_timestamp(
    candles: Sequence[Candle],
    break_index: int,
    level: float,
    *,
    bearish: bool,
) -> datetime | None:
    """Timestamp of the candle that formed `level`, for a BOS line's start anchor.

    A BOS line is drawn from the origin of the level it broke to where it broke
    it. That origin is the swing extreme that made `level`. Scans the candles
    *before* ``break_index`` most-recent-first:

    1. an exact match on the level's own side -- ``low`` for a bearish floor,
       ``high`` for a bullish one (a continuation floor is the prior BOS extreme
       on that side);
    2. failing that, the *opposite* side -- the first BOS of a leg is seeded from
       the reversal's extreme (a bearish leg's floor is the reversal *top*, a
       high), so its origin is the opposite polarity;
    3. failing that, any candle whose range straddles the level -- the last
       resort so a real level always resolves to where price last held it.

    Purely cosmetic -- never feeds detector state. Returns ``None`` only when the
    level was never reached before the break (no candle qualifies), in which case
    the caller keeps whatever anchor it already had.
    """
    hi = min(break_index, len(candles) - 1)
    if hi < 0:
        return None
    own_exact = hi
    while own_exact >= 0:
        c = candles[own_exact]
        if (c.low if bearish else c.high) == level:
            return c.timestamp
        own_exact -= 1
    opp_exact = hi
    while opp_exact >= 0:
        c = candles[opp_exact]
        if (c.high if bearish else c.low) == level:
            return c.timestamp
        opp_exact -= 1
    # Straddle: exclude the break candle itself (it necessarily brackets the
    # level as it breaks through), so this resolves the prior candle that held
    # the level rather than the break.
    straddle = min(break_index - 1, len(candles) - 1)
    while straddle >= 0:
        c = candles[straddle]
        if c.low <= level <= c.high:
            return c.timestamp
        straddle -= 1
    return None


def bos_confluence(
    candle: Candle, *, bullish: bool, strong_close_frac: float | None = None
) -> bool:
    """LuxAlgo-style confluence filter for internal BOS candles.

    For a bullish BOS the breaking candle must have a larger upper shadow
    than lower shadow (upward price expansion beyond the level, even if the
    close pulled back inside the body); for a bearish BOS the reverse. This
    mirrors the 'Confluence Filter' option in LuxAlgo's Smart Money Concepts
    indicator (`bullishBar`/`bearishBar` in its Pine source).

    upper_shadow = high - max(close, open)
    lower_shadow = min(close, open) - low
    bullish: upper_shadow > lower_shadow
    bearish: upper_shadow < lower_shadow

    The shadow-balance test is meant to reject *rejection* candles (a wick past
    the level that closes back the other way), but it also rejects a clean
    momentum candle that *closes at its extreme* after an early counter-dip: a
    bullish close at the high leaves almost no upper shadow, so a larger lower
    shadow (the dip) fails the test even though the close is decisively strong
    (BTC 5m 2026-07-21 13:45: O 66336 / L 66311 / C 66627 / H 66636 -- close at
    97% of range, upper 9.1 < lower 24.8). When ``strong_close_frac`` is set, a
    candle also passes if its close sits in the top (bullish) / bottom (bearish)
    ``strong_close_frac`` of its own high-low range -- a decisive close overrides
    the shadow shape, while a genuine rejection wick (close back inside) still
    fails both tests. ``None`` keeps the pure LuxAlgo behaviour.
    """
    upper_shadow = candle.high - max(candle.close, candle.open)
    lower_shadow = min(candle.close, candle.open) - candle.low
    if bullish:
        if upper_shadow > lower_shadow:
            return True
    elif upper_shadow < lower_shadow:
        return True
    if strong_close_frac is not None:
        span = candle.high - candle.low
        if span <= 0:
            return False
        close_pos = (candle.close - candle.low) / span
        return (
            close_pos >= strong_close_frac
            if bullish
            else close_pos <= 1 - strong_close_frac
        )
    return False


def find_fvg(
    candles: Sequence[Candle],
    start_index: int,
    end_index: int,
    *,
    bullish: bool,
) -> tuple[int, float] | None:
    """The first 3-candle fair-value gap (FVG / displacement) in
    `candles[start_index:end_index + 1]`, scanned chronologically.

    An FVG is an imbalance left by an impulsive move: a 3-candle window
    `(c0, c1, c2)` where price gapped past `c1` without overlap.

    - Bearish gap (`bullish=False`): `c0.low > c2.high` -- a displacement down.
      The *reclaim level* a later reversal must reclaim is `c0.low` (the top of
      the gap, the last price before the imbalance).
    - Bullish gap (`bullish=True`): `c0.high < c2.low` -- a displacement up. The
      reclaim level is `c0.high` (the bottom of the gap).

    Returns `(c0_index, reclaim_level)` for the first qualifying window, or
    `None` if the range holds no gap. The returned index is `c0` -- the candle
    that *formed* the reclaim level -- so callers can anchor a re-anchor line at
    the level's origin. The window requires `c0_index >= start_index` and
    `c0_index + 2 <= end_index`.
    """
    for c0_index in range(start_index, end_index - 1):
        c0 = candles[c0_index]
        c2 = candles[c0_index + 2]
        if bullish and c0.high < c2.low:
            return c0_index, c0.high
        if not bullish and c0.low > c2.high:
            return c0_index, c0.low
    return None


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
