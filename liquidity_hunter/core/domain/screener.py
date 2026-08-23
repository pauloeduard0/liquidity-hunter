"""Screener rows: the block-reclaim setup surfaced across many symbols.

The setup fires roughly once every two months per symbol on a gated intraday
timeframe -- the scarcity a single watchlist reads as "almost never" is a
coverage problem, not a filter problem (measured 2026-08-23: the gated M15
rule fires ~39 times a month across the 71-symbol universe, ~0.55 per symbol).
These models are the units a universe-wide scan returns, so a reader watches
one list instead of seventy charts.

Descriptive throughout, like `BlockReclaim` itself: a row says what was
observed and where, never what to do about it. In particular `r_atr` is
carried, not thresholded -- the gate is the reader's choice, and the
measurement behind each choice lives in `docs/block_reclaim.md`.
"""

from datetime import datetime

from pydantic import Field

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.block_reclaim import BlockReclaim
from liquidity_hunter.core.domain.enums import (
    MarketDirection,
    ScreenerStatus,
    TimeFrame,
)


class BlockReclaimScanEntry(DomainModel):
    """One symbol/timeframe's standing in the block-reclaim setup.

    A ``FIRED`` row wraps the full :class:`BlockReclaim` it reports; an
    ``ARMED`` row has no reclaim yet -- it names the block under test and how
    long the detector's wait window has been open.
    """

    symbol: str
    timeframe: TimeFrame
    status: ScreenerStatus
    direction: MarketDirection
    #: The reclaim candle (``FIRED``) or the first candle of the open visit
    #: (``ARMED``).
    timestamp: datetime
    #: Whole closed candles since `timestamp` (0 = the live candle).
    candles_ago: int = Field(ge=0)
    current_price: float = Field(gt=0)
    #: The tested block, frozen as it stood.
    block_price_low: float = Field(gt=0)
    block_price_high: float = Field(gt=0)
    #: `FIRED` only; `None` while armed (there is no reclaim to measure).
    r_atr: float | None = Field(default=None, ge=0.0)
    #: The full observation behind a ``FIRED`` row.
    reclaim: BlockReclaim | None = None


class BlockReclaimScreen(DomainModel):
    """A universe scan: every armed or recently fired block-reclaim."""

    generated_at: datetime
    timeframes: list[TimeFrame]
    symbols_scanned: int = Field(ge=0)
    #: Symbols whose fetch or detection failed this pass -- reported, not
    #: silently dropped, so an empty list means the scan really was complete.
    symbols_failed: list[str] = Field(default_factory=list)
    entries: list[BlockReclaimScanEntry] = Field(default_factory=list)
