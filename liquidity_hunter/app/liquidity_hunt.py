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

from datetime import timedelta
from statistics import fmean
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
    VSAPattern,
)
from liquidity_hunter.core.domain.liquidity_hunt import (
    LiquidityHuntEpisode,
    LiquidityHuntState,
    LiquidityHuntTarget,
)

if TYPE_CHECKING:
    from datetime import datetime

    from liquidity_hunter.app.dashboard_data import DashboardData
    from liquidity_hunter.core.domain.candle import Candle
    from liquidity_hunter.core.domain.liquidation import LiquidationBand
    from liquidity_hunter.core.domain.market_structure import MarketStructure

# Liquidation bands projected from nearby entry clusters can sit almost on top
# of each other (several entries, several tiers); near-equal levels are one
# pool, so they are clustered within this fraction of price (mirroring the
# estimator's own entry clustering) and the strongest band represents each.
_BAND_CLUSTER_PCT = 0.004

# Capture-side grabs closer than this many candles are one sweep cluster, so a
# single liquidity grab closes one hunt (not several near-identical ones).
_GRAB_MERGE_CANDLES = 3

# Weighted capture composition (confirmed with the user 2026-07-20): a grab
# closes a hunt when co-located evidence reaches _CAPTURE_THRESHOLD. Flat
# weights (not confidence-scaled); VSA is already gated by its own analyzer, so
# its mere presence on the grab side is the signal.
#
# Threshold 6 (raised 3 -> 5 -> 6 on 2026-07-20): a lone strong signal (a
# single sweep / VSA / OI flush = 3) is not a capture — internal sweeps are
# frequent, so lone-signal grabs over-fired; a strong signal plus only the pool
# it swept (3 + zone 2 = 5) was still too many. A real turning point requires
# *two strong signals* in confluence (sweep + VSA, sweep + OI flush, or
# VSA + OI = 6). Tunable — the user is visually backtesting these.
_CAPTURE_THRESHOLD = 6.0
_WEIGHT_SWEEP = 3.0
_WEIGHT_VSA = 3.0
_WEIGHT_OI_FLUSH = 3.0
_WEIGHT_ZONE = 2.0
_WEIGHT_DELTA_MODIFIER = 1.0
# Net taker aggression must exceed this fraction of the candle's volume to count
# as a directional confirmation (|2*taker_buy - volume| / volume).
_DELTA_MODIFIER_MIN_RATIO = 0.1

# VSA capture patterns mapped by the *grab side* (the wick the sweep rejects),
# which is the mirror of VSA's own implied `direction`. A hunted-short capture
# is an up-sweep rejecting the high; a hunted-long capture rejects the low.
_VSA_SHORT_CAPTURE: frozenset[VSAPattern] = frozenset(
    {VSAPattern.UP_THRUST, VSAPattern.BUYING_CLIMAX}
)
_VSA_LONG_CAPTURE: frozenset[VSAPattern] = frozenset(
    {VSAPattern.DOWN_THRUST, VSAPattern.SELLING_CLIMAX}
)


