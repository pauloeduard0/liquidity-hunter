"""Liquidity-hunt synthesizer: who is the resting liquidity of the current move.

When the current timeframe's structure runs counter to the higher-timeframe
trend (e.g. a bearish CHoCH inside an H4 uptrend), the traders entering with
that counter-move become the resting liquidity the larger trend feeds on:
their stops cluster at equal highs/lows and their leveraged positions project
liquidation bands just beyond price. This engine reads the assembled
:class:`DashboardData` snapshot and describes how far the capture of those
nearby opposing pools has progressed — which pools are mapped, which were
consumed since the counter-trend structure began (zone sweeps, liquidation
hits, OI flush events), and whether open interest is still unwinding against
the hunted side.

Lives in ``app/`` because it is a composition-level synthesizer depending on
outputs from several layers (structure, liquidity, psychology), like
:class:`~liquidity_hunter.app.narrative.NarrativeEngine`. Purely descriptive:
it states who the liquidity is and when it was captured, never what to do.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from liquidity_hunter.core.domain.enums import (
    LiquidityHuntPhase,
    LiquidityHuntTargetKind,
    LiquiditySide,
    LiquidityZoneType,
    MarketDirection,
    OIParticipation,
    OIRegime,
    RetailPositioning,
    StructureEvent,
)
from liquidity_hunter.core.domain.liquidity_hunt import (
    LiquidityHuntState,
    LiquidityHuntTarget,
)

if TYPE_CHECKING:
    from datetime import datetime

    from liquidity_hunter.app.dashboard_data import DashboardData
    from liquidity_hunter.core.domain.liquidation import LiquidationBand
    from liquidity_hunter.core.domain.market_structure import MarketStructure

# Liquidation bands projected from nearby entry clusters can sit almost on top
# of each other (several entries, several tiers); near-equal levels are one
# pool, so they are clustered within this fraction of price (mirroring the
# estimator's own entry clustering) and the strongest band represents each.
_BAND_CLUSTER_PCT = 0.004


class LiquidityHuntEngine:
    """Builds a :class:`LiquidityHuntState` from a completed :class:`DashboardData`.

    ``proximity_pct`` bounds which opposing pools count as "nearby" targets of
    the current corrective move (default 2%, in line with the other analyzers'
    proximity windows). ``max_targets`` caps the *reported* target list; the
    captured/total counters always reflect the full mapped set.
    """

    def __init__(self, proximity_pct: float = 0.02, max_targets: int = 8) -> None:
        self.proximity_pct = proximity_pct
        self.max_targets = max_targets

    def build(self, data: DashboardData) -> LiquidityHuntState:
        htf = data.higher_timeframe_direction
        trend, flip_timestamp = self._current_trend(data.internal_structure_events)
        directional = (MarketDirection.BULLISH, MarketDirection.BEARISH)
        counter_trend = (
            htf in directional
            and trend in directional
            and trend is not htf
            and flip_timestamp is not None
        )
        if not counter_trend or trend is None or flip_timestamp is None:
            return LiquidityHuntState(
                symbol=data.symbol,
                timeframe=data.timeframe,
                phase=LiquidityHuntPhase.NONE,
                hunted_side=RetailPositioning.NEUTRAL,
                description=(
                    "Current-timeframe structure is aligned with the higher "
                    "timeframe; no counter-trend pool of entrants in play."
                ),
            )

        # In a bullish HTF trend, a bearish correction's sellers are the fuel:
        # their stops/liquidations rest on the buy side above price. Mirror for
        # a bearish HTF trend. The capture direction is the sweep/flush side
        # that consumes them (an upward wick grabs short liquidity).
        hunted_short = htf is MarketDirection.BULLISH
        hunted_side = RetailPositioning.SHORT if hunted_short else RetailPositioning.LONG
        capture_direction = (
            MarketDirection.BULLISH if hunted_short else MarketDirection.BEARISH
        )

        targets = [
            *self._zone_targets(data, hunted_short, flip_timestamp),
            *self._band_targets(data, hunted_short, flip_timestamp),
        ]
        targets.sort(key=lambda t: abs(t.price_level - data.current_price))
        captured = [t for t in targets if t.captured]

        last_flush = self._last_flush(data, capture_direction, flip_timestamp)
        swept_since_flip = self._swept_since(
            data.internal_structure_events, capture_direction, flip_timestamp
        )
        oi_unwinding = self._oi_unwinding(data, hunted_short)

        all_captured = bool(targets) and len(captured) == len(targets)
        captured_at: datetime | None = None
        if all_captured and not oi_unwinding:
            phase = LiquidityHuntPhase.CAPTURED
            captured_at = max(
                (t.captured_at for t in captured if t.captured_at is not None),
                default=last_flush,
            )
        elif captured or last_flush is not None or swept_since_flip or oi_unwinding:
            phase = LiquidityHuntPhase.HUNT_IN_PROGRESS
        else:
            phase = LiquidityHuntPhase.COUNTER_TREND

        return LiquidityHuntState(
            symbol=data.symbol,
            timeframe=data.timeframe,
            phase=phase,
            hunted_side=hunted_side,
            correction_direction=trend,
            counter_structure_timestamp=flip_timestamp,
            targets=targets[: self.max_targets],
            targets_captured=len(captured),
            targets_total=len(targets),
            oi_unwinding=oi_unwinding,
            last_flush_timestamp=last_flush,
            captured_at=captured_at,
            description=self._describe(
                phase=phase,
                hunted_short=hunted_short,
                htf=htf,
                trend=trend,
                captured_count=len(captured),
                total=len(targets),
                oi_unwinding=oi_unwinding,
                last_flush=last_flush,
            ),
        )

    # ------------------------------------------------------------------
    # Current-timeframe structural trend
    # ------------------------------------------------------------------

    @staticmethod
    def _current_trend(
        events: list[MarketStructure],
    ) -> tuple[MarketDirection | None, datetime | None]:
        """Replay the internal structure stream into (trend, flip timestamp).

        BOS and CHoCH set the trend to their direction; a ``CHOCH_FAILED``
        reverts it (the failed CHoCH's direction never held). Provisional
        live-edge marks and descriptive pivot/sweep labels are ignored. The
        flip timestamp is the event that last *changed* the trend — the start
        of the current corrective leg, used to separate pools captured by this
        move from ones consumed long before it.
        """
        trend: MarketDirection | None = None
        flip_timestamp: datetime | None = None
        for event in sorted(events, key=lambda e: e.timestamp):
            if event.provisional:
                continue
            if event.event in (
                StructureEvent.BREAK_OF_STRUCTURE,
                StructureEvent.CHANGE_OF_CHARACTER,
            ):
                new_trend = event.direction
            elif event.event is StructureEvent.CHOCH_FAILED:
                new_trend = (
                    MarketDirection.BEARISH
                    if event.direction is MarketDirection.BULLISH
                    else MarketDirection.BULLISH
                )
            else:
                continue
            if new_trend is not trend:
                flip_timestamp = event.timestamp
            trend = new_trend
        return trend, flip_timestamp

    # ------------------------------------------------------------------
    # Targets: the nearby opposing pools
    # ------------------------------------------------------------------

    def _zone_targets(
        self, data: DashboardData, hunted_short: bool, flip_timestamp: datetime
    ) -> list[LiquidityHuntTarget]:
        """Equal highs/lows within proximity — the classic clustered-stop pools."""
        price = data.current_price
        zone_type = (
            LiquidityZoneType.EQUAL_HIGHS if hunted_short else LiquidityZoneType.EQUAL_LOWS
        )
        label = "EQH" if hunted_short else "EQL"
        targets: list[LiquidityHuntTarget] = []
        for zone in data.liquidity_zones:
            if zone.zone_type is not zone_type:
                continue
            mid = (zone.price_low + zone.price_high) / 2
            if mid <= 0 or abs(mid - price) / price > self.proximity_pct:
                continue
            if zone.is_mitigated:
                # Only sweeps that happened during this corrective leg count as
                # captures of *its* liquidity; older sweeps are history.
                if zone.invalidated_at is None or zone.invalidated_at < flip_timestamp:
                    continue
                targets.append(
                    LiquidityHuntTarget(
                        kind=LiquidityHuntTargetKind.EQUAL_LEVEL,
                        label=label,
                        price_level=mid,
                        captured=True,
                        captured_at=zone.invalidated_at,
                    )
                )
            else:
                ahead = mid > price if hunted_short else mid < price
                if not ahead:
                    continue
                targets.append(
                    LiquidityHuntTarget(
                        kind=LiquidityHuntTargetKind.EQUAL_LEVEL,
                        label=label,
                        price_level=mid,
                    )
                )
        return targets

    def _band_targets(
        self, data: DashboardData, hunted_short: bool, flip_timestamp: datetime
    ) -> list[LiquidityHuntTarget]:
        """Nearby leveraged-liquidation bands on the hunted side.

        Shorts liquidate above price (``BUY_SIDE`` bands), longs below. A live
        band (``end_time`` is None) is an intact pool; one whose ``end_time``
        falls after the counter-trend flip was consumed by this move. Bands
        clustered within ``_BAND_CLUSTER_PCT`` are one pool — represented by
        the strongest member, and intact as long as any member is still live.
        """
        if data.liquidation_map is None:
            return []
        price = data.current_price
        band_side = LiquiditySide.BUY_SIDE if hunted_short else LiquiditySide.SELL_SIDE

        candidates: list[tuple[float, LiquidationBand]] = []
        for band in data.liquidation_map.bands:
            if band.side is not band_side:
                continue
            mid = (band.price_low + band.price_high) / 2
            if abs(mid - price) / price > self.proximity_pct:
                continue
            if band.end_time is None:
                ahead = mid > price if hunted_short else mid < price
                if not ahead:
                    continue
            elif band.end_time < flip_timestamp:
                continue
            candidates.append((mid, band))
        candidates.sort(key=lambda c: c[0])

        groups: list[list[tuple[float, LiquidationBand]]] = []
        for candidate in candidates:
            if groups and (candidate[0] - groups[-1][-1][0]) / price <= _BAND_CLUSTER_PCT:
                groups[-1].append(candidate)
            else:
                groups.append([candidate])

        targets: list[LiquidityHuntTarget] = []
        for group in groups:
            live = [c for c in group if c[1].end_time is None]
            pool = live if live else group
            mid, band = max(pool, key=lambda c: c[1].intensity)
            is_captured = not live
            targets.append(
                LiquidityHuntTarget(
                    kind=LiquidityHuntTargetKind.LIQUIDATION_BAND,
                    label=f"{band.leverage}x",
                    price_level=mid,
                    captured=is_captured,
                    captured_at=band.end_time if is_captured else None,
                )
            )
        return targets

    # ------------------------------------------------------------------
    # Capture evidence
    # ------------------------------------------------------------------

    @staticmethod
    def _last_flush(
        data: DashboardData, capture_direction: MarketDirection, flip_timestamp: datetime
    ) -> datetime | None:
        """Most recent OI-flush event against the hunted side during this leg."""
        if data.oi_analysis is None:
            return None
        stamps = [
            qualified.event_timestamp
            for qualified in data.oi_analysis.qualified_events
            if qualified.participation is OIParticipation.FLUSH
            and qualified.direction is capture_direction
            and qualified.event_timestamp >= flip_timestamp
        ]
        return max(stamps, default=None)

    @staticmethod
    def _swept_since(
        events: list[MarketStructure],
        capture_direction: MarketDirection,
        flip_timestamp: datetime,
    ) -> bool:
        """Whether a capture-side liquidity sweep fired during this leg."""
        return any(
            event.event is StructureEvent.LIQUIDITY_SWEEP
            and event.direction is capture_direction
            and event.timestamp >= flip_timestamp
            for event in events
        )

    @staticmethod
    def _oi_unwinding(data: DashboardData, hunted_short: bool) -> bool:
        """Whether open interest is still burning the hunted side.

        Short covering while shorts are hunted (or long liquidation while
        longs are) means the move is still feeding on forced position closes —
        the hunt is not concluded even if the mapped pools were all touched.
        """
        regime = data.oi_analysis.current_regime if data.oi_analysis else None
        if regime is None:
            return False
        expected = OIRegime.SHORT_COVERING if hunted_short else OIRegime.LONG_LIQUIDATION
        return regime.regime is expected

    # ------------------------------------------------------------------
    # Description
    # ------------------------------------------------------------------

    @staticmethod
    def _describe(
        phase: LiquidityHuntPhase,
        hunted_short: bool,
        htf: MarketDirection,
        trend: MarketDirection,
        captured_count: int,
        total: int,
        oi_unwinding: bool,
        last_flush: datetime | None,
    ) -> str:
        side_word = "shorts" if hunted_short else "longs"
        pool_side = "buy-side" if hunted_short else "sell-side"
        regime_word = "short covering" if hunted_short else "long liquidation"
        base = (
            f"{trend.value.capitalize()} move against a {htf.value} higher-timeframe "
            f"trend: {side_word} entering it are the resting liquidity."
        )
        if phase is LiquidityHuntPhase.CAPTURED:
            oi_note = " Open interest no longer unwinding." if total else ""
            return (
                f"{base} All {total} mapped {pool_side} pool(s) nearby were "
                f"captured during this leg.{oi_note}"
            )
        parts = [base]
        if total:
            parts.append(f"{captured_count}/{total} nearby {pool_side} pool(s) captured.")
        else:
            parts.append(f"No {pool_side} pools mapped within proximity.")
        if last_flush is not None:
            parts.append(f"Leveraged {side_word} flushed at {last_flush:%Y-%m-%d %H:%M}.")
        if oi_unwinding:
            parts.append(f"OI regime still {regime_word} — {side_word} still being consumed.")
        return " ".join(parts)
