"""Leverage liquidation estimator.

Builds a `LeverageLiquidationMap` — a descriptive "gravitational map" of where
leveraged retail positions would be force-liquidated. It first infers which
side of the perpetual book is over-leveraged from three pieces of futures
market state (funding rate, crowd long/short ratio, open-interest change), then
projects liquidation price bands at fixed leverage tiers around likely entry
clusters (liquidity zones). The result estimates *where* potential energy is
stored, not what to do about it.
"""

from dataclasses import dataclass
from datetime import datetime

from liquidity_hunter.core.domain import (
    Candle,
    FundingRate,
    LeverageLiquidationMap,
    LiquidationBand,
    LiquiditySide,
    LiquidityZone,
    LongShortRatio,
    OpenInterestPoint,
    POIZone,
    POIZoneStatus,
    RetailPositioning,
    TimeFrame,
)

# Approximate distance from entry to liquidation, per leverage tier, derived
# from Binance BTCUSDT tier-1 maintenance margin (~0.4%): dist ~= 1/lev - mmr.
_LEVERAGE_DISTANCE_PCT: dict[int, float] = {
    10: 0.095,
    25: 0.036,
    50: 0.016,
    100: 0.006,
}

# How populated each leverage tier typically is (lower leverage is far more
# common among retail), used to weight band intensity.
_LEVERAGE_POPULATION_PRIOR: dict[int, float] = {
    10: 1.0,
    25: 0.7,
    50: 0.45,
    100: 0.25,
}

# Half-width of a liquidation band as a fraction of its liquidation price.
_BAND_HALF_PCT = 0.001

# Normalizers mapping a raw signal to roughly [-1, 1].
_FUNDING_NORM = 0.0005  # 0.05% funding is markedly elevated.
_RATIO_NORM = 0.5  # a long/short account ratio of 1.5 reads as fully long.

# Below this absolute positioning score neither side is meaningfully crowded.
_NEUTRAL_THRESHOLD = 0.1

# Intensity multiplier for the non-dominant side's pools. Both sides hold
# resting stops, but the less-crowded side is dampened so the over-leveraged
# (dominant) side stays visually prominent.
_NON_DOMINANT_FACTOR = 0.45

# Entry-anchor selection. Liquidation bands are projected from entry clusters
# (liquidity zones). To cover the whole price range — not just the densest
# cluster — nearby entries are first merged (kept strongest) within
# `_ENTRY_CLUSTER_PCT`, then at most `_MAX_ENTRY_CLUSTERS` are kept, spread
# evenly across price via bucket selection. Mitigated (already-swept) zones are
# still valid historical entry areas, downweighted by `_MITIGATED_ENTRY_FACTOR`.
_ENTRY_CLUSTER_PCT = 0.004
_MAX_ENTRY_CLUSTERS = 16
_MITIGATED_ENTRY_FACTOR = 0.7

# Base weight for an order-block (POI) entry anchor. Order blocks concentrate
# real institutional volume, so they rank as strong as the strongest liquidity
# zone (which tops out at strength 1.0).
_POI_ENTRY_WEIGHT = 1.0


@dataclass(frozen=True)
class _Entry:
    """A deduplicated entry-cluster anchor for projecting liquidation bands."""

    price: float
    weight: float
    start_time: datetime


@dataclass(frozen=True)
class ProjectedLevel:
    """A candidate liquidation level, independent of futures positioning.

    Produced by `LeverageLiquidationEstimator.project_levels`: the price where a
    position entered at `source_entry_price` with `leverage` would be liquidated
    (`side` = `SELL_SIDE` for longs liquidating below, `BUY_SIDE` for shorts
    above). `base_weight` (= entry weight × leverage-population prior) ranks the
    level *before* any futures-derived side scaling, so it is the futures-neutral
    intensity signal the backtest evaluates.
    """

    price: float
    leverage: int
    side: LiquiditySide
    source_entry_price: float
    start_time: datetime
    base_weight: float


