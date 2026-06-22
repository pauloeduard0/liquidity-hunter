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

# Cap on emitted bands *per side* (strongest first), so overlapping zones don't
# flood the map/chart with near-duplicate liquidation levels.
_MAX_BANDS_PER_SIDE = 20

# Intensity multiplier for the non-dominant side's pools. Both sides hold
# resting stops, but the less-crowded side is dampened so the over-leveraged
# (dominant) side stays visually prominent.
_NON_DOMINANT_FACTOR = 0.45


@dataclass(frozen=True)
class _RawBand:
    """A liquidation band before intensity normalization and time-bounding."""

    liq_price: float
    leverage: int
    side: LiquiditySide
    source_entry_price: float
    start_time: datetime
    weight: float


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

        bands = self._project_bands(dominant, candles, liquidity_zones, intensity_scale)

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

    def _project_bands(
        self,
        dominant: RetailPositioning,
        candles: list[Candle],
        liquidity_zones: list[LiquidityZone],
        intensity_scale: float,
    ) -> list[LiquidationBand]:
        """Project liquidation bands around entry anchors for both sides.

        Both the long-liquidation pool (below entries, ``SELL_SIDE``) and the
        short-liquidation pool (above, ``BUY_SIDE``) are emitted; the
        non-dominant side is dampened by `_NON_DOMINANT_FACTOR` so the
        over-leveraged side stays prominent.
        """
        if dominant is RetailPositioning.NEUTRAL or intensity_scale <= 0:
            return []

        # (side, price direction from entry, intensity scale). Crowded longs
        # liquidate below entries (sell-side); crowded shorts above (buy-side).
        nondominant = intensity_scale * _NON_DOMINANT_FACTOR
        if dominant is RetailPositioning.LONG:
            side_scales = [
                (LiquiditySide.SELL_SIDE, -1, intensity_scale),
                (LiquiditySide.BUY_SIDE, 1, nondominant),
            ]
        else:
            side_scales = [
                (LiquiditySide.BUY_SIDE, 1, intensity_scale),
                (LiquiditySide.SELL_SIDE, -1, nondominant),
            ]

        raw_by_side: dict[LiquiditySide, list[_RawBand]] = {
            LiquiditySide.SELL_SIDE: [],
            LiquiditySide.BUY_SIDE: [],
        }
        for side, direction, scale in side_scales:
            if scale <= 0:
                continue
            for zone in liquidity_zones:
                if zone.is_mitigated or zone.strength <= 0:
                    continue
                entry = (zone.price_low + zone.price_high) / 2
                for leverage, dist_pct in _LEVERAGE_DISTANCE_PCT.items():
                    liq_price = entry * (1 + direction * dist_pct)
                    if liq_price <= 0:
                        continue
                    weight = scale * zone.strength * _LEVERAGE_POPULATION_PRIOR[leverage]
                    if weight <= 0:
                        continue
                    raw_by_side[side].append(
                        _RawBand(
                            liq_price=liq_price,
                            leverage=leverage,
                            side=side,
                            source_entry_price=entry,
                            start_time=zone.formed_at,
                            weight=weight,
                        )
                    )

        peak = max(
            (b.weight for bands in raw_by_side.values() for b in bands), default=0.0
        )
        if peak <= 0:
            return []

        # Keep the strongest bands per side, then resolve start/end times only
        # for those (the liquidation-hit scan is the costly part).
        bands: list[LiquidationBand] = []
        for side_bands in raw_by_side.values():
            strongest = sorted(side_bands, key=lambda b: b.weight, reverse=True)[
                :_MAX_BANDS_PER_SIDE
            ]
            for b in strongest:
                half = b.liq_price * _BAND_HALF_PCT
                end_time = _liquidation_hit_time(candles, b.start_time, b.liq_price, b.side)
                bands.append(
                    LiquidationBand(
                        price_low=b.liq_price - half,
                        price_high=b.liq_price + half,
                        leverage=b.leverage,
                        side=b.side,
                        source_entry_price=b.source_entry_price,
                        intensity=b.weight / peak * 100.0,
                        start_time=b.start_time,
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
