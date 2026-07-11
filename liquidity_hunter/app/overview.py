"""Multi-timeframe structural overview: the M5 → W1 "state ladder".

Builds a :class:`~liquidity_hunter.core.domain.MarketOverview` — one compact
:class:`~liquidity_hunter.core.domain.TimeframeOverview` per timeframe, each
carrying the internal detector's standing trend (exactly the trend the chart
renders when that timeframe is opened), the last trend-relevant structural
event, any provisional live-edge mark, and a per-timeframe liquidity-hunt
summary.

The load is split into two stages so the API layer can cache each timeframe
independently (a weekly reading does not need refreshing every few seconds):

- :func:`load_timeframe_structure` — the I/O + detection unit for one
  timeframe (buffered fetch, structural anchor, production detector wiring,
  composition passes, equal-level zones). This is the cacheable unit.
- :func:`build_overview` — pure assembly of the cached snapshots into a
  :class:`MarketOverview` (cross-timeframe: each entry's hunt is computed
  against the trend of its `_HIGHER_TIMEFRAME_MAP` anchor from the same
  snapshot set).

The overview deliberately skips the futures state (open interest, funding,
liquidation map): its per-timeframe hunt maps equal-level pools only, the
documented graceful degradation of `LiquidityHuntEngine` — the full
OI-qualified hunt for the selected timeframe stays on `/api/dashboard`.
Purely descriptive throughout: each entry states which way a timeframe's
structure points and who its resting liquidity is, never what to do.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from liquidity_hunter.app.dashboard_data import (
    _HIGHER_TIMEFRAME_MAP,
    _HUNT_PROXIMITY_ATR,
    DashboardData,
    _run_internal_structure,
    default_ohlcv_provider,
)
from liquidity_hunter.app.liquidity_hunt import LiquidityHuntEngine
from liquidity_hunter.core.domain import (
    Candle,
    LiquidityZone,
    MarketDirection,
    MarketOverview,
    MarketStructure,
    RetailPositioning,
    StructureEvent,
    TimeFrame,
    TimeframeOverview,
)
from liquidity_hunter.data import OHLCVProvider
from liquidity_hunter.liquidity import (
    EqualHighDetector,
    EqualLowDetector,
    mark_swept_zones,
)
from liquidity_hunter.psychology import RetailBiasEstimate

# The default ladder, fine -> coarse. Every timeframe's `_HIGHER_TIMEFRAME_MAP`
# anchor is also in the set, so each entry's hunt reads its HTF trend from the
# same snapshot batch (W1, the top, falls back to its own trend = "aligned").
OVERVIEW_TIMEFRAMES: tuple[TimeFrame, ...] = (
    TimeFrame.M5,
    TimeFrame.M15,
    TimeFrame.M30,
    TimeFrame.H1,
    TimeFrame.H4,
    TimeFrame.D1,
    TimeFrame.W1,
)

# Trend-relevant marks for the "last event" reading (the same set the hunt's
# trend replay considers); descriptive HH/HL/LH/LL pivots and sweeps are not
# standing-state changes.
_TREND_EVENTS = frozenset(
    {
        StructureEvent.BREAK_OF_STRUCTURE,
        StructureEvent.CHANGE_OF_CHARACTER,
        StructureEvent.CHOCH_FAILED,
    }
)

# Provisional live-edge marks worth surfacing as "forming" (`BOS?`/`CHoCH?`).
# A provisional CHOCH_FAILED is the additive fast-fizzle marker, not a forming
# event, so it is excluded.
_FORMING_EVENTS = frozenset(
    {StructureEvent.BREAK_OF_STRUCTURE, StructureEvent.CHANGE_OF_CHARACTER}
)


@dataclass(frozen=True)
class TimeframeStructureSnapshot:
    """One timeframe's cacheable structure reading (I/O + detection output)."""

    timeframe: TimeFrame
    # The visible window (trailing `limit` candles).
    candles: list[Candle]
    # Visible-window internal-structure events, composition-passed -- the same
    # marks the chart renders for this timeframe.
    events: list[MarketStructure]
    # The internal detector's state-machine trend at the series end.
    trend: MarketDirection
    # Equal-level zones with sweep marking (the hunt's stop-cluster pools).
    liquidity_zones: list[LiquidityZone]


def load_timeframe_structure(
    provider: OHLCVProvider | None = None,
    symbol: str = "BTCUSDT",
    timeframe: TimeFrame = TimeFrame.H1,
    limit: int = 1200,
    confluence_filter: bool = False,
) -> TimeframeStructureSnapshot:
    """Fetch and detect one timeframe's structure snapshot (the cacheable unit).

    Runs the exact production internal-structure pipeline `load_dashboard_data`
    uses (`_run_internal_structure`: buffered fetch, structural anchor,
    per-timeframe detector wiring, composition passes), plus the equal-level
    zone detection the per-timeframe hunt needs.
    """
    if provider is None:
        provider = default_ohlcv_provider()
    run = _run_internal_structure(provider, symbol, timeframe, limit, confluence_filter)
    liquidity_zones = mark_swept_zones(
        [
            *EqualHighDetector().detect(run.candles),
            *EqualLowDetector().detect(run.candles),
        ],
        run.candles,
    )
    return TimeframeStructureSnapshot(
        timeframe=timeframe,
        candles=run.candles,
        events=run.events,
        trend=run.trend,
        liquidity_zones=liquidity_zones,
    )


