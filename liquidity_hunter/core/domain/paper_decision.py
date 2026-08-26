"""A recorded live decision: what the rule saw, and what the tape offered.

The measurement this exists for is the gap between the two. Every number in
`docs/block_reclaim.md` assumes an entry at the trigger candle's **close**,
which is a price that has already happened by the time anything can act on
it. A live decision is taken at the first price available *after* that close,
and the difference -- in R, not in percent, since R is the denominator the
edge is measured in -- is the one cost the study estimates but never observed.

Recording only. Nothing here places, sizes, or manages an order: the fields
name what was seen and what the tape was showing at that moment, and the
resolution fields name what the candles did afterwards. Order execution and
position management stay out of this project.
"""

from datetime import datetime

from pydantic import Field

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import (
    MarketDirection,
    PaperOutcome,
    TimeFrame,
)


class PaperDecision(DomainModel):
    """One journalled decision, open or resolved."""

    #: Stable identity: the same trigger candle can only be journalled once.
    key: str
    symbol: str
    timeframe: TimeFrame
    direction: MarketDirection
    #: The trigger candle, and the close the measurement would have used.
    signal_timestamp: datetime
    signal_close: float = Field(gt=0)
    #: When the decision was recorded, and the price the tape was showing
    #: then -- the first price actually available to act on.
    recorded_at: datetime
    observed_price: float = Field(gt=0)
    #: The gap, signed *against* the trade (positive = worse than the close),
    #: as a fraction of price and as a fraction of R. The second is the one
    #: that matters: the same basis points cost twice as much on a stop half
    #: as wide.
    slippage_pct: float
    slippage_r: float
    #: The levels the rule names, measured from the signal close.
    stop_price: float = Field(gt=0)
    target_price: float = Field(gt=0)
    r_pct: float = Field(gt=0)
    #: The readings the gates are taken on, carried so a journal entry can be
    #: re-sliced later without re-deriving anything.
    r_atr: float | None = Field(default=None, ge=0.0)
    vwap_candles: int = Field(ge=1)
    trigger_line: str
    pinbar_grade: str
    #: Whether the trigger candle closed the way the trade reads it. Carried
    #: for the same reason as the rest of this block -- it is a reading, not a
    #: gate, and gating on it measured worse (see `BlockReclaim.color_agrees`).
    color_agrees: bool = True
    #: Resolution, filled in by a later pass.
    outcome: PaperOutcome = PaperOutcome.OPEN
    resolved_at: datetime | None = None
    #: Candles from the trigger to the resolution.
    bars_to_resolution: int | None = Field(default=None, ge=0)
    #: Realized R measured from the **observed** price, so the journal's own
    #: R already carries the slippage the study could not see.
    realized_r: float | None = None
