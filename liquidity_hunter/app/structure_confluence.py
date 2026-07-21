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
    Candle,
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

# Per-factor weight; present factors sum (capped at 100) into `score`. The two
# cross-timeframe factors (HTF trend agreement + reaction at an HTF order block)
# carry the most weight — higher-timeframe context is the strongest confidence
# read in SMC.
_FACTOR_WEIGHTS: dict[ConfluenceFactor, float] = {
    ConfluenceFactor.HTF_ALIGNMENT: 20.0,
    ConfluenceFactor.HTF_ORDER_BLOCK: 20.0,
    ConfluenceFactor.VSA_VOLUME: 15.0,
    ConfluenceFactor.ORDER_BLOCK: 15.0,
    ConfluenceFactor.OI_PARTICIPATION: 12.0,
    ConfluenceFactor.VOLUME_DELTA: 9.0,
    ConfluenceFactor.LIQUIDITY_SWEEP: 9.0,
}

# Evidence windows, in candles, around the break (BOS).
_LOOKBACK = 5  # VSA / volume-delta evidence just before/at the break
_LOOKAHEAD = 2  # ...allowing the confirming close a candle or two later
_SWEEP_LOOKBACK = 10  # a stop-hunt sweep preceding the break
_OB_PRICE_BUFFER = 0.001  # 0.1% tolerance for "the break came from this OB"

# A CHoCH (reversal) is different from a BOS: its confirming candle sits up at
# the broken level, but the fuel that launched the reversal — the sweep, climax
# and thrust — formed candles earlier down at the leg extreme, and the level is
# then *retested and defended* candles later. So a CHoCH's evidence window is
# anchored to the reversal origin (the leg extreme) and grows forward until the
# next opposite/failing event (or a cap), rather than a tight window around the
# confirmation. Because the dashboard polls, this forward end extends with each
# refresh, so a CHoCH accretes confluence as its level is defended over time.
_CHOCH_FORWARD_CAP = 60  # candles a CHoCH's evidence window may extend forward