def build_overview(
    symbol: str, snapshots: Sequence[TimeframeStructureSnapshot]
) -> MarketOverview:
    """Assemble snapshots into a :class:`MarketOverview` (pure, no I/O).

    Entry order follows ``snapshots``. Each entry's hunt is computed against
    the trend of its `_HIGHER_TIMEFRAME_MAP` anchor when that timeframe is in
    the batch; otherwise (the top timeframe, or a partial batch) it degrades
    to the entry's own trend — reading "aligned", the same fallback
    `load_dashboard_data` uses.
    """
    by_timeframe = {snapshot.timeframe: snapshot for snapshot in snapshots}
    entries = [_build_entry(symbol, snapshot, by_timeframe) for snapshot in snapshots]
    return MarketOverview(symbol=symbol, entries=entries)


def load_overview(
    provider: OHLCVProvider | None = None,
    symbol: str = "BTCUSDT",
    timeframes: Sequence[TimeFrame] | None = None,
    limit: int = 1200,
    confluence_filter: bool = False,
) -> MarketOverview:
    """Load the full multi-timeframe overview (default ladder M5 → W1)."""
    if provider is None:
        provider = default_ohlcv_provider()
    selected = tuple(timeframes) if timeframes is not None else OVERVIEW_TIMEFRAMES
    snapshots = [
        load_timeframe_structure(
            provider=provider,
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            confluence_filter=confluence_filter,
        )
        for timeframe in selected
    ]
    return build_overview(symbol, snapshots)


def _build_entry(
    symbol: str,
    snapshot: TimeframeStructureSnapshot,
    by_timeframe: dict[TimeFrame, TimeframeStructureSnapshot],
) -> TimeframeOverview:
    htf = _HIGHER_TIMEFRAME_MAP.get(snapshot.timeframe)
    htf_snapshot = by_timeframe.get(htf) if htf is not None else None
    if htf_snapshot is not None:
        higher_timeframe: TimeFrame | None = htf
        higher_timeframe_direction = htf_snapshot.trend
    else:
        higher_timeframe = None
        higher_timeframe_direction = snapshot.trend

    hunt = LiquidityHuntEngine(proximity_atr=_HUNT_PROXIMITY_ATR).build(
        _slim_dashboard_data(symbol, snapshot, higher_timeframe_direction, higher_timeframe)
    )

    last_event = _latest(snapshot.events, _TREND_EVENTS, provisional=False)
    forming_event = _latest(snapshot.events, _FORMING_EVENTS, provisional=True)
    last_candle = snapshot.candles[-1]

    return TimeframeOverview(
        timeframe=snapshot.timeframe,
        trend=snapshot.trend,
        current_price=last_candle.close,
        candle_timestamp=last_candle.timestamp,
        higher_timeframe=higher_timeframe,
        higher_timeframe_direction=(
            higher_timeframe_direction if higher_timeframe is not None else None
        ),
        last_event=last_event.event if last_event else None,
        last_event_direction=last_event.direction if last_event else None,
        last_event_timestamp=last_event.timestamp if last_event else None,
        last_event_candles_ago=(
            sum(1 for c in snapshot.candles if c.timestamp > last_event.timestamp)
            if last_event
            else None
        ),
        forming_event=forming_event.event if forming_event else None,
        forming_direction=forming_event.direction if forming_event else None,
        hunt_phase=hunt.phase,
        hunted_side=hunt.hunted_side,
        hunt_targets_captured=hunt.targets_captured,
        hunt_targets_total=hunt.targets_total,
    )


def _latest(
    events: Sequence[MarketStructure],
    kinds: frozenset[StructureEvent],
    *,
    provisional: bool,
) -> MarketStructure | None:
    matching = [e for e in events if e.event in kinds and e.provisional is provisional]
    return max(matching, key=lambda e: e.timestamp, default=None)


def _slim_dashboard_data(
    symbol: str,
    snapshot: TimeframeStructureSnapshot,
    higher_timeframe_direction: MarketDirection,
    higher_timeframe: TimeFrame | None,
) -> DashboardData:
    """A minimal `DashboardData` carrying just what `LiquidityHuntEngine` reads.

    The hunt engine consumes candles, internal structure events, equal-level
    zones, the HTF direction, and (optionally) the liquidation map / OI
    analysis; the latter two are deliberately `None` here (see the module
    docstring). `retail_bias` is a required field the engine never reads, so
    a neutral placeholder fills it.
    """
    last_candle = snapshot.candles[-1]
    return DashboardData(
        symbol=symbol,
        timeframe=snapshot.timeframe,
        candles=snapshot.candles,
        current_price=last_candle.close,
        higher_timeframe_direction=higher_timeframe_direction,
        higher_timeframe=higher_timeframe,
        liquidity_zones=snapshot.liquidity_zones,
        ranked_zones=[],
        market_structure_events=[],
        internal_structure_events=snapshot.events,
        retail_bias=RetailBiasEstimate(
            symbol=symbol,
            generated_at=last_candle.timestamp,
            dominant_side=RetailPositioning.NEUTRAL,
            confidence=0.0,
            explanation="Not estimated for the multi-timeframe overview.",
        ),
        poi_zones=[],
        manipulation_cycles=[],
        behavior_divergences=[],
    )
