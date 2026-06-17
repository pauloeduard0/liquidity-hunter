"""POI (Point of Interest / Order Block) detector.

Identifies institutional order block zones anchored to the leg between a
validated CHoCH and the *first* BOS in the same direction.  The zone box
is built from the extreme candle in that window:

  Bullish demand zone:
    price_low  = extreme_candle.low          (invalidation line)
    price_high = (low + high) / 2            (50 % midpoint)

  Bearish supply zone (mirror):
    price_high = extreme_candle.high         (invalidation line)
    price_low  = (low + high) / 2

Zone lifecycle
--------------
ACTIVE → MITIGATED
  Price sweeps the invalidation boundary (wick or brief closes) and
  then a candle closes back inside / beyond the zone.  One RTO
  (Return-to-Origin) event is emitted and the zone is retired.

ACTIVE → INVALIDATED
  `invalidation_persistence_candles` consecutive candle closes breach
  the boundary without recovery.  The zone is retired with no signal.

A pending CHoCH context is cancelled by an opposing BOS (trend resumed
in the original direction before price formed a new leg), which discards
any in-progress zone anchor for that side.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    POIZoneStatus,
    StructureEvent,
    StructureScope,
    TimeFrame,
)
from liquidity_hunter.core.domain.poi_zone import POIZone, RTOSweepEvent

# ---------------------------------------------------------------------------
# Internal mutable zone tracker
# ---------------------------------------------------------------------------


@dataclass
class _ZoneState:
    symbol: str
    timeframe: TimeFrame
    direction: MarketDirection
    price_low: float
    price_high: float
    created_at: datetime
    origin_choch_timestamp: datetime
    origin_bos_timestamp: datetime
    extreme_candle_timestamp: datetime
    # Tracking counters
    consecutive_closes_beyond: int = 0
    sweep_started: bool = False
    sweep_extreme: float | None = None  # worst price reached during sweep
    # Final state
    status: POIZoneStatus = field(default=POIZoneStatus.ACTIVE)
    invalidated_at: datetime | None = None
    mitigated_at: datetime | None = None
    is_done: bool = False

    def to_poi_zone(self) -> POIZone:
        return POIZone(
            symbol=self.symbol,
            timeframe=self.timeframe,
            direction=self.direction,
            price_low=self.price_low,
            price_high=self.price_high,
            created_at=self.created_at,
            origin_choch_timestamp=self.origin_choch_timestamp,
            origin_bos_timestamp=self.origin_bos_timestamp,
            extreme_candle_timestamp=self.extreme_candle_timestamp,
            status=self.status,
            invalidated_at=self.invalidated_at,
            mitigated_at=self.mitigated_at,
        )


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class POIResult:
    """Output of `POIDetector.detect()`."""

    zones: list[POIZone]
    sweep_events: list[RTOSweepEvent]


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class POIDetector:
    """Detects institutional order block zones from structure events + candles.

    Parameters
    ----------
    invalidation_persistence_candles:
        Number of consecutive candle closes that must breach the zone
        boundary to declare it invalidated.  Default 4.
    """

    def __init__(self, invalidation_persistence_candles: int = 4) -> None:
        if invalidation_persistence_candles < 1:
            raise ValueError("invalidation_persistence_candles must be >= 1")
        self._invalidation_candles = invalidation_persistence_candles

    def detect(
        self,
        candles: list[Candle],
        structure_events: list[MarketStructure],
    ) -> POIResult:
        """Detect POI zones and RTO sweep events.

        `candles` must be the *same* series passed to
        `InternalStructureDetector` (including any bootstrap buffer).
        `structure_events` must be the *unfiltered* output of that
        detector so that CHoCH anchors from the buffer can produce zones
        visible in the display window.
        """
        if not candles:
            return POIResult(zones=[], sweep_events=[])

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe

        # Only INTERNAL-scope CHoCH and BOS drive POI creation.
        internal_events = sorted(
            (e for e in structure_events if e.scope == StructureScope.INTERNAL),
            key=lambda e: e.timestamp,
        )

        timestamp_to_index: dict[datetime, int] = {
            c.timestamp: i for i, c in enumerate(candles)
        }

        events_by_timestamp: dict[datetime, list[MarketStructure]] = defaultdict(list)
        for event in internal_events:
            if event.timestamp in timestamp_to_index:
                events_by_timestamp[event.timestamp].append(event)

        pending_bullish_choch: MarketStructure | None = None
        pending_bearish_choch: MarketStructure | None = None

        active_zones: list[_ZoneState] = []
        all_zones: list[_ZoneState] = []
        sweep_events: list[RTOSweepEvent] = []

        for candle in candles:
            # --- process structure events at this candle ---
            for event in events_by_timestamp.get(candle.timestamp, []):
                if event.event == StructureEvent.CHANGE_OF_CHARACTER:
                    if event.direction == MarketDirection.BULLISH:
                        pending_bullish_choch = event
                        pending_bearish_choch = None
                    else:
                        pending_bearish_choch = event
                        pending_bullish_choch = None

                elif event.event == StructureEvent.BREAK_OF_STRUCTURE:
                    if event.direction == MarketDirection.BULLISH:
                        if pending_bullish_choch is not None:
                            zone = self._create_zone(
                                candles,
                                pending_bullish_choch,
                                event,
                                timestamp_to_index,
                                symbol,
                                timeframe,
                                bullish=True,
                            )
                            if zone is not None:
                                active_zones.append(zone)
                                all_zones.append(zone)
                            pending_bullish_choch = None
                        # A bullish BOS means the bearish leg never resumed —
                        # discard any pending bearish CHoCH anchor.
                        pending_bearish_choch = None

                    else:  # BEARISH BOS
                        if pending_bearish_choch is not None:
                            zone = self._create_zone(
                                candles,
                                pending_bearish_choch,
                                event,
                                timestamp_to_index,
                                symbol,
                                timeframe,
                                bullish=False,
                            )
                            if zone is not None:
                                active_zones.append(zone)
                                all_zones.append(zone)
                            pending_bearish_choch = None
                        pending_bullish_choch = None

            # --- update all active zones (skip the candle they were created on) ---
            still_active: list[_ZoneState] = []
            for zone in active_zones:
                if zone.created_at >= candle.timestamp:
                    still_active.append(zone)
                    continue

                rto = self._update_zone(zone, candle)
                if rto is not None:
                    sweep_events.append(rto)

                if not zone.is_done:
                    still_active.append(zone)

            active_zones = still_active

        return POIResult(
            zones=[z.to_poi_zone() for z in all_zones],
            sweep_events=sweep_events,
        )

    # ------------------------------------------------------------------
    # Zone creation
    # ------------------------------------------------------------------

    def _create_zone(
        self,
        candles: list[Candle],
        choch_event: MarketStructure,
        bos_event: MarketStructure,
        timestamp_to_index: dict[datetime, int],
        symbol: str,
        timeframe: TimeFrame,
        *,
        bullish: bool,
    ) -> _ZoneState | None:
        choch_idx = timestamp_to_index.get(choch_event.timestamp)
        bos_ts = (
            bos_event.reference_timestamp
            if bos_event.reference_timestamp is not None
            else bos_event.timestamp
        )
        bos_idx = timestamp_to_index.get(bos_ts)
        if choch_idx is None or bos_idx is None or choch_idx >= bos_idx:
            return None

        window = candles[choch_idx : bos_idx + 1]

        if bullish:
            extreme = min(window, key=lambda c: c.low)
            price_low = extreme.low
            price_high = (extreme.low + extreme.high) / 2
        else:
            extreme = max(window, key=lambda c: c.high)
            price_high = extreme.high
            price_low = (extreme.low + extreme.high) / 2

        if price_high <= price_low:
            # Degenerate candle with no range — skip.
            return None

        return _ZoneState(
            symbol=symbol,
            timeframe=timeframe,
            direction=MarketDirection.BULLISH if bullish else MarketDirection.BEARISH,
            price_low=price_low,
            price_high=price_high,
            created_at=bos_event.timestamp,
            origin_choch_timestamp=choch_event.timestamp,
            origin_bos_timestamp=bos_event.timestamp,
            extreme_candle_timestamp=extreme.timestamp,
        )

    # ------------------------------------------------------------------
    # Zone state update (one candle at a time)
    # ------------------------------------------------------------------

    def _update_zone(self, zone: _ZoneState, candle: Candle) -> RTOSweepEvent | None:
        if zone.is_done:
            return None
        if zone.direction == MarketDirection.BULLISH:
            return self._update_bullish(zone, candle)
        return self._update_bearish(zone, candle)

    def _update_bullish(self, zone: _ZoneState, candle: Candle) -> RTOSweepEvent | None:
        """Demand zone: invalidation line is `price_low` (bottom of box)."""
        # Track wick violation below the box floor.
        if candle.low < zone.price_low:
            zone.sweep_started = True
            if zone.sweep_extreme is None or candle.low < zone.sweep_extreme:
                zone.sweep_extreme = candle.low

        if candle.close < zone.price_low:
            zone.consecutive_closes_beyond += 1
            if zone.consecutive_closes_beyond >= self._invalidation_candles:
                zone.status = POIZoneStatus.INVALIDATED
                zone.invalidated_at = candle.timestamp
                zone.is_done = True
        else:
            # Close back inside or above the zone.
            if zone.sweep_started:
                rto = RTOSweepEvent(
                    symbol=zone.symbol,
                    timeframe=zone.timeframe,
                    direction=MarketDirection.BULLISH,
                    timestamp=candle.timestamp,
                    zone_price_low=zone.price_low,
                    zone_price_high=zone.price_high,
                    sweep_extreme=zone.sweep_extreme
                    if zone.sweep_extreme is not None
                    else candle.low,
                )
                zone.status = POIZoneStatus.MITIGATED
                zone.mitigated_at = candle.timestamp
                zone.is_done = True
                return rto
            zone.consecutive_closes_beyond = 0

        return None

    def _update_bearish(self, zone: _ZoneState, candle: Candle) -> RTOSweepEvent | None:
        """Supply zone: invalidation line is `price_high` (top of box)."""
        if candle.high > zone.price_high:
            zone.sweep_started = True
            if zone.sweep_extreme is None or candle.high > zone.sweep_extreme:
                zone.sweep_extreme = candle.high

        if candle.close > zone.price_high:
            zone.consecutive_closes_beyond += 1
            if zone.consecutive_closes_beyond >= self._invalidation_candles:
                zone.status = POIZoneStatus.INVALIDATED
                zone.invalidated_at = candle.timestamp
                zone.is_done = True
        else:
            if zone.sweep_started:
                rto = RTOSweepEvent(
                    symbol=zone.symbol,
                    timeframe=zone.timeframe,
                    direction=MarketDirection.BEARISH,
                    timestamp=candle.timestamp,
                    zone_price_low=zone.price_low,
                    zone_price_high=zone.price_high,
                    sweep_extreme=zone.sweep_extreme
                    if zone.sweep_extreme is not None
                    else candle.high,
                )
                zone.status = POIZoneStatus.MITIGATED
                zone.mitigated_at = candle.timestamp
                zone.is_done = True
                return rto
            zone.consecutive_closes_beyond = 0

        return None