class LeverageLiquidationEstimator:
    """Estimates leveraged-liquidation bands from futures market state."""

    def estimate(
        self,
        symbol: str,
        timeframe: TimeFrame,
        current_price: float,
        candles: list[Candle],
        liquidity_zones: list[LiquidityZone],
        open_interest: list[OpenInterestPoint],
        funding: list[FundingRate],
        long_short: list[LongShortRatio],
        poi_zones: list[POIZone] | None = None,
    ) -> LeverageLiquidationMap:
        """Infer the over-leveraged side and project its liquidation bands."""
        if current_price <= 0:
            raise ValueError("current_price must be > 0")

        funding_rate = funding[-1].funding_rate if funding else 0.0
        long_short_ratio = (
            sum(s.ratio for s in long_short) / len(long_short) if long_short else 1.0
        )
        oi_change_pct = _open_interest_change_pct(open_interest)

        score = _positioning_score(funding_rate, long_short_ratio)
        # More open interest building up = more fuel; modestly amplify intensity.
        intensity_scale = min(1.0, abs(score) * (1.0 + max(0.0, oi_change_pct)))

        if score > _NEUTRAL_THRESHOLD:
            dominant = RetailPositioning.LONG
        elif score < -_NEUTRAL_THRESHOLD:
            dominant = RetailPositioning.SHORT
        else:
            dominant = RetailPositioning.NEUTRAL

        bands = self._project_bands(
            dominant, candles, liquidity_zones, poi_zones or [], intensity_scale
        )

        return LeverageLiquidationMap(
            symbol=symbol,
            timeframe=timeframe,
            current_price=current_price,
            dominant_leveraged_side=dominant,
            positioning_intensity=intensity_scale,
            funding_rate=funding_rate,
            open_interest_change_pct=oi_change_pct,
            long_short_ratio=long_short_ratio,
            bands=bands,
        )

    def project_levels(
        self,
        liquidity_zones: list[LiquidityZone],
        poi_zones: list[POIZone] | None = None,
    ) -> list[ProjectedLevel]:
        """Project candidate liquidation levels (both sides, all tiers).

        Futures-independent: it places levels at `entry × (1 ± tier_distance)`
        for every entry anchor and weights them by `entry.weight × leverage
        prior` — *no* positioning side scaling. `estimate` layers the futures
        side/intensity on top; the backtest evaluates these raw levels directly.
        """
        entries = _entry_anchors(liquidity_zones, poi_zones or [])
        levels: list[ProjectedLevel] = []
        for side, direction in ((LiquiditySide.SELL_SIDE, -1), (LiquiditySide.BUY_SIDE, 1)):
            for entry in entries:
                for leverage, dist_pct in _LEVERAGE_DISTANCE_PCT.items():
                    price = entry.price * (1 + direction * dist_pct)
                    base_weight = entry.weight * _LEVERAGE_POPULATION_PRIOR[leverage]
                    if price <= 0 or base_weight <= 0:
                        continue
                    levels.append(
                        ProjectedLevel(
                            price=price,
                            leverage=leverage,
                            side=side,
                            source_entry_price=entry.price,
                            start_time=entry.start_time,
                            base_weight=base_weight,
                        )
                    )
        return levels

    def _project_bands(
        self,
        dominant: RetailPositioning,
        candles: list[Candle],
        liquidity_zones: list[LiquidityZone],
        poi_zones: list[POIZone],
        intensity_scale: float,
    ) -> list[LiquidationBand]:
        """Apply futures side/intensity scaling + time-bounding to raw levels.

        Both the long-liquidation pool (below entries, ``SELL_SIDE``) and the
        short-liquidation pool (above, ``BUY_SIDE``) are emitted; the
        non-dominant side is dampened by `_NON_DOMINANT_FACTOR` so the
        over-leveraged side stays prominent.
        """
        if dominant is RetailPositioning.NEUTRAL or intensity_scale <= 0:
            return []

        # Crowded longs liquidate below entries (sell-side); shorts above.
        nondominant = intensity_scale * _NON_DOMINANT_FACTOR
        if dominant is RetailPositioning.LONG:
            side_scale = {
                LiquiditySide.SELL_SIDE: intensity_scale,
                LiquiditySide.BUY_SIDE: nondominant,
            }
        else:
            side_scale = {
                LiquiditySide.BUY_SIDE: intensity_scale,
                LiquiditySide.SELL_SIDE: nondominant,
            }

        weighted: list[tuple[ProjectedLevel, float]] = []
        for level in self.project_levels(liquidity_zones, poi_zones):
            weight = level.base_weight * side_scale[level.side]
            if weight > 0:
                weighted.append((level, weight))

        peak = max((w for _, w in weighted), default=0.0)
        if peak <= 0:
            return []

        bands: list[LiquidationBand] = []
        for level, weight in weighted:
            half = level.price * _BAND_HALF_PCT
            end_time = _liquidation_hit_time(candles, level.start_time, level.price, level.side)
            bands.append(
                LiquidationBand(
                    price_low=level.price - half,
                    price_high=level.price + half,
                    leverage=level.leverage,
                    side=level.side,
                    source_entry_price=level.source_entry_price,
                    intensity=weight / peak * 100.0,
                    start_time=level.start_time,
                    end_time=end_time,
                )
            )
        return bands


