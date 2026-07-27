"""VolumeProfile domain entities.

Volume-at-price: how much of a window's traded volume changed hands in each
price band, rather than in each unit of time. Where the candle series answers
"when did price move", the profile answers "where did the market agree" — the
POC is the price the market kept coming back to, the value area is the band it
accepted, and the low-volume nodes are the prices it refused and travelled
through quickly.

A descriptive *observation* of participation across price, not a target or a
recommendation.

Fidelity
--------
Binance klines report volume per candle, not per price. Each candle's volume is
therefore spread across the buckets its high-low range overlaps. Measured
against the true profile rebuilt from every individual trade (aggTrades) on
BTCUSDT and NEARUSDT, over the window the dashboard actually renders this
reproduces the POC exactly (or within ~0.6 of a timeframe-ATR) at 95-97%
histogram overlap and 91-100% value-area IoU: the buckets of a wide window are
wider than a single candle's range, so the distribution error averages out.

``buy_volume``/``sell_volume`` are a weaker reading and are flagged as such by
``delta_estimated``. They split each candle's volume by its *taker* balance
(the same basis as ``indicators.volume_delta``) and then spread that split
across the candle's range — so the aggressor is known per candle but not per
price. Per-bucket delta *sign* agreed with the true trade-level profile 72-90%
of the time depending on timeframe. True delta-at-price needs trade-level data
(a footprint layer), which this is not.
"""

from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import TimeFrame, VolumeNode


class VolumeProfileBucket(DomainModel):
    """One price band of a `VolumeProfile`.

    ``volume`` is the base-asset volume attributed to this band.
    ``buy_volume`` and ``sell_volume`` split it by taker aggression and always
    sum to ``volume``; see the module docstring on their weaker fidelity.
    """

    price_low: float = Field(gt=0)
    price_high: float = Field(gt=0)
    volume: float = Field(ge=0.0)
    buy_volume: float = Field(default=0.0, ge=0.0)
    sell_volume: float = Field(default=0.0, ge=0.0)
    node: VolumeNode = VolumeNode.NORMAL
    in_value_area: bool = False
    is_poc: bool = False

    @model_validator(mode="after")
    def _check_band(self) -> Self:
        if self.price_high <= self.price_low:
            raise ValueError("price_high must be > price_low")
        return self

    @property
    def delta(self) -> float:
        """Net taker aggression attributed to this band (buy − sell)."""
        return self.buy_volume - self.sell_volume

    @property
    def mid_price(self) -> float:
        """Centre of the band, the price this bucket is plotted at."""
        return (self.price_low + self.price_high) / 2


class VolumeProfile(DomainModel):
    """Volume-at-price over one window of a symbol/timeframe.

    ``buckets`` partition ``[price_low, price_high]`` into equal-width bands of
    ``bucket_size``, ordered from lowest price to highest.

    Fields
    ------
    start_timestamp / end_timestamp:
        The first and last candle the profile was built from.
    poc_price:
        Point of control — the mid price of the highest-volume bucket.
    value_area_low / value_area_high:
        The band containing ``value_area_pct`` of total volume, grown outward
        from the POC toward whichever neighbour holds more volume.
    delta_estimated:
        Always ``True`` for a kline-sourced profile: the buy/sell split is
        inferred per candle, not observed per trade. A future footprint layer
        built on trade data would set it ``False``.
    """

    symbol: str
    timeframe: TimeFrame
    start_timestamp: datetime
    end_timestamp: datetime
    price_low: float = Field(gt=0)
    price_high: float = Field(gt=0)
    bucket_size: float = Field(gt=0)
    buckets: list[VolumeProfileBucket]
    poc_price: float = Field(gt=0)
    value_area_low: float = Field(gt=0)
    value_area_high: float = Field(gt=0)
    value_area_pct: float = Field(gt=0.0, le=1.0)
    total_volume: float = Field(ge=0.0)
    delta_estimated: bool = True

    @model_validator(mode="after")
    def _check_ranges(self) -> Self:
        if self.price_high <= self.price_low:
            raise ValueError("price_high must be > price_low")
        if self.value_area_high < self.value_area_low:
            raise ValueError("value_area_high must be >= value_area_low")
        if self.end_timestamp < self.start_timestamp:
            raise ValueError("end_timestamp must be >= start_timestamp")
        return self

    @property
    def high_volume_nodes(self) -> list[VolumeProfileBucket]:
        """Bands the market kept trading at — shelves that tend to hold."""
        return [b for b in self.buckets if b.node is VolumeNode.HIGH_VOLUME]

    @property
    def low_volume_nodes(self) -> list[VolumeProfileBucket]:
        """Bands the market refused — price tends to travel through them fast."""
        return [b for b in self.buckets if b.node is VolumeNode.LOW_VOLUME]
