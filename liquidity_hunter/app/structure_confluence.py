"""Structure-confluence synthesizer: how much evidence backs each break.

For every confirmed BOS/CHoCH the chart renders, this engine tallies the
independent observations that agree with the break's direction near it — a VSA
volume signal, an order block the move launched from, new money entering (OI),
aligned taker aggression, a preceding stop-hunt sweep — into a descriptive
:class:`StructureConfluence` per event. A break with four confirming layers
reads as a strong structure; one standing alone reads as weak.

Lives in ``app/`` because it is a composition-level synthesizer depending on
outputs from several layers (structure, liquidity, psychology), like
:class:`~liquidity_hunter.app.narrative.NarrativeEngine`. Purely descriptive:
it counts how many reads confluence on the structure, never what to do.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from liquidity_hunter.core.domain import (
    ConfluenceFactor,
    MarketDirection,
    MarketStructure,
    OIParticipation,
    POIZone,
    POIZoneStatus,
    StructureConfluence,
    StructureEvent,
)
from liquidity_hunter.indicators import volume_delta_series

if TYPE_CHECKING:
    from liquidity_hunter.app.dashboard_data import DashboardData

# Events that carry a directional structural break worth qualifying. Pivot
# labels, sweeps and failed/invalidation marks are skipped.
_QUALIFIED_EVENTS = frozenset(
    {StructureEvent.BREAK_OF_STRUCTURE, StructureEvent.CHANGE_OF_CHARACTER}
)

# Per-factor weight; present factors sum (capped at 100) into `score`.
_FACTOR_WEIGHTS: dict[ConfluenceFactor, float] = {
    ConfluenceFactor.VSA_VOLUME: 25.0,
    ConfluenceFactor.ORDER_BLOCK: 25.0,
    ConfluenceFactor.OI_PARTICIPATION: 20.0,
    ConfluenceFactor.VOLUME_DELTA: 15.0,
    ConfluenceFactor.LIQUIDITY_SWEEP: 15.0,
}

# Evidence windows, in candles, around the break.
_LOOKBACK = 5  # VSA / volume-delta evidence just before/at the break
_LOOKAHEAD = 2  # ...allowing the confirming close a candle or two later
_SWEEP_LOOKBACK = 10  # a stop-hunt sweep preceding the break
_OB_PRICE_BUFFER = 0.001  # 0.1% tolerance for "the break came from this OB"


class StructureConfluenceEngine:
    """Builds per-event :class:`StructureConfluence` from a `DashboardData`."""

    def build(self, data: DashboardData) -> list[StructureConfluence]:
        events = data.internal_structure_events
        candles = data.candles
        if not events or not candles:
            return []

        idx_by_ts = {c.timestamp: i for i, c in enumerate(candles)}
        vds = volume_delta_series(candles)

        # Pre-index evidence by candle index for cheap window lookups.
        vsa_by_idx: dict[int, list[MarketDirection]] = {}
        for sig in data.volume_spread_signals:
            i = idx_by_ts.get(sig.timestamp)
            if i is not None:
                vsa_by_idx.setdefault(i, []).append(sig.direction)

        sweep_idxs = [
            idx_by_ts[e.timestamp]
            for e in events
            if e.event == StructureEvent.LIQUIDITY_SWEEP and e.timestamp in idx_by_ts
        ]

        oi_participation: dict[tuple[datetime, StructureEvent], OIParticipation] = {}
        if data.oi_analysis is not None:
            for qe in data.oi_analysis.qualified_events:
                oi_participation[(qe.event_timestamp, qe.event_type)] = qe.participation

        active_obs = [z for z in data.poi_zones if z.status == POIZoneStatus.ACTIVE]

        results: list[StructureConfluence] = []
        for ev in events:
            if ev.event not in _QUALIFIED_EVENTS or ev.provisional:
                continue
            ev_idx = idx_by_ts.get(ev.timestamp)
            if ev_idx is None:
                continue

            factors: list[ConfluenceFactor] = []

            # VSA volume signal aligned with the break, in its neighborhood.
            lo = ev_idx - _LOOKBACK
            hi = ev_idx + _LOOKAHEAD
            if any(
                ev.direction in vsa_by_idx.get(j, ())
                for j in range(lo, hi + 1)
            ):
                factors.append(ConfluenceFactor.VSA_VOLUME)

            # Order block the break launched from / reacted at.
            if self._has_order_block(ev, active_obs):
                factors.append(ConfluenceFactor.ORDER_BLOCK)

            # OI: new money entering the break.
            if oi_participation.get((ev.timestamp, ev.event)) == OIParticipation.NEW_MONEY:
                factors.append(ConfluenceFactor.OI_PARTICIPATION)

            # Net taker aggression aligned with the break at the break candle.
            if self._volume_delta_aligned(ev.direction, vds[ev_idx], candles[ev_idx].volume):
                factors.append(ConfluenceFactor.VOLUME_DELTA)

            # A stop-hunt sweep shortly before the break.
            if any(ev_idx - _SWEEP_LOOKBACK <= s < ev_idx for s in sweep_idxs):
                factors.append(ConfluenceFactor.LIQUIDITY_SWEEP)

            score = min(100.0, sum(_FACTOR_WEIGHTS[f] for f in factors))
            results.append(
                StructureConfluence(
                    symbol=data.symbol,
                    timeframe=data.timeframe,
                    event_timestamp=ev.timestamp,
                    event_type=ev.event,
                    direction=ev.direction,
                    price_level=ev.price_level,
                    factors=factors,
                    score=score,
                    description=_describe(ev.event, ev.direction, factors, score),
                )
            )
        return results

    @staticmethod
    def _has_order_block(ev: MarketStructure, active_obs: list[POIZone]) -> bool:
        level = ev.reference_price_level if ev.reference_price_level is not None else ev.price_level
        for ob in active_obs:
            if ob.direction != ev.direction:
                continue
            if ob.created_at > ev.timestamp:
                continue
            buffer = ob.price_high * _OB_PRICE_BUFFER
            if ob.price_low - buffer <= level <= ob.price_high + buffer:
                return True
        return False

    @staticmethod
    def _volume_delta_aligned(direction: MarketDirection, vd: float, volume: float) -> bool:
        if volume <= 0 or abs(vd) < 0.1 * volume:
            return False
        if direction == MarketDirection.BULLISH:
            return vd > 0
        if direction == MarketDirection.BEARISH:
            return vd < 0
        return False


def _describe(
    event: StructureEvent,
    direction: MarketDirection,
    factors: list[ConfluenceFactor],
    score: float,
) -> str:
    name = "BOS" if event == StructureEvent.BREAK_OF_STRUCTURE else "CHoCH"
    if not factors:
        return f"{direction.value} {name} with no confirming confluence"
    tags = ", ".join(f.value for f in factors)
    return (
        f"{direction.value} {name} confirmed by {len(factors)} factor(s) "
        f"[{tags}] — score {score:.0f}"
    )
