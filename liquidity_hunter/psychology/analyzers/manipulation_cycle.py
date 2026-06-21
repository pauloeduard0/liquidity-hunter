"""Institutional manipulation cycle detector.

Connects existing observations (liquidity zones, structure sweeps, POI
RTO events, BOS events, volume delta) into three-phase Wyckoff/SMC
manipulation cycles:

  Accumulation  →  Manipulation (sweep)  →  Expansion (BOS)

The detector is retrospective: it scans the full candle series and
reports each cycle's phase and status at the time of analysis.  It also
identifies *prospective* accumulation zones — active liquidity zones
where price is currently consolidating and stops are likely building —
as ``IN_PROGRESS`` ``ACCUMULATION`` cycles.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from liquidity_hunter.core.domain import (
    Candle,
    LiquiditySide,
    LiquidityZone,
    ManipulationCycleStatus,
    ManipulationPhase,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.core.domain.manipulation_cycle import ManipulationCycle
from liquidity_hunter.core.domain.poi_zone import RTOSweepEvent

_TIMEFRAME_MIN_ACCUMULATION: dict[TimeFrame, int] = {
    TimeFrame.M1: 20,
    TimeFrame.M5: 15,
    TimeFrame.M15: 10,
    TimeFrame.M30: 7,
    TimeFrame.H1: 5,
    TimeFrame.H4: 3,
    TimeFrame.D1: 2,
    TimeFrame.W1: 2,
}

_TIMEFRAME_PROXIMITY: dict[TimeFrame, float] = {
    TimeFrame.M1: 0.012,
    TimeFrame.M5: 0.015,
    TimeFrame.M15: 0.015,
    TimeFrame.M30: 0.018,
    TimeFrame.H1: 0.02,
    TimeFrame.H4: 0.025,
    TimeFrame.D1: 0.03,
    TimeFrame.W1: 0.03,
}


@dataclass(frozen=True)
class _SweepTrigger:
    timestamp: datetime
    price: float
    sweep_direction: MarketDirection
    source: str


@dataclass(frozen=True)
class _Accumulation:
    start: datetime
    end: datetime
    candle_count: int
    avg_volume_delta: float


@dataclass(frozen=True)
class _Expansion:
    timestamp: datetime
    price: float
    volume_delta: float


class ManipulationCycleDetector:
    """Detects institutional manipulation cycles from market observations.

    Parameters
    ----------
    proximity_pct:
        How close price must be to a zone boundary to count as
        "consolidating near" that zone, as a fraction of zone price
        (e.g. 0.015 = 1.5%).
    min_accumulation_candles:
        Minimum candles near a zone to qualify as accumulation.
    max_expansion_candles:
        Maximum candles after a sweep to look for an expansion BOS.
    """

    def __init__(
        self,
        proximity_pct: float | None = None,
        min_accumulation_candles: int | None = None,
        max_expansion_candles: int = 30,
    ) -> None:
        self._proximity_override = proximity_pct
        self._min_accum_override = min_accumulation_candles
        self._max_expansion = max_expansion_candles

    def detect(
        self,
        candles: list[Candle],
        structure_events: list[MarketStructure],
        liquidity_zones: list[LiquidityZone],
        poi_sweep_events: list[RTOSweepEvent],
        volume_deltas: Sequence[float],
    ) -> list[ManipulationCycle]:
        if len(candles) < 2 or not liquidity_zones:
            return []

        tf = candles[0].timeframe

        if self._proximity_override is not None:
            self._proximity_pct = self._proximity_override
        else:
            self._proximity_pct = _TIMEFRAME_PROXIMITY.get(tf, 0.015)

        if self._min_accum_override is not None:
            self._min_accum = self._min_accum_override
        else:
            self._min_accum = _TIMEFRAME_MIN_ACCUMULATION.get(tf, 5)

        ts_to_idx = {c.timestamp: i for i, c in enumerate(candles)}

        sweeps = self._collect_sweeps(structure_events, poi_sweep_events)
        bos_events = sorted(
            (e for e in structure_events if e.event == StructureEvent.BREAK_OF_STRUCTURE),
            key=lambda e: e.timestamp,
        )

        cycles: list[ManipulationCycle] = []
        used_zones: set[tuple[float, float, datetime]] = set()
        swept_zone_prices: list[tuple[float, LiquiditySide]] = []

        for sweep in sweeps:
            sweep_idx = ts_to_idx.get(sweep.timestamp)
            if sweep_idx is None:
                continue

            zone = self._find_swept_zone(sweep, liquidity_zones)
            if zone is None:
                continue

            zone_key = (zone.price_low, zone.price_high, zone.formed_at)
            if zone_key in used_zones:
                continue
            used_zones.add(zone_key)

            zone_mid = (zone.price_low + zone.price_high) / 2
            swept_zone_prices.append((zone_mid, zone.side))

            accum = self._measure_accumulation(
                candles, zone, sweep_idx, volume_deltas
            )

            if accum.candle_count < self._min_accum:
                continue

            expansion_dir = (
                MarketDirection.BULLISH
                if sweep.sweep_direction == MarketDirection.BEARISH
                else MarketDirection.BEARISH
            )

            expansion = self._find_expansion(
                bos_events, expansion_dir, sweep.timestamp, candles, ts_to_idx, volume_deltas
            )

            if expansion is not None:
                phase = ManipulationPhase.EXPANSION
                status = ManipulationCycleStatus.CONFIRMED
            else:
                phase = ManipulationPhase.MANIPULATION
                remaining = len(candles) - 1 - sweep_idx
                status = (
                    ManipulationCycleStatus.IN_PROGRESS
                    if remaining < self._max_expansion
                    else ManipulationCycleStatus.FAILED
                )

            sweep_vd = volume_deltas[sweep_idx] if sweep_idx < len(volume_deltas) else None

            cycles.append(
                ManipulationCycle(
                    symbol=candles[0].symbol,
                    timeframe=candles[0].timeframe,
                    direction=expansion_dir,
                    phase=phase,
                    status=status,
                    target_zone_price_low=zone.price_low,
                    target_zone_price_high=zone.price_high,
                    target_zone_type=zone.zone_type,
                    target_zone_side=zone.side,
                    accumulation_start=accum.start,
                    accumulation_end=accum.end,
                    consolidation_candles=accum.candle_count,
                    accumulation_avg_volume_delta=accum.avg_volume_delta,
                    sweep_timestamp=sweep.timestamp,
                    sweep_extreme=sweep.price,
                    sweep_volume_delta=sweep_vd,
                    expansion_timestamp=expansion.timestamp if expansion else None,
                    expansion_price=expansion.price if expansion else None,
                    expansion_volume_delta=expansion.volume_delta if expansion else None,
                )
            )

        prospective = self._find_prospective_accumulations(
            candles, liquidity_zones, volume_deltas, swept_zone_prices
        )
        cycles.extend(prospective)

        return sorted(cycles, key=lambda c: c.accumulation_start)

    # ------------------------------------------------------------------
    # Zone deduplication
    # ------------------------------------------------------------------

    def _is_zone_used(
        self,
        zone_mid: float,
        side: LiquiditySide,
        used: list[tuple[float, LiquiditySide]],
    ) -> bool:
        for used_mid, used_side in used:
            if used_side != side:
                continue
            if abs(zone_mid - used_mid) / used_mid <= self._proximity_pct:
                return True
        return False

    # ------------------------------------------------------------------
    # Sweep collection
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_sweeps(
        structure_events: list[MarketStructure],
        poi_sweep_events: list[RTOSweepEvent],
    ) -> list[_SweepTrigger]:
        triggers: list[_SweepTrigger] = []

        for e in structure_events:
            if e.event != StructureEvent.LIQUIDITY_SWEEP:
                continue
            triggers.append(
                _SweepTrigger(
                    timestamp=e.timestamp,
                    price=e.price_level,
                    sweep_direction=e.direction,
                    source="structure",
                )
            )

        for rto in poi_sweep_events:
            triggers.append(
                _SweepTrigger(
                    timestamp=rto.timestamp,
                    price=rto.sweep_extreme,
                    sweep_direction=(
                        MarketDirection.BEARISH
                        if rto.direction == MarketDirection.BULLISH
                        else MarketDirection.BULLISH
                    ),
                    source="poi_rto",
                )
            )

        return sorted(triggers, key=lambda t: t.timestamp)

    # ------------------------------------------------------------------
    # Zone matching
    # ------------------------------------------------------------------

    def _find_swept_zone(
        self,
        sweep: _SweepTrigger,
        zones: list[LiquidityZone],
    ) -> LiquidityZone | None:
        if sweep.sweep_direction == MarketDirection.BEARISH:
            expected_side = LiquiditySide.SELL_SIDE
        else:
            expected_side = LiquiditySide.BUY_SIDE

        candidates = [z for z in zones if z.side == expected_side]
        if not candidates:
            return None

        ref = sweep.price
        best: LiquidityZone | None = None
        best_dist = float("inf")

        for zone in candidates:
            if expected_side == LiquiditySide.SELL_SIDE:
                if ref > zone.price_low * (1 + self._proximity_pct):
                    continue
                dist = max(0.0, zone.price_low - ref)
            else:
                if ref < zone.price_high * (1 - self._proximity_pct):
                    continue
                dist = max(0.0, ref - zone.price_high)

            if dist < best_dist:
                best = zone
                best_dist = dist

        return best

    # ------------------------------------------------------------------
    # Accumulation measurement
    # ------------------------------------------------------------------

    def _measure_accumulation(
        self,
        candles: list[Candle],
        zone: LiquidityZone,
        sweep_idx: int,
        volume_deltas: Sequence[float],
    ) -> _Accumulation:
        zone_mid = (zone.price_high + zone.price_low) / 2
        threshold = zone_mid * self._proximity_pct

        count = 0
        start_idx = sweep_idx

        for i in range(sweep_idx - 1, -1, -1):
            candle = candles[i]
            mid = (candle.high + candle.low) / 2
            if abs(mid - zone_mid) <= threshold:
                count += 1
                start_idx = i
            else:
                break

        if count < 1:
            start_idx = max(0, sweep_idx - 1)
            count = 1

        vd_slice = list(volume_deltas[start_idx:sweep_idx])
        avg_vd = sum(abs(v) for v in vd_slice) / len(vd_slice) if vd_slice else 0.0

        return _Accumulation(
            start=candles[start_idx].timestamp,
            end=candles[sweep_idx - 1].timestamp if sweep_idx > 0 else candles[0].timestamp,
            candle_count=count,
            avg_volume_delta=avg_vd,
        )

    # ------------------------------------------------------------------
    # Expansion detection
    # ------------------------------------------------------------------

    def _find_expansion(
        self,
        bos_events: list[MarketStructure],
        expansion_dir: MarketDirection,
        sweep_ts: datetime,
        candles: list[Candle],
        ts_to_idx: dict[datetime, int],
        volume_deltas: Sequence[float],
    ) -> _Expansion | None:
        sweep_idx = ts_to_idx.get(sweep_ts)
        if sweep_idx is None:
            return None

        max_ts_idx = min(sweep_idx + self._max_expansion, len(candles) - 1)
        max_ts = candles[max_ts_idx].timestamp

        for bos in bos_events:
            if bos.timestamp <= sweep_ts:
                continue
            if bos.timestamp > max_ts:
                break
            if bos.direction != expansion_dir:
                continue

            bos_idx = ts_to_idx.get(bos.timestamp)
            vd = (
                volume_deltas[bos_idx]
                if bos_idx is not None and bos_idx < len(volume_deltas)
                else 0.0
            )

            return _Expansion(
                timestamp=bos.timestamp,
                price=bos.price_level,
                volume_delta=vd,
            )

        return None

    # ------------------------------------------------------------------
    # Prospective accumulation (no sweep yet)
    # ------------------------------------------------------------------

    @staticmethod
    def _cluster_zones(
        zones: list[LiquidityZone],
        cluster_pct: float,
    ) -> list[LiquidityZone]:
        if not zones:
            return []

        by_side: dict[LiquiditySide, list[LiquidityZone]] = {}
        for z in zones:
            by_side.setdefault(z.side, []).append(z)

        result: list[LiquidityZone] = []
        for side_zones in by_side.values():
            sorted_zones = sorted(
                side_zones,
                key=lambda z: (z.price_high + z.price_low) / 2,
            )
            clusters: list[list[LiquidityZone]] = []
            for z in sorted_zones:
                z_mid = (z.price_high + z.price_low) / 2
                if clusters:
                    last_mid = (
                        clusters[-1][-1].price_high + clusters[-1][-1].price_low
                    ) / 2
                    if abs(z_mid - last_mid) / last_mid <= cluster_pct:
                        clusters[-1].append(z)
                        continue
                clusters.append([z])

            for cluster in clusters:
                result.append(max(cluster, key=lambda z: z.strength))

        return result

    def _find_prospective_accumulations(
        self,
        candles: list[Candle],
        zones: list[LiquidityZone],
        volume_deltas: Sequence[float],
        used_zone_prices: list[tuple[float, LiquiditySide]],
    ) -> list[ManipulationCycle]:
        active_zones = [z for z in zones if not z.is_mitigated]
        clustered = self._cluster_zones(active_zones, self._proximity_pct)
        results: list[ManipulationCycle] = []

        for zone in clustered:
            zone_mid = (zone.price_high + zone.price_low) / 2
            if self._is_zone_used(zone_mid, zone.side, used_zone_prices):
                continue
            threshold = zone_mid * self._proximity_pct

            count = 0
            start_idx = len(candles) - 1

            for i in range(len(candles) - 1, -1, -1):
                candle = candles[i]
                mid = (candle.high + candle.low) / 2
                if abs(mid - zone_mid) <= threshold:
                    count += 1
                    start_idx = i
                else:
                    break

            if count < self._min_accum:
                continue

            vd_slice = list(volume_deltas[start_idx:])
            avg_vd = (
                sum(abs(v) for v in vd_slice) / len(vd_slice) if vd_slice else 0.0
            )

            expansion_dir = (
                MarketDirection.BULLISH
                if zone.side == LiquiditySide.SELL_SIDE
                else MarketDirection.BEARISH
            )

            results.append(
                ManipulationCycle(
                    symbol=candles[0].symbol,
                    timeframe=candles[0].timeframe,
                    direction=expansion_dir,
                    phase=ManipulationPhase.ACCUMULATION,
                    status=ManipulationCycleStatus.IN_PROGRESS,
                    target_zone_price_low=zone.price_low,
                    target_zone_price_high=zone.price_high,
                    target_zone_type=zone.zone_type,
                    target_zone_side=zone.side,
                    accumulation_start=candles[start_idx].timestamp,
                    accumulation_end=candles[-1].timestamp,
                    consolidation_candles=count,
                    accumulation_avg_volume_delta=avg_vd,
                )
            )

        return results