# A recently-invalidated OB at the break level is a *breaker retest*: price
# broke the zone, then returned to break structure at it again — still real
# confluence, but counted at reduced weight. `_OB_INVALIDATION_LOOKBACK` bounds
# "recently" (older invalidations are stale coincidental levels, not breakers).
_OB_INVALIDATION_LOOKBACK = 50  # candles
_BREAKER_RETEST_WEIGHT_FACTOR = 0.5


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

        # Structural flow (index-ordered BOS/CHoCH/CHOCH_FAILED) — used to bound
        # a CHoCH's forward evidence window at the next opposite/failing event.
        flow = sorted(
            (
                (idx_by_ts[e.timestamp], e)
                for e in events
                if e.timestamp in idx_by_ts
                and e.event
                in (
                    StructureEvent.BREAK_OF_STRUCTURE,
                    StructureEvent.CHANGE_OF_CHARACTER,
                    StructureEvent.CHOCH_FAILED,
                )
            ),
            key=lambda pair: pair[0],
        )
        last_idx = len(candles) - 1

        results: list[StructureConfluence] = []
        for ev in events:
            if ev.event not in _QUALIFIED_EVENTS or ev.provisional:
                continue
            ev_idx = idx_by_ts.get(ev.timestamp)
            if ev_idx is None:
                continue

            is_choch = ev.event == StructureEvent.CHANGE_OF_CHARACTER

            # Resolve the evidence window and OB matching parameters. A BOS uses
            # a tight window around the break; a CHoCH spans from the reversal
            # origin (leg extreme) forward to the next opposite/failing event.
            if is_choch:
                origin_idx, origin_price = self._reversal_origin(ev, ev_idx, candles, idx_by_ts)
                fwd_idx = self._choch_forward_bound(ev, ev_idx, flow, last_idx)
                vsa_lo, vsa_hi = origin_idx, fwd_idx
                sweep_lo, sweep_hi = origin_idx, fwd_idx
                ob_levels = [origin_price]
                if ev.reference_price_level is not None:
                    ob_levels.append(ev.reference_price_level)
                ob_created_bound = candles[fwd_idx].timestamp
                ob_retest_hi = fwd_idx
            else:
                vsa_lo, vsa_hi = ev_idx - _LOOKBACK, ev_idx + _LOOKAHEAD
                sweep_lo, sweep_hi = ev_idx - _SWEEP_LOOKBACK, ev_idx - 1
                level = (
                    ev.reference_price_level
                    if ev.reference_price_level is not None
                    else ev.price_level
                )
                ob_levels = [level]
                ob_created_bound = ev.timestamp
                ob_retest_hi = ev_idx - 1

            # (factor, weight multiplier) contributions for this event.
            contribs: list[tuple[ConfluenceFactor, float]] = []

            # Higher-timeframe alignment: the break agrees with the HTF trend.
            if (
                data.higher_timeframe_direction != MarketDirection.NEUTRAL
                and ev.direction == data.higher_timeframe_direction
            ):
                contribs.append((ConfluenceFactor.HTF_ALIGNMENT, 1.0))

            # VSA volume signal aligned with the break, within its window.
            if any(
                ev.direction in vsa_by_idx.get(j, ())
                for j in range(vsa_lo, vsa_hi + 1)
            ):
                contribs.append((ConfluenceFactor.VSA_VOLUME, 1.0))

            # Order block the break launched from / reacted at / was defended by
            # (current TF). An active OB counts full; a recently-invalidated one
            # (breaker retest) at reduced weight.
            ob_match = self._order_block_match(
                ev, data.poi_zones, ev_idx, idx_by_ts,
                levels=ob_levels, created_bound=ob_created_bound, retest_hi=ob_retest_hi,
            )
            if ob_match == "active":
                contribs.append((ConfluenceFactor.ORDER_BLOCK, 1.0))
            elif ob_match == "retest":
                contribs.append((ConfluenceFactor.ORDER_BLOCK, _BREAKER_RETEST_WEIGHT_FACTOR))

            # A higher-timeframe order block the break reacted at (stronger).
            # `htf_poi_zones` carries only ACTIVE zones, so no retest here.
            htf_match = self._order_block_match(
                ev, data.htf_poi_zones, ev_idx, idx_by_ts,
                levels=ob_levels, created_bound=ob_created_bound, retest_hi=ob_retest_hi,
            )
            if htf_match == "active":
                contribs.append((ConfluenceFactor.HTF_ORDER_BLOCK, 1.0))

            # OI: new money entering the break.
            if oi_participation.get((ev.timestamp, ev.event)) == OIParticipation.NEW_MONEY:
                contribs.append((ConfluenceFactor.OI_PARTICIPATION, 1.0))

            # Net taker aggression aligned with the break at the break candle.
            if self._volume_delta_aligned(ev.direction, vds[ev_idx], candles[ev_idx].volume):
                contribs.append((ConfluenceFactor.VOLUME_DELTA, 1.0))

            # A stop-hunt sweep within the window (any side — a bullish reversal
            # sweeps the lows, a wick-down/bearish-labeled sweep).
            if any(sweep_lo <= s <= sweep_hi for s in sweep_idxs):
                contribs.append((ConfluenceFactor.LIQUIDITY_SWEEP, 1.0))

            factors = [f for f, _ in contribs]
            score = min(100.0, sum(_FACTOR_WEIGHTS[f] * w for f, w in contribs))
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
    def _order_block_match(
        ev: MarketStructure,
        obs: list[POIZone],
        ev_idx: int,
        idx_by_ts: dict[datetime, int],
        *,
        levels: list[float],
        created_bound: datetime,
        retest_hi: int,
    ) -> str | None:
        """Classify the OB backing this break: ``"active"`` (an active OB whose
        range holds one of ``levels``), ``"retest"`` (a recently-invalidated OB
        at that level — a breaker retest), or ``None``. Active wins over retest.

        ``created_bound`` is the latest OB-confirmation timestamp accepted (a
        BOS caps it at the break; a CHoCH extends it to the forward window end,
        so an OB that confirmed while its level was being defended counts).
        ``retest_hi`` is the last candle index at which an invalidation still
        reads as a fresh breaker retest.
        """
        found_retest = False
        for ob in obs:
            if ob.direction != ev.direction:
                continue
            if ob.created_at > created_bound:
                continue
            buffer = ob.price_high * _OB_PRICE_BUFFER
            if not any(ob.price_low - buffer <= lvl <= ob.price_high + buffer for lvl in levels):
                continue
            if ob.status == POIZoneStatus.ACTIVE:
                return "active"
            if ob.invalidated_at is None:
                continue
            inv_idx = idx_by_ts.get(ob.invalidated_at)
            if inv_idx is not None and ev_idx - _OB_INVALIDATION_LOOKBACK <= inv_idx <= retest_hi:
                found_retest = True
        return "retest" if found_retest else None

    @staticmethod
    def _reversal_origin(
        ev: MarketStructure,
        ev_idx: int,
        candles: list[Candle],
        idx_by_ts: dict[datetime, int],
    ) -> tuple[int, float]:
        """The leg extreme the CHoCH reversal launched from: the lowest low
        (bullish CHoCH) / highest high (bearish) between the broken reference's
        formation and the confirmation candle. Returns ``(index, price)``.
        """
        ref_idx = (
            idx_by_ts.get(ev.reference_timestamp)
            if ev.reference_timestamp is not None
            else None
        )
        start = ref_idx if ref_idx is not None else max(0, ev_idx - _LOOKBACK)
        start = max(0, min(start, ev_idx))
        bullish = ev.direction == MarketDirection.BULLISH
        best_idx = start
        best_price = candles[start].low if bullish else candles[start].high
        for i in range(start, ev_idx + 1):
            if bullish and candles[i].low < best_price:
                best_idx, best_price = i, candles[i].low
            elif not bullish and candles[i].high > best_price:
                best_idx, best_price = i, candles[i].high
        return best_idx, best_price

    @staticmethod
    def _choch_forward_bound(
        ev: MarketStructure,
        ev_idx: int,
        flow: list[tuple[int, MarketStructure]],
        last_idx: int,
    ) -> int:
        """Forward end of a CHoCH's evidence window: the first later
        opposite-direction BOS/CHoCH, or a same-direction CHOCH_FAILED (the
        reversal died), else a fixed cap — clamped to the last candle. Grows
        with the series until such an event forms.
        """
        term = None
        for i, e in flow:
            if i <= ev_idx:
                continue
            terminates = (
                e.event
                in (StructureEvent.BREAK_OF_STRUCTURE, StructureEvent.CHANGE_OF_CHARACTER)
                and e.direction != ev.direction
            ) or (
                e.event == StructureEvent.CHOCH_FAILED and e.direction == ev.direction
            )
            if terminates:
                term = i
                break
        cap = ev_idx + _CHOCH_FORWARD_CAP
        bound = min(cap, term) if term is not None else cap
        return min(bound, last_idx)

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
