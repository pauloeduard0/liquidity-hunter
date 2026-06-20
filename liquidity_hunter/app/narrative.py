"""Narrative engine: synthesizes all detection layers into a coherent story.

Lives in ``app/`` because it is a composition-level synthesizer that
depends on outputs from every layer (structure, liquidity, psychology,
indicators), not a single-domain analyzer.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from liquidity_hunter.core.domain.enums import (
    AnomalySeverity,
    DivergenceType,
    LiquidityZoneType,
    ManipulationCycleStatus,
    ManipulationPhase,
    MarketDirection,
    NarrativeEventType,
    RetailPositioning,
    StructureEvent,
)
from liquidity_hunter.core.domain.narrative import (
    MarketNarrative,
    NarrativeAnomaly,
    NarrativeEvent,
)

if TYPE_CHECKING:
    from liquidity_hunter.app.dashboard_data import DashboardData
    from liquidity_hunter.core.domain.manipulation_cycle import ManipulationCycle
    from liquidity_hunter.core.domain.market_structure import MarketStructure
    from liquidity_hunter.core.domain.poi_zone import RTOSweepEvent


_DIVERGENCE_TO_NARRATIVE: dict[DivergenceType, NarrativeEventType] = {
    DivergenceType.DISTRIBUTION: NarrativeEventType.DISTRIBUTION,
    DivergenceType.ACCUMULATION: NarrativeEventType.ACCUMULATION,
    DivergenceType.EXHAUSTION: NarrativeEventType.EXHAUSTION,
    DivergenceType.ABSORPTION: NarrativeEventType.ABSORPTION,
}

_ZONE_TYPE_LABELS: dict[LiquidityZoneType, str] = {
    LiquidityZoneType.EQUAL_HIGHS: "EQH",
    LiquidityZoneType.EQUAL_LOWS: "EQL",
    LiquidityZoneType.SWING_HIGH: "SH",
    LiquidityZoneType.SWING_LOW: "SL",
    LiquidityZoneType.ORDER_BLOCK: "OB",
    LiquidityZoneType.FAIR_VALUE_GAP: "FVG",
    LiquidityZoneType.LIQUIDITY_POOL: "LP",
}

_SOURCE_PRIORITY: dict[str, int] = {
    "manipulation_cycle": 3,
    "poi": 2,
    "behavior_divergence": 1,
    "market_structure": 0,
    "internal_structure": 0,
}


class NarrativeEngine:
    """Builds a :class:`MarketNarrative` from a completed :class:`DashboardData`."""

    def build(self, data: DashboardData) -> MarketNarrative:
        timeline = self._build_timeline(data)
        phase = self._current_phase(data)
        anomalies = self._detect_anomalies(data)
        confluence_count, confluence_total = self._count_confluence(data)
        summary = self._generate_summary(data, phase, timeline, anomalies)

        return MarketNarrative(
            symbol=data.symbol,
            timeframe=data.timeframe,
            timestamp=datetime.now(tz=UTC),
            phase=phase,
            timeline=timeline,
            anomalies=anomalies,
            summary=summary,
            confluence_count=confluence_count,
            confluence_total=confluence_total,
        )

    # ------------------------------------------------------------------
    # Timeline
    # ------------------------------------------------------------------

    def _build_timeline(self, data: DashboardData) -> list[NarrativeEvent]:
        events: list[NarrativeEvent] = []
        events.extend(self._events_from_structure(data.market_structure_events, "market_structure"))
        events.extend(
            self._events_from_structure(data.internal_structure_events, "internal_structure")
        )
        events.extend(self._events_from_manipulation_cycles(data.manipulation_cycles))
        events.extend(self._events_from_behavior_divergences(data))
        events.extend(self._events_from_poi_sweeps(data.poi_sweep_events))
        events = self._deduplicate(events)
        events.sort(key=lambda e: e.timestamp)
        return events

    @staticmethod
    def _deduplicate(events: list[NarrativeEvent]) -> list[NarrativeEvent]:
        buckets: dict[tuple[datetime, NarrativeEventType], list[NarrativeEvent]] = {}
        for ev in events:
            key = (ev.timestamp, ev.event_type)
            buckets.setdefault(key, []).append(ev)
        result: list[NarrativeEvent] = []
        for group in buckets.values():
            if len(group) == 1:
                result.append(group[0])
            else:
                best = max(
                    group,
                    key=lambda e: _SOURCE_PRIORITY.get(e.source_layer, 0),
                )
                result.append(best)
        return result

    def _events_from_structure(
        self,
        structure_events: list[MarketStructure],
        source_layer: str,
    ) -> list[NarrativeEvent]:
        events: list[NarrativeEvent] = []
        scope_label = "" if source_layer == "market_structure" else " (internal)"
        for ms in structure_events:
            if ms.event == StructureEvent.BREAK_OF_STRUCTURE:
                ref = f"{ms.reference_price_level:,.2f}" if ms.reference_price_level else "—"
                desc = (
                    f"BOS {ms.direction.value}{scope_label} — "
                    f"price closed beyond {ref}, "
                    f"confirming trend continuation to {ms.price_level:,.2f}"
                )
                events.append(
                    NarrativeEvent(
                        timestamp=ms.timestamp,
                        event_type=NarrativeEventType.STRUCTURE_BREAK,
                        direction=ms.direction,
                        description=desc,
                        source_layer=source_layer,
                    )
                )
            elif ms.event == StructureEvent.CHANGE_OF_CHARACTER:
                ref = f"{ms.reference_price_level:,.2f}" if ms.reference_price_level else "—"
                desc = (
                    f"CHoCH {ms.direction.value}{scope_label} — "
                    f"sustained break beyond {ref} "
                    f"(validated pivot), reversing prior structure"
                )
                events.append(
                    NarrativeEvent(
                        timestamp=ms.timestamp,
                        event_type=NarrativeEventType.STRUCTURE_BREAK,
                        direction=ms.direction,
                        description=desc,
                        source_layer=source_layer,
                    )
                )
            elif ms.event == StructureEvent.LIQUIDITY_SWEEP:
                ref = f"{ms.reference_price_level:,.2f}" if ms.reference_price_level else "—"
                side = "support" if ms.direction == MarketDirection.BEARISH else "resistance"
                desc = (
                    f"Sweep {ms.direction.value}{scope_label} — "
                    f"wick pierced {ref} {side} but failed to hold"
                )
                events.append(
                    NarrativeEvent(
                        timestamp=ms.timestamp,
                        event_type=NarrativeEventType.SWEEP,
                        direction=ms.direction,
                        description=desc,
                        source_layer=source_layer,
                    )
                )
        return events

    def _events_from_manipulation_cycles(
        self, cycles: list[ManipulationCycle]
    ) -> list[NarrativeEvent]:
        events: list[NarrativeEvent] = []
        for mc in cycles:
            zone_label = _ZONE_TYPE_LABELS.get(mc.target_zone_type, mc.target_zone_type.value)
            vd_ctx = ""
            if mc.accumulation_avg_volume_delta != 0.0:
                vd_sign = "+" if mc.accumulation_avg_volume_delta > 0 else ""
                vd_ctx = (
                    f", avg VD {vd_sign}{mc.accumulation_avg_volume_delta:.1f}"
                )
            desc = (
                f"Price consolidated {mc.consolidation_candles} candles "
                f"near {mc.target_zone_side.value} {zone_label} "
                f"@ {mc.target_zone_price_low:,.2f}–"
                f"{mc.target_zone_price_high:,.2f}{vd_ctx}"
            )
            events.append(
                NarrativeEvent(
                    timestamp=mc.accumulation_start,
                    event_type=NarrativeEventType.CONSOLIDATION,
                    direction=mc.direction,
                    description=desc,
                    source_layer="manipulation_cycle",
                )
            )
            if mc.sweep_timestamp is not None and mc.sweep_extreme is not None:
                vd_sweep = ""
                if mc.sweep_volume_delta is not None:
                    vd_sweep = f" (VD: {mc.sweep_volume_delta:+.1f})"
                desc = (
                    f"Stop cascade captured "
                    f"{mc.target_zone_side.value} liquidity "
                    f"@ {mc.sweep_extreme:,.2f}{vd_sweep}"
                )
                events.append(
                    NarrativeEvent(
                        timestamp=mc.sweep_timestamp,
                        event_type=NarrativeEventType.SWEEP,
                        direction=mc.direction,
                        description=desc,
                        source_layer="manipulation_cycle",
                    )
                )
            if mc.expansion_timestamp is not None:
                vd_exp = ""
                if mc.expansion_volume_delta is not None:
                    vd_exp = f" (VD: {mc.expansion_volume_delta:+.1f})"
                price_ctx = (
                    f" @ {mc.expansion_price:,.2f}" if mc.expansion_price else ""
                )
                desc = (
                    f"Impulsive expansion {mc.direction.value}"
                    f"{price_ctx}{vd_exp} — "
                    f"prior {mc.target_zone_side.value} sweep resolved"
                )
                events.append(
                    NarrativeEvent(
                        timestamp=mc.expansion_timestamp,
                        event_type=NarrativeEventType.EXPANSION,
                        direction=mc.direction,
                        description=desc,
                        source_layer="manipulation_cycle",
                    )
                )
        return events

    def _events_from_behavior_divergences(
        self, data: DashboardData
    ) -> list[NarrativeEvent]:
        return [
            NarrativeEvent(
                timestamp=bd.timestamp,
                event_type=_DIVERGENCE_TO_NARRATIVE[bd.divergence_type],
                direction=bd.direction,
                description=bd.description,
                source_layer="behavior_divergence",
            )
            for bd in data.behavior_divergences
        ]

    def _events_from_poi_sweeps(
        self, sweep_events: list[RTOSweepEvent]
    ) -> list[NarrativeEvent]:
        return [
            NarrativeEvent(
                timestamp=se.timestamp,
                event_type=NarrativeEventType.ZONE_MITIGATION,
                direction=se.direction,
                description=(
                    f"Return-to-origin: price swept beyond zone "
                    f"[{se.zone_price_low:,.2f}–{se.zone_price_high:,.2f}] "
                    f"to {se.sweep_extreme:,.2f}, then recovered"
                ),
                source_layer="poi",
            )
            for se in sweep_events
        ]

    # ------------------------------------------------------------------
    # Phase detection
    # ------------------------------------------------------------------

    def _current_phase(self, data: DashboardData) -> ManipulationPhase | None:
        active = [
            mc
            for mc in data.manipulation_cycles
            if mc.status == ManipulationCycleStatus.IN_PROGRESS
        ]
        if not active:
            return None
        latest = max(active, key=lambda mc: mc.accumulation_start)
        return latest.phase

    def _has_recent_failure(self, data: DashboardData) -> bool:
        failed = [
            mc
            for mc in data.manipulation_cycles
            if mc.status == ManipulationCycleStatus.FAILED
        ]
        return bool(failed)

    # ------------------------------------------------------------------
    # Anomaly detection
    # ------------------------------------------------------------------

    def _detect_anomalies(self, data: DashboardData) -> list[NarrativeAnomaly]:
        anomalies: list[NarrativeAnomaly] = []
        anomalies.extend(self._expansion_exhaustion_conflict(data))
        anomalies.extend(self._accumulation_distribution_conflict(data))
        anomalies.extend(self._concentrated_liquidity(data))
        anomalies.extend(self._unconfirmed_choch(data))
        anomalies.extend(self._bos_without_vd(data))
        return anomalies

    def _expansion_exhaustion_conflict(
        self, data: DashboardData
    ) -> list[NarrativeAnomaly]:
        active_expansions = [
            mc
            for mc in data.manipulation_cycles
            if mc.phase == ManipulationPhase.EXPANSION
            and mc.status == ManipulationCycleStatus.IN_PROGRESS
        ]
        if not active_expansions:
            return []

        exhaustion_events = [
            bd
            for bd in data.behavior_divergences
            if bd.divergence_type == DivergenceType.EXHAUSTION
        ]
        anomalies: list[NarrativeAnomaly] = []
        for bd in exhaustion_events:
            for mc in active_expansions:
                if mc.expansion_timestamp and bd.timestamp >= mc.expansion_timestamp:
                    anomalies.append(
                        NarrativeAnomaly(
                            timestamp=bd.timestamp,
                            expected="Sustained volume delta during expansion",
                            observed="Volume delta declining — exhaustion detected",
                            description="Expansion may be losing momentum",
                            severity=AnomalySeverity.HIGH,
                        )
                    )
        return anomalies

    def _accumulation_distribution_conflict(
        self, data: DashboardData
    ) -> list[NarrativeAnomaly]:
        active_accumulations = [
            mc
            for mc in data.manipulation_cycles
            if mc.phase == ManipulationPhase.ACCUMULATION
            and mc.status == ManipulationCycleStatus.IN_PROGRESS
        ]
        if not active_accumulations:
            return []

        distribution_events = [
            bd
            for bd in data.behavior_divergences
            if bd.divergence_type == DivergenceType.DISTRIBUTION
        ]
        anomalies: list[NarrativeAnomaly] = []
        for bd in distribution_events:
            for mc in active_accumulations:
                if mc.accumulation_start <= bd.timestamp <= mc.accumulation_end:
                    anomalies.append(
                        NarrativeAnomaly(
                            timestamp=bd.timestamp,
                            expected="Neutral or positive VD during accumulation",
                            observed="Distribution detected — institutional selling",
                            description=(
                                "Contradictory signals: accumulation phase "
                                "with distribution behavior"
                            ),
                            severity=AnomalySeverity.MEDIUM,
                        )
                    )
        return anomalies

    def _concentrated_liquidity(
        self, data: DashboardData, proximity_pct: float = 0.02
    ) -> list[NarrativeAnomaly]:
        active_zones = [z for z in data.liquidity_zones if not z.is_mitigated]
        if len(active_zones) < 2:
            return []

        from liquidity_hunter.core.domain.enums import LiquiditySide

        anomalies: list[NarrativeAnomaly] = []
        seen_sides: set[LiquiditySide] = set()

        for side in (LiquiditySide.BUY_SIDE, LiquiditySide.SELL_SIDE):
            side_zones = [z for z in active_zones if z.side == side]
            if len(side_zones) < 2:
                continue
            side_zones.sort(key=lambda z: z.price_low)
            cluster_count = 0
            for i in range(len(side_zones) - 1):
                mid_a = (side_zones[i].price_low + side_zones[i].price_high) / 2
                mid_b = (
                    side_zones[i + 1].price_low + side_zones[i + 1].price_high
                ) / 2
                if mid_b > 0 and abs(mid_a - mid_b) / mid_b <= proximity_pct:
                    cluster_count += 1
            if cluster_count >= 1 and side not in seen_sides:
                seen_sides.add(side)
                severity = (
                    AnomalySeverity.HIGH
                    if cluster_count >= 2
                    else AnomalySeverity.MEDIUM
                )
                representative = side_zones[len(side_zones) // 2]
                anomalies.append(
                    NarrativeAnomaly(
                        timestamp=representative.formed_at,
                        expected="Liquidity spread across distinct levels",
                        observed=(
                            f"{cluster_count + 1} {side.value} zones "
                            f"clustered within {proximity_pct:.0%}"
                        ),
                        description=(
                            f"Liquidity heavily concentrated on "
                            f"{side.value} — high sweep probability"
                        ),
                        severity=severity,
                    )
                )
        return anomalies

    def _unconfirmed_choch(
        self, data: DashboardData
    ) -> list[NarrativeAnomaly]:
        if not data.market_structure_events:
            return []

        sorted_events = sorted(
            data.market_structure_events, key=lambda e: e.timestamp
        )
        last_choch = None
        for ev in sorted_events:
            if ev.event == StructureEvent.CHANGE_OF_CHARACTER:
                last_choch = ev
            elif (
                ev.event == StructureEvent.BREAK_OF_STRUCTURE
                and last_choch is not None
                and ev.direction == last_choch.direction
            ):
                last_choch = None

        if last_choch is None:
            return []

        return [
            NarrativeAnomaly(
                timestamp=last_choch.timestamp,
                expected=(
                    f"BOS {last_choch.direction.value} "
                    f"confirming the CHoCH"
                ),
                observed="No subsequent BOS in the same direction",
                description=(
                    f"CHoCH {last_choch.direction.value} unconfirmed"
                    " — reversal not yet validated by structure"
                ),
                severity=AnomalySeverity.MEDIUM,
            )
        ]

    def _bos_without_vd(
        self, data: DashboardData
    ) -> list[NarrativeAnomaly]:
        if not data.market_structure_events or not data.behavior_divergences:
            return []

        sorted_events = sorted(
            data.market_structure_events, key=lambda e: e.timestamp
        )
        last_bos = None
        for ev in sorted_events:
            if ev.event == StructureEvent.BREAK_OF_STRUCTURE:
                last_bos = ev

        if last_bos is None:
            return []

        exhaustion_after_bos = [
            bd
            for bd in data.behavior_divergences
            if (
                bd.divergence_type == DivergenceType.EXHAUSTION
                and bd.timestamp >= last_bos.timestamp
            )
        ]
        if not exhaustion_after_bos:
            return []

        in_expansion = any(
            mc.phase == ManipulationPhase.EXPANSION
            and mc.status == ManipulationCycleStatus.IN_PROGRESS
            for mc in data.manipulation_cycles
        )
        if in_expansion:
            return []

        bd = max(exhaustion_after_bos, key=lambda b: b.timestamp)
        return [
            NarrativeAnomaly(
                timestamp=bd.timestamp,
                expected="Sustained VD after break of structure",
                observed="VD declining post-BOS — exhaustion detected",
                description=(
                    "Structure break not backed by volume delta"
                    " — move may lack institutional conviction"
                ),
                severity=AnomalySeverity.MEDIUM,
            )
        ]

    # ------------------------------------------------------------------
    # Confluence
    # ------------------------------------------------------------------

    def _count_confluence(self, data: DashboardData) -> tuple[int, int]:
        layers: list[tuple[str, MarketDirection | None]] = []

        if data.market_structure_events:
            latest_ms = max(
                data.market_structure_events, key=lambda e: e.timestamp
            )
            layers.append(("structure", latest_ms.direction))

        if data.manipulation_cycles:
            active = [
                mc
                for mc in data.manipulation_cycles
                if mc.status == ManipulationCycleStatus.IN_PROGRESS
            ]
            if active:
                latest_mc = max(active, key=lambda mc: mc.accumulation_start)
                layers.append(("manipulation_cycle", latest_mc.direction))

        if data.behavior_divergences:
            latest_bd = max(
                data.behavior_divergences, key=lambda bd: bd.timestamp
            )
            layers.append(("behavior_divergence", latest_bd.direction))

        layers.append(("higher_tf", data.higher_timeframe_direction))

        total = len(layers)
        if total == 0:
            return 0, 0

        directions = [d for _, d in layers if d is not None]
        if not directions:
            return 0, total

        counts = Counter(directions)
        dominant_count = counts.most_common(1)[0][1]
        return dominant_count, total

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _generate_summary(
        self,
        data: DashboardData,
        phase: ManipulationPhase | None,
        timeline: list[NarrativeEvent],
        anomalies: list[NarrativeAnomaly],
    ) -> str:
        if phase is None:
            if self._has_recent_failure(data):
                return self._failed_summary(data)
            return self._neutral_summary(data, timeline)
        if phase == ManipulationPhase.ACCUMULATION:
            return self._accumulation_summary(data, anomalies)
        if phase == ManipulationPhase.MANIPULATION:
            return self._manipulation_summary(data)
        return self._expansion_summary(data, anomalies)

    def _retail_context(self, data: DashboardData) -> str:
        rb = data.retail_bias
        if rb.dominant_side == RetailPositioning.NEUTRAL:
            return ""
        return (
            f" Retail crowded {rb.dominant_side.value} "
            f"({rb.confidence:.0f}% confidence)."
        )

    def _htf_alignment(self, data: DashboardData, cycle_dir: MarketDirection) -> str:
        htf = data.higher_timeframe_direction
        if htf == MarketDirection.NEUTRAL:
            return ""
        if htf == cycle_dir:
            return " HTF trend aligned."
        return " HTF trend diverges — caution."

    def _neutral_summary(
        self, data: DashboardData, timeline: list[NarrativeEvent]
    ) -> str:
        parts: list[str] = []
        if data.market_structure_events:
            latest = max(
                data.market_structure_events, key=lambda e: e.timestamp
            )
            parts.append(
                f"Structure {latest.direction.value}, latest event: "
                f"{latest.event.value} @ {latest.price_level:,.2f}."
            )
        if data.behavior_divergences:
            latest_bd = max(
                data.behavior_divergences, key=lambda bd: bd.timestamp
            )
            parts.append(
                f"Latest divergence: {latest_bd.divergence_type.value} "
                f"({latest_bd.direction.value})."
            )
        retail = self._retail_context(data)
        if retail:
            parts.append(retail.strip())
        if not parts:
            parts.append("No significant institutional activity detected.")
        return " ".join(parts)

    def _accumulation_summary(
        self, data: DashboardData, anomalies: list[NarrativeAnomaly]
    ) -> str:
        active = [
            mc
            for mc in data.manipulation_cycles
            if mc.phase == ManipulationPhase.ACCUMULATION
            and mc.status == ManipulationCycleStatus.IN_PROGRESS
        ]
        if not active:
            return "Accumulation phase detected."
        mc = max(active, key=lambda m: m.accumulation_start)
        zone_label = _ZONE_TYPE_LABELS.get(
            mc.target_zone_type, mc.target_zone_type.value
        )
        vd_tone = ""
        if mc.accumulation_avg_volume_delta > 0:
            vd_tone = ", VD positive despite lateral price action"
        elif mc.accumulation_avg_volume_delta < 0:
            vd_tone = ", VD negative — supply being absorbed"
        summary = (
            f"Smart money absorbing supply near "
            f"{mc.target_zone_side.value} {zone_label} "
            f"[{mc.target_zone_price_low:,.2f}–"
            f"{mc.target_zone_price_high:,.2f}]{vd_tone}. "
            f"Stops building after "
            f"{mc.consolidation_candles} candles of consolidation."
        )
        summary += self._retail_context(data)
        summary += self._htf_alignment(data, mc.direction)
        if anomalies:
            summary += (
                f" {len(anomalies)} anomaly(ies) detected"
                " — signals may be contradictory."
            )
        return summary

    def _manipulation_summary(self, data: DashboardData) -> str:
        active = [
            mc
            for mc in data.manipulation_cycles
            if mc.phase == ManipulationPhase.MANIPULATION
            and mc.status == ManipulationCycleStatus.IN_PROGRESS
        ]
        if not active:
            return "Manipulation phase detected."
        mc = max(active, key=lambda m: m.accumulation_start)
        extreme = f" @ {mc.sweep_extreme:,.2f}" if mc.sweep_extreme else ""
        vd_spike = ""
        if mc.sweep_volume_delta is not None:
            vd_spike = (
                f", VD spiked {mc.sweep_volume_delta:+.1f}"
                " — probable cascading liquidation"
            )
        retail_trap = ""
        rb = data.retail_bias
        if rb.dominant_side != RetailPositioning.NEUTRAL:
            retail_trap = (
                f" Retail trapped {rb.dominant_side.value}"
                f" at swept {mc.target_zone_side.value} zone."
            )
        summary = (
            f"Stops swept{extreme}{vd_spike}. "
            f"Watching for expansion {mc.direction.value}."
        )
        summary += retail_trap
        summary += self._htf_alignment(data, mc.direction)
        return summary

    def _expansion_summary(
        self, data: DashboardData, anomalies: list[NarrativeAnomaly]
    ) -> str:
        active = [
            mc
            for mc in data.manipulation_cycles
            if mc.phase == ManipulationPhase.EXPANSION
            and mc.status == ManipulationCycleStatus.IN_PROGRESS
        ]
        if not active:
            return "Expansion phase detected."
        mc = max(active, key=lambda m: m.accumulation_start)
        vd_ctx = ""
        if mc.expansion_volume_delta is not None:
            vd_ctx = f" with sustained VD ({mc.expansion_volume_delta:+.1f})"
        summary = (
            f"Impulsive move {mc.direction.value}{vd_ctx}, "
            f"confirming institutional direction. "
            f"Prior {mc.target_zone_side.value} sweep resolved."
        )
        summary += self._retail_context(data)
        summary += self._htf_alignment(data, mc.direction)
        high_anomalies = [
            a for a in anomalies if a.severity == AnomalySeverity.HIGH
        ]
        if high_anomalies:
            summary += (
                " Expansion may be losing momentum"
                " — VD declining post-breakout."
            )
        return summary

    def _failed_summary(self, data: DashboardData) -> str:
        failed = [
            mc
            for mc in data.manipulation_cycles
            if mc.status == ManipulationCycleStatus.FAILED
        ]
        if not failed:
            return "No significant institutional activity detected."
        mc = max(failed, key=lambda m: m.accumulation_start)
        zone_label = _ZONE_TYPE_LABELS.get(
            mc.target_zone_type, mc.target_zone_type.value
        )
        summary = (
            f"Expansion failed to materialize — "
            f"{mc.direction.value} cycle near "
            f"{mc.target_zone_side.value} {zone_label} "
            f"[{mc.target_zone_price_low:,.2f}–"
            f"{mc.target_zone_price_high:,.2f}] invalidated."
        )
        summary += self._retail_context(data)
        return summary
