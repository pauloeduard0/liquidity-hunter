"""Perpetual-futures market-state domain entities.

These describe *observations* about the futures market — open interest,
funding, and crowd positioning — sourced from a perpetual-swap venue
(e.g. Binance USDT-M). They are the raw inputs the
`LeverageLiquidationEstimator` uses to infer which side of the book is
over-leveraged. No trading or decisioning logic.
"""

from datetime import datetime

from pydantic import Field

from liquidity_hunter.core.domain.base import DomainModel


class OpenInterestPoint(DomainModel):
    """A single open-interest sample for a perpetual contract.

    ``open_interest`` is the number of outstanding contracts (base asset);
    ``open_interest_value`` is the same in quote-asset notional. Rising open
    interest alongside a directional move signals *new* leveraged positions
    being opened (fresh liquidation fuel), not just position rotation.
    """

    symbol: str
    timestamp: datetime
    open_interest: float = Field(ge=0)
    open_interest_value: float = Field(default=0.0, ge=0.0)


class FundingRate(DomainModel):
    """A funding-rate sample for a perpetual contract.

    ``funding_rate`` is the signed periodic rate longs pay shorts (positive)
    or shorts pay longs (negative). Persistently positive funding indicates a
    crowded-long book (longs paying to hold), negative a crowded-short book.
    """

    symbol: str
    timestamp: datetime
    funding_rate: float


class LongShortRatio(DomainModel):
    """A crowd long/short account-ratio sample for a perpetual contract.

    ``long_account_pct`` / ``short_account_pct`` are the fraction of accounts
    positioned long/short (each in [0, 1], summing to ~1). ``ratio`` is
    ``long_account_pct / short_account_pct`` (> 1 means more accounts long).
    """

    symbol: str
    timestamp: datetime
    long_account_pct: float = Field(ge=0.0, le=1.0)
    short_account_pct: float = Field(ge=0.0, le=1.0)
    ratio: float = Field(gt=0)
