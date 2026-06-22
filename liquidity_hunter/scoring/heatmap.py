"""Liquidity heatmap engine.

Aggregates estimated resting-liquidity concentration ("stop magnets") across
price into a `LiquidityHeatmap` of equal-width price bands, combining three
sources of evidence:

- **POI (order block) zones** — the strongest magnets; institutions defend
  them, so unfilled orders cluster there.
- **Liquidity zones** — swing points and equal highs/lows where retail stops
  rest; weighted by `strength` (which already encodes touch count/prominence).
- **In-progress manipulation cycles** — the accumulation target zone of a
  cycle that is currently building stops.

A retail-bias multiplier amplifies the side where retail stops most likely
rest (longs stop out below price, shorts above). The result is descriptive:
it estimates *where* liquidity sits, not what to do about it.
"""

import math

from liquidity_hunter.core.domain import (
    Candle,
    HeatmapBucket,
    LiquidityHeatmap,
    LiquiditySide,
    LiquidityZone,
    ManipulationCycle,
    ManipulationCycleStatus,
    POIZone,
    POIZoneStatus,
    RetailPositioning,
    TimeFrame,
)
from liquidity_hunter.psychology import RetailBiasEstimate

# Bucket width as a fraction of current price, per timeframe. Finer on low
# timeframes (tighter ranges, more resolution wanted), coarser on high ones.
_TIMEFRAME_BUCKET_PCT: dict[TimeFrame, float] = {
    TimeFrame.M1: 0.001,
    TimeFrame.M5: 0.001,
    TimeFrame.M15: 0.001,
    TimeFrame.M30: 0.002,
    TimeFrame.H1: 0.002,
    TimeFrame.H4: 0.005,
    TimeFrame.D1: 0.005,
    TimeFrame.W1: 0.005,
}
_DEFAULT_BUCKET_PCT = 0.002

# Per-source base heat weights. POI > Zones > Manipulation, per design.
_POI_WEIGHT = 100.0
_ZONE_WEIGHT = 60.0
_MANIPULATION_WEIGHT = 40.0

# Amplification applied to the side where retail stops most likely rest.
_RETAIL_MULTIPLIER = 1.3

# Gaussian smoothing spread, in buckets, so adjacent bands bleed into each
# other rather than appearing as isolated spikes.
_SMOOTHING_SIGMA = 1.0

# Guard against pathological bucket counts on a huge range / tiny price.
_MAX_BUCKETS = 600


