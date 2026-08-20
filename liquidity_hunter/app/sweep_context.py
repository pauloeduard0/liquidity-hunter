"""Annotate each liquidity sweep with the pool it landed in.

Composition-level, like `build_liquidity_grabs`: it reads the `liquidity`
layer's structure stream and its order blocks and says one thing about the
pair that neither says alone. Deliberately **additive** -- it never changes
how a sweep is classified, only what is known about one after the fact.
"""

from statistics import fmean

from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    POIZone,
    POIZoneKind,
    StructureEvent,
    SweepContext,
    TimeFrame,
)
from liquidity_hunter.indicators.supertrend import true_range_series

#: How far outside an order block's boundaries the swept extreme may land and
#: still count as landing in it, in mean-true-range units. Measured across 12
#: symbols x 15m/1h/4h: at this width the annotated sweeps' extremes held for
#: the next 5 candles 76% of the time vs 60% for a direction-matched control.
#: A far stricter reading (within a quarter ATR of the block's *far* edge)
#: was measured too and collapsed to a handful of events per chart with no
#: effect left to see -- the block is a zone, and asking the wick to find its
#: edge asks for precision the observation does not have.
_BLOCK_TOLERANCE_ATR = 0.5


def build_sweep_contexts(
    *,
    symbol: str,
    timeframe: TimeFrame,
    structure_events: list[MarketStructure],
    poi_zones: list[POIZone],
    candles: list[Candle],
) -> list[SweepContext]:
    """One `SweepContext` per confirmed sweep in the window."""
    if not candles:
        return []
    by_timestamp = {candle.timestamp: candle for candle in candles}
    # One volatility unit for the whole window, so depths and tolerances are
    # comparable between sweeps of the same chart (the `LiquidityGrab`
    # reasoning: a per-candle ATR would make a sweep in a quiet stretch look
    # deeper than the same distance in a violent one).
    tr = true_range_series(candles)
    atr = fmean(tr) if tr else 0.0
    tolerance = _BLOCK_TOLERANCE_ATR * atr

    # Order blocks only -- the breaker/mitigation block of the same MSB sits a
    # few ticks away and would double-count one observation, the same reason
    # the chart draws only order blocks.
    blocks = [zone for zone in poi_zones if zone.kind is POIZoneKind.ORDER_BLOCK]

    contexts: list[SweepContext] = []
    for event in structure_events:
        if event.event is not StructureEvent.LIQUIDITY_SWEEP or event.provisional:
            continue
        candle = by_timestamp.get(event.timestamp)
        if candle is None:
            continue
        bullish = event.direction is MarketDirection.BULLISH
        extreme = candle.high if bullish else candle.low

        block = _facing_block(
            extreme,
            blocks,
            bullish=bullish,
            tolerance=tolerance,
            timestamp=candle.timestamp,
        )
        excursion: float | None = None
        if event.reference_price_level is not None and atr > 0:
            beyond = (
                extreme - event.reference_price_level
                if bullish
                else event.reference_price_level - extreme
            )
            excursion = max(0.0, beyond) / atr

        contexts.append(
            SweepContext(
                symbol=symbol,
                timeframe=timeframe,
                event_timestamp=event.timestamp,
                direction=event.direction,
                swept_extreme=extreme,
                in_order_block=block is not None,
                block_low=None if block is None else block.price_low,
                block_high=None if block is None else block.price_high,
                excursion_atr=excursion,
            )
        )

    contexts.sort(key=lambda c: c.event_timestamp)
    return contexts


def _facing_block(
    extreme: float,
    blocks: list[POIZone],
    *,
    bullish: bool,
    tolerance: float,
    timestamp: "object",
) -> POIZone | None:
    """The order block this sweep ran into, if any.

    Three conditions, and each one was load-bearing in the measurement:

    * **Facing** -- a sweep taking highs runs into *supply*, a sweep taking
      lows into *demand*. Without the direction match the channel is
      vacuous (a sweep sits inside some block 88% of the time, a random
      pivot 85%); with it, it separates.
    * **Pre-existing** -- by `created_at`, when the MSB confirmed the box,
      not by the anchor candle it was drawn back onto. The anchor is chosen
      in hindsight, and price passing that level before confirmation broke
      nothing: there was no box there yet.
    * **Still on the board** -- FIFO queue retirement takes a box off the
      chart when *another* one breaks, and a level nobody is holding any
      more is not a pool. Both are the datation lessons `liquidity_grabs`
      learned the hard way.
    """
    facing = MarketDirection.BEARISH if bullish else MarketDirection.BULLISH
    candidates = [
        block
        for block in blocks
        if block.direction is facing
        and block.created_at < timestamp
        and (block.invalidated_at is None or timestamp <= block.invalidated_at)
        and block.price_low - tolerance <= extreme <= block.price_high + tolerance
    ]
    if not candidates:
        return None
    # The closest one, when the sweep landed where several stack: the box
    # whose own range the wick actually reached is the one a reader points at.
    return min(
        candidates,
        key=lambda b: abs((b.price_low + b.price_high) / 2 - extreme),
    )