def _positioning_score(funding_rate: float, long_short_ratio: float) -> float:
    """Signed crowd-positioning score in [-1, 1] (positive = crowded long).

    Combines funding (longs pay shorts when positive) and the crowd long/short
    account ratio (> 1 when more accounts are long), each clamped to [-1, 1]
    and averaged.
    """
    funding_signal = _clamp(funding_rate / _FUNDING_NORM, -1.0, 1.0)
    ratio_signal = _clamp((long_short_ratio - 1.0) / _RATIO_NORM, -1.0, 1.0)
    return 0.5 * funding_signal + 0.5 * ratio_signal


def _entry_anchors(
    liquidity_zones: list[LiquidityZone], poi_zones: list[POIZone]
) -> list[_Entry]:
    """Entry-cluster anchors for projecting liquidation bands.

    Sources entries from liquidity zones (equal highs/lows, swings) **and**
    order blocks (POI zones — real institutional volume areas). Mitigated zones
    and mitigated order blocks are kept as historical entry areas, downweighted
    by `_MITIGATED_ENTRY_FACTOR` (invalidated order blocks are dropped). Entries
    within `_ENTRY_CLUSTER_PCT` are merged (keep strongest), then at most
    `_MAX_ENTRY_CLUSTERS` are kept spread evenly across price so coverage isn't
    monopolized by the densest cluster.
    """
    candidates = [
        _Entry(
            price=(z.price_low + z.price_high) / 2,
            weight=z.strength * (_MITIGATED_ENTRY_FACTOR if z.is_mitigated else 1.0),
            start_time=z.formed_at,
        )
        for z in liquidity_zones
        if z.strength > 0
    ]
    for poi in poi_zones:
        if poi.status is POIZoneStatus.INVALIDATED:
            continue
        weight = _POI_ENTRY_WEIGHT * (
            _MITIGATED_ENTRY_FACTOR if poi.status is POIZoneStatus.MITIGATED else 1.0
        )
        candidates.append(
            _Entry(
                price=(poi.price_low + poi.price_high) / 2,
                weight=weight,
                start_time=poi.created_at,
            )
        )
    if not candidates:
        return []

    # Merge nearby entries (keep the strongest per cluster).
    candidates.sort(key=lambda e: e.price)
    merged: list[_Entry] = []
    cluster = [candidates[0]]
    for entry in candidates[1:]:
        if entry.price - cluster[0].price <= cluster[0].price * _ENTRY_CLUSTER_PCT:
            cluster.append(entry)
        else:
            merged.append(max(cluster, key=lambda e: e.weight))
            cluster = [entry]
    merged.append(max(cluster, key=lambda e: e.weight))

    if len(merged) <= _MAX_ENTRY_CLUSTERS:
        return merged
    return _bucket_select(merged, _MAX_ENTRY_CLUSTERS)


def _bucket_select(entries: list[_Entry], k: int) -> list[_Entry]:
    """Keep the strongest entry in each of `k` equal-width price buckets.

    Guarantees the kept anchors are spread across the price range rather than
    clumped in the strongest region.
    """
    prices = [e.price for e in entries]
    lo, hi = min(prices), max(prices)
    if hi <= lo:
        return sorted(entries, key=lambda e: e.weight, reverse=True)[:k]
    span = hi - lo
    by_bucket: dict[int, _Entry] = {}
    for entry in entries:
        idx = min(k - 1, int((entry.price - lo) / span * k))
        current = by_bucket.get(idx)
        if current is None or entry.weight > current.weight:
            by_bucket[idx] = entry
    return list(by_bucket.values())


def _liquidation_hit_time(
    candles: list[Candle], start_time: datetime, liq_price: float, side: LiquiditySide
) -> datetime | None:
    """When price first reaches ``liq_price`` at/after ``start_time``.

    A long-liquidation level (``SELL_SIDE``, below entry) is hit when a candle's
    low pierces it; a short-liquidation level (``BUY_SIDE``, above entry) when a
    candle's high pierces it. Returns ``None`` if it has not been reached yet
    (the liquidation pool is still live).
    """
    for candle in candles:
        if candle.timestamp < start_time:
            continue
        if side is LiquiditySide.SELL_SIDE and candle.low <= liq_price:
            return candle.timestamp
        if side is LiquiditySide.BUY_SIDE and candle.high >= liq_price:
            return candle.timestamp
    return None


def _open_interest_change_pct(open_interest: list[OpenInterestPoint]) -> float:
    """Fractional change in open interest across the sampled window."""
    if len(open_interest) < 2:
        return 0.0
    first = open_interest[0].open_interest
    last = open_interest[-1].open_interest
    if first <= 0:
        return 0.0
    return (last - first) / first


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
