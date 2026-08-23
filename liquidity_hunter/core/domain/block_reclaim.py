"""Block reclaim: an order block tested and handed back at the VWAP."""

from datetime import datetime

from pydantic import Field

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import MarketDirection, TimeFrame


class BlockReclaim(DomainModel):
    """A candle that reclaimed the VWAP after price tested an order block.

    Two populations sit at these two levels, and this observation is about
    the moment they resolve together. Whoever bought the order block is
    positioned in it; whoever entered since the VWAP's anchor has that
    average as their break-even. Price working below the VWAP leaves the
    second group underwater, and therefore supply. A candle whose wick
    pierces the VWAP and whose body closes back on the other side is the
    moment that group stops being underwater -- and it happens right after
    the first group's level was tested.

    The reading is only interesting when the two levels are close. The
    distance from the reclaim to the tested block, measured in the series'
    own volatility (`r_atr`), is what separates *one* level holding two
    populations from two independent levels that price happened to visit in
    sequence. Measured across 673 such candles on 69 symbols, the hit rate
    of a 2:1 excursion is 51% inside one ATR against 29% for the same
    reclaim with no block behind it, where 33.3% is the break-even of that
    payoff; the same lift replicated on thirty symbols held out of the
    search. That measurement is what admits this layer at all -- see
    `docs/block_reclaim.md`, which also records what it does *not* establish.

    Descriptive throughout. The fields name what was observed on the candle;
    none of them names a position, a size, or a target. What the reading is
    worth acting on is the reader's, and depends on execution costs this
    layer cannot see.
    """

    symbol: str
    timeframe: TimeFrame
    #: The reclaiming candle -- the one whose wick crossed the VWAP and whose
    #: body closed back across it.
    timestamp: datetime
    #: Which way the reclaim resolved: BULLISH when the wick pierced down
    #: through the VWAP and the close landed above it, bearish mirrored.
    direction: MarketDirection
    #: That candle's close, and the VWAP it closed across.
    reclaim_price: float = Field(gt=0)
    vwap_price: float = Field(gt=0)
    #: The order block that was tested, frozen as it stood.
    block_price_low: float = Field(gt=0)
    block_price_high: float = Field(gt=0)
    #: The candle the block was anchored to, and the first candle of the
    #: visit that tested it -- the test is usually several candles long.
    block_timestamp: datetime
    test_start_timestamp: datetime
    #: Whether this was the block's **first** visit since it formed. A block
    #: price has already traded back into has had its resting orders worked;
    #: measured, freshness helps a little and is not what carries the reading.
    first_test: bool
    #: The extreme of the test: the lowest low (bullish) or highest high
    #: (bearish) between the start of the visit and the reclaim. The level
    #: the reclaim is measured against.
    test_extreme: float = Field(gt=0)
    #: Distance from the reclaim to that extreme, in price and normalized by
    #: the local ATR. `r_atr` is the reading that matters: it says whether
    #: the block and the VWAP are one level or two, in units comparable
    #: across instruments. `None` when the window carries no volatility.
    reclaim_distance: float = Field(gt=0)
    r_atr: float | None = Field(default=None, ge=0.0)
    #: Whether the reclaiming candle is the series' **last** one, which on a
    #: live feed is still forming. Nothing about this reading is knowable
    #: until that candle closes -- the wick may not end up crossing the VWAP,
    #: or the close may not end up on the other side -- so a provisional
    #: reclaim can vanish on the next refresh. The measurement never covered
    #: these: it reads a forward outcome and so drops the tail of the series
    #: outright. Consumers that replay history should skip them, the same
    #: contract the provisional structure marks carry.
    provisional: bool = False
    #: Which shared line the pinbar rejected: ``"vwap"``, ``"ema"`` (the fast
    #: EMA(9), with price already holding the VWAP side) or ``"both"`` on one
    #: candle. Always ``"vwap"`` when the detector runs without an EMA, which
    #: is the reading this layer shipped with.
    #:
    #: Widening the trigger to either line is measured, not assumed: over 22
    #: walk-forward folds the widened rule pools the best out-of-sample Sharpe
    #: of 26 declared candidates (6.72 against 6.15 for the VWAP-only trigger),
    #: and it earns that without discarding trades -- 622 against 636 -- so the
    #: gain is a re-timing of the entry rather than a subset selection. The
    #: ``"both"`` subset measures far better in the search half and *worse* out
    #: of sample (3.87), so this field is here to be observed, never filtered
    #: on. See `docs/block_reclaim.md`.
    trigger_line: str = "vwap"
    #: Which pinbar definitions the trigger candle met, comma-joined and
    #: sorted: ``legacy``, ``l1`` (the golden two-thirds tail), ``l2``
    #: (body-heavy with a capped nose). The **union** is accepted, which is
    #: measured rather than assumed -- it pools the best out-of-sample Sharpe
    #: of 30 declared rules and beats each of its own subsets. Observe this
    #: field; do not filter on it.
    pinbar_grade: str = "legacy"
    #: How many candles of accumulation the VWAP carried at the reclaim.
    #: The lift tracks this monotonically -- a six-candle average is nobody's
    #: break-even -- so a reading against a barely-started VWAP is weaker
    #: evidence than the same reading late in an accumulation.
    vwap_candles: int = Field(ge=1)