class LiquidityHeatmapEngine:
    """Builds a `LiquidityHeatmap` from liquidity, POI, and cycle observations."""

    def __init__(
        self,
        bucket_pct: float | None = None,
        retail_multiplier: float = _RETAIL_MULTIPLIER,
        smoothing_sigma: float = _SMOOTHING_SIGMA,
    ) -> None:
        if bucket_pct is not None and bucket_pct <= 0:
            raise ValueError("bucket_pct must be > 0")
        if retail_multiplier < 1.0:
            raise ValueError("retail_multiplier must be >= 1.0")
        if smoothing_sigma < 0:
            raise ValueError("smoothing_sigma must be >= 0")
        self._bucket_pct = bucket_pct
        self._retail_multiplier = retail_multiplier
        self._smoothing_sigma = smoothing_sigma

    def build(
        self,
        symbol: str,
        timeframe: TimeFrame,
        candles: list[Candle],
        current_price: float,
        liquidity_zones: list[LiquidityZone],
        poi_zones: list[POIZone],
        manipulation_cycles: list[ManipulationCycle],
        retail_bias: RetailBiasEstimate | None = None,
    ) -> LiquidityHeatmap:
        """Aggregate liquidity evidence into a normalized price heatmap."""
        if not candles:
            raise ValueError("candles must not be empty")
        if current_price <= 0:
            raise ValueError("current_price must be > 0")

        bucket_pct = self._bucket_pct or _TIMEFRAME_BUCKET_PCT.get(
            timeframe, _DEFAULT_BUCKET_PCT
        )

        price_min = min(c.low for c in candles)
        price_max = max(c.high for c in candles)
        width = current_price * bucket_pct
        n_buckets = max(1, math.ceil((price_max - price_min) / width))
        if n_buckets > _MAX_BUCKETS:
            n_buckets = _MAX_BUCKETS
            width = (price_max - price_min) / n_buckets

        edges = [price_min + i * width for i in range(n_buckets + 1)]

        heat_zones = [0.0] * n_buckets
        heat_poi = [0.0] * n_buckets
        heat_manip = [0.0] * n_buckets

        for zone in liquidity_zones:
            if zone.is_mitigated:
                continue
            contribution = _ZONE_WEIGHT * zone.strength
            if contribution <= 0:
                continue
            for i in _overlapping_buckets(
                zone.price_low, zone.price_high, price_min, width, n_buckets
            ):
                heat_zones[i] += contribution

        for poi in poi_zones:
            if poi.status is not POIZoneStatus.ACTIVE:
                continue
            for i in _overlapping_buckets(
                poi.price_low, poi.price_high, price_min, width, n_buckets
            ):
                heat_poi[i] += _POI_WEIGHT

        for cycle in manipulation_cycles:
            if cycle.status is not ManipulationCycleStatus.IN_PROGRESS:
                continue
            for i in _overlapping_buckets(
                cycle.target_zone_price_low,
                cycle.target_zone_price_high,
                price_min,
                width,
                n_buckets,
            ):
                heat_manip[i] += _MANIPULATION_WEIGHT

        totals = [heat_zones[i] + heat_poi[i] + heat_manip[i] for i in range(n_buckets)]
        totals = _gaussian_smooth(totals, self._smoothing_sigma)
        totals = self._apply_retail_multiplier(
            totals, edges, current_price, retail_bias
        )

        peak = max(totals) if totals else 0.0
        buckets = [
            HeatmapBucket(
                price_low=edges[i],
                price_high=edges[i + 1],
                heat=(totals[i] / peak * 100.0) if peak > 0 else 0.0,
                side=_bucket_side(edges[i], edges[i + 1], current_price),
                heat_zones=heat_zones[i],
                heat_poi=heat_poi[i],
                heat_manipulation=heat_manip[i],
            )
            for i in range(n_buckets)
        ]

        return LiquidityHeatmap(
            symbol=symbol,
            timeframe=timeframe,
            current_price=current_price,
            bucket_pct=bucket_pct,
            buckets=buckets,
        )

    def _apply_retail_multiplier(
        self,
        totals: list[float],
        edges: list[float],
        current_price: float,
        retail_bias: RetailBiasEstimate | None,
    ) -> list[float]:
        """Amplify the side where retail stops most likely rest.

        Retail longs stop out *below* their entries (sell-side, under price);
        retail shorts stop out *above* (buy-side). So a LONG retail bias makes
        the sell-side hotter, a SHORT bias the buy-side.
        """
        if retail_bias is None or retail_bias.dominant_side is RetailPositioning.NEUTRAL:
            return totals
        if retail_bias.dominant_side is RetailPositioning.LONG:
            amplified_side = LiquiditySide.SELL_SIDE
        else:
            amplified_side = LiquiditySide.BUY_SIDE
        return [
            total * self._retail_multiplier
            if _bucket_side(edges[i], edges[i + 1], current_price) is amplified_side
            else total
            for i, total in enumerate(totals)
        ]


def _overlapping_buckets(
    price_low: float,
    price_high: float,
    price_min: float,
    width: float,
    n_buckets: int,
) -> range:
    """Indices of buckets that the price interval [low, high] overlaps."""
    lo = int((price_low - price_min) // width)
    hi = int((price_high - price_min) // width)
    lo = max(0, min(lo, n_buckets - 1))
    hi = max(0, min(hi, n_buckets - 1))
    return range(lo, hi + 1)


def _bucket_side(price_low: float, price_high: float, current_price: float) -> LiquiditySide:
    """Buy-side if the band's midpoint is above the current price, else sell-side."""
    midpoint = (price_low + price_high) / 2
    return LiquiditySide.BUY_SIDE if midpoint >= current_price else LiquiditySide.SELL_SIDE


def _gaussian_smooth(values: list[float], sigma: float) -> list[float]:
    """1-D Gaussian smoothing with a kernel truncated at 3 sigma."""
    if sigma <= 0 or len(values) < 2:
        return list(values)
    radius = max(1, int(math.ceil(3 * sigma)))
    kernel = [math.exp(-(k * k) / (2 * sigma * sigma)) for k in range(-radius, radius + 1)]
    kernel_sum = sum(kernel)
    kernel = [k / kernel_sum for k in kernel]

    n = len(values)
    smoothed = [0.0] * n
    for i in range(n):
        acc = 0.0
        for offset, weight in enumerate(kernel, start=-radius):
            j = min(n - 1, max(0, i + offset))
            acc += values[j] * weight
        smoothed[i] = acc
    return smoothed