class LiquidityHuntEngine:
    """Builds a :class:`LiquidityHuntState` from a completed :class:`DashboardData`.

    ``proximity_pct`` bounds which opposing pools count as "nearby" targets of
    the current corrective move (default 2%, in line with the other analyzers'
    proximity windows). ``proximity_atr`` (default ``None`` = off) makes that
    bound volatility-normalized instead: N x the visible series' mean
    true-range% of price, so "nearby" means the same number of typical candles
    on every asset/timeframe — a fixed 2% maps far too many pools on a calm
    M15 chart and almost none on a volatile daily (the same lesson as the
    detector's `bos_leg_origin_release_gap_atr`). Falls back to
    ``proximity_pct`` when the series is too short to measure a range.
    ``max_targets`` caps the *reported* target list; the captured/total
    counters always reflect the full mapped set.
    """

    def __init__(
        self,
        proximity_pct: float = 0.02,
        proximity_atr: float | None = None,
        max_targets: int = 8,
    ) -> None:
        self.proximity_pct = proximity_pct
        self.proximity_atr = proximity_atr
        self.max_targets = max_targets

    def _effective_proximity(self, candles: list[Candle]) -> float:
        """The proximity bound for this snapshot (ATR-normalized when enabled)."""
        if self.proximity_atr is None or len(candles) < 2:
            return self.proximity_pct
        mean_tr_pct = fmean(
            max(
                curr.high - curr.low,
                abs(curr.high - prev.close),
                abs(curr.low - prev.close),
            )
            / curr.close
            for prev, curr in zip(candles, candles[1:], strict=False)
        )
        return self.proximity_atr * mean_tr_pct

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

        proximity = self._effective_proximity(data.candles)
        targets = [
            *self._zone_targets(data, hunted_short, flip_timestamp, proximity),
            *self._band_targets(data, hunted_short, flip_timestamp, proximity),
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
    # Historical (concluded) hunts
    # ------------------------------------------------------------------

    def build_history(self, data: DashboardData) -> list[LiquidityHuntEpisode]:
        """Reconstruct every *past* counter-trend hunt, anchored to its grab.

        A hunt is the **near-term** move that clears the counter-trend
        entrants' liquidity so price can flow on: within a counter-trend leg
        (structure opposed the higher-timeframe trend), each capture-side
        liquidity grab — an up-sweep of the shorts' stops for hunted shorts,
        a down-sweep for hunted longs (plus hunted-side equal-level zones
        swept) — **closes one hunt at that grab**. The episode therefore runs
        from the leg flip (or the previous grab) to the grab itself, not all
        the way to the eventual trend reversal, so a single leg can hold
        several short consecutive hunts (the SOL "captured, then a new
        shorts-hunted opens" case). Grabs inside the *current* open leg are
        included as long as they already happened; the still-open tail after
        the last grab is the live :class:`LiquidityHuntState`, not history.
        """
        htf = data.higher_timeframe_direction
        directional = (MarketDirection.BULLISH, MarketDirection.BEARISH)
        if htf not in directional:
            return []

        segments = self._trend_segments(data.internal_structure_events)
        now = data.candles[-1].timestamp if data.candles else None
        merge_gap = self._grab_merge_gap(data.candles)

        episodes: list[LiquidityHuntEpisode] = []
        for idx, (direction, start) in enumerate(segments):
            # Leg spans until the next flip, or the live edge for the open leg.
            end = segments[idx + 1][1] if idx + 1 < len(segments) else now
            if end is None:
                continue
            if direction is htf or direction not in directional:
                continue  # aligned leg, not a hunt
            hunted_short = htf is MarketDirection.BULLISH
            hunted_side = (
                RetailPositioning.SHORT if hunted_short else RetailPositioning.LONG
            )
            capture_direction = (
                MarketDirection.BULLISH if hunted_short else MarketDirection.BEARISH
            )
            grabs = self._capture_grabs(
                data, hunted_short, capture_direction, start, end, merge_gap
            )
            side_word = "shorts" if hunted_short else "longs"
            sub_start = start
            for grab_ts, score, sources in grabs:
                episodes.append(
                    LiquidityHuntEpisode(
                        hunted_side=hunted_side,
                        correction_direction=direction,
                        start_timestamp=sub_start,
                        end_timestamp=grab_ts,
                        capture_score=score,
                        capture_sources=sources,
                        description=(
                            f"Completed hunt: a {direction.value} move against "
                            f"the {htf.value} higher-timeframe trend swept "
                            f"{side_word} liquidity "
                            f"({', '.join(sources)}; score {score:.0f}), "
                            f"freeing the near-term move."
                        ),
                    )
                )
                sub_start = grab_ts
        return episodes

    @staticmethod
    def _trend_segments(
        events: list[MarketStructure],
    ) -> list[tuple[MarketDirection, datetime]]:
        """Segment the event replay into (trend direction, flip timestamp) legs.

        Same replay rules as :meth:`_current_trend` (BOS/CHoCH set the trend,
        ``CHOCH_FAILED`` reverts it, provisional/descriptive events ignored),
        but returns one entry per trend leg instead of only the final state.
        """
        segments: list[tuple[MarketDirection, datetime]] = []
        trend: MarketDirection | None = None
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
                segments.append((new_trend, event.timestamp))
            trend = new_trend
        return segments

    def _capture_grabs(
        self,
        data: DashboardData,
        hunted_short: bool,
        capture_direction: MarketDirection,
        start: datetime,
        end: datetime,
        merge_gap: timedelta | None,
    ) -> list[tuple[datetime, float, list[str]]]:
        """Weighted capture grabs inside ``[start, end]`` as (ts, score, sources).

        Each capture-side signal — a ``LIQUIDITY_SWEEP`` (weight
        ``_WEIGHT_SWEEP``), a VSA climax/thrust on the grab side
        (``_WEIGHT_VSA``), an OI ``FLUSH`` (``_WEIGHT_OI_FLUSH``), or a
        hunted-side equal-level zone swept (``_WEIGHT_ZONE``) — is collected
        with its timestamp. Signals within ``merge_gap`` are one cluster (a
        single grab moment); a source type counts once per cluster, and a
        volume-delta confirmation in the capture direction adds
        ``_WEIGHT_DELTA_MODIFIER``. A cluster whose score reaches
        ``_CAPTURE_THRESHOLD`` is a grab, anchored at its first signal (the
        hunt ends at the first touch). Below threshold, no grab.
        """
        signals = self._collect_capture_signals(
            data, hunted_short, capture_direction, start, end
        )
        signals.sort(key=lambda s: s[0])

        clusters: list[list[tuple[datetime, float, str]]] = []
        for signal in signals:
            if (
                clusters
                and merge_gap is not None
                and signal[0] - clusters[-1][-1][0] <= merge_gap
            ):
                clusters[-1].append(signal)
            else:
                clusters.append([signal])

        grabs: list[tuple[datetime, float, list[str]]] = []
        for cluster in clusters:
            # A source type counts once per cluster (two sweeps close together
            # are still one grab, not double weight).
            by_source: dict[str, float] = {}
            for _ts, weight, source in cluster:
                by_source[source] = max(by_source.get(source, 0.0), weight)
            first_ts, last_ts = cluster[0][0], cluster[-1][0]
            if self._delta_confirms(data, capture_direction, first_ts, last_ts):
                by_source["delta"] = _WEIGHT_DELTA_MODIFIER
            score = sum(by_source.values())
            if score >= _CAPTURE_THRESHOLD:
                grabs.append((first_ts, score, sorted(by_source)))
        return grabs

    def _collect_capture_signals(
        self,
        data: DashboardData,
        hunted_short: bool,
        capture_direction: MarketDirection,
        start: datetime,
        end: datetime,
    ) -> list[tuple[datetime, float, str]]:
        """All weighted capture-side signals inside ``[start, end]``."""
        signals: list[tuple[datetime, float, str]] = []
        for event in data.internal_structure_events:
            if (
                event.event is StructureEvent.LIQUIDITY_SWEEP
                and event.direction is capture_direction
                and start <= event.timestamp <= end
            ):
                signals.append((event.timestamp, _WEIGHT_SWEEP, "sweep"))

        zone_type = (
            LiquidityZoneType.EQUAL_HIGHS
            if hunted_short
            else LiquidityZoneType.EQUAL_LOWS
        )
        for zone in data.liquidity_zones:
            if (
                zone.zone_type is zone_type
                and zone.is_mitigated
                and zone.invalidated_at is not None
                and start <= zone.invalidated_at <= end
            ):
                signals.append((zone.invalidated_at, _WEIGHT_ZONE, "zone"))

        # VSA maps by the *grab side*, not by VSA's implied direction (which is
        # the mirror): a hunted-short capture is an up-sweep rejecting the high
        # (UP_THRUST / BUYING_CLIMAX), a hunted-long capture rejects the low.
        vsa_patterns = _VSA_SHORT_CAPTURE if hunted_short else _VSA_LONG_CAPTURE
        for vsa in data.volume_spread_signals:
            if vsa.pattern in vsa_patterns and start <= vsa.timestamp <= end:
                signals.append((vsa.timestamp, _WEIGHT_VSA, "vsa"))

        if data.oi_analysis is not None:
            for qualified in data.oi_analysis.qualified_events:
                if (
                    qualified.participation is OIParticipation.FLUSH
                    and qualified.direction is capture_direction
                    and start <= qualified.event_timestamp <= end
                ):
                    signals.append(
                        (qualified.event_timestamp, _WEIGHT_OI_FLUSH, "oi_flush")
                    )
        return signals

    @staticmethod
    def _delta_confirms(
        data: DashboardData,
        capture_direction: MarketDirection,
        first_ts: datetime,
        last_ts: datetime,
    ) -> bool:
        """Whether a candle in the cluster shows net taker aggression in the
        capture direction (volume delta beyond ``_DELTA_MODIFIER_MIN_RATIO`` of
        its volume) — confirming *who* won the grab candle."""
        want_positive = capture_direction is MarketDirection.BULLISH
        for candle in data.candles:
            if candle.timestamp < first_ts or candle.timestamp > last_ts:
                continue
            if candle.volume <= 0:
                continue
            delta = 2 * candle.taker_buy_volume - candle.volume
            if abs(delta) / candle.volume < _DELTA_MODIFIER_MIN_RATIO:
                continue
            if (delta > 0) is want_positive:
                return True
        return False

    @staticmethod
    def _grab_merge_gap(candles: list[Candle]) -> timedelta | None:
        """A few candles' worth of time — grabs within it are one sweep cluster."""
        if len(candles) < 2:
            return None
        spacing = candles[-1].timestamp - candles[-2].timestamp
        if spacing <= timedelta(0):
            return None
        return spacing * _GRAB_MERGE_CANDLES

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
        self,
        data: DashboardData,
        hunted_short: bool,
        flip_timestamp: datetime,
        proximity: float,
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
            if mid <= 0 or abs(mid - price) / price > proximity:
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
        self,
        data: DashboardData,
        hunted_short: bool,
        flip_timestamp: datetime,
        proximity: float,
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
            if abs(mid - price) / price > proximity:
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
