"""Tests for `liquidity_hunter.app.dashboard_data`."""

from datetime import UTC, datetime

import pytest

from liquidity_hunter.app import dashboard_data
from liquidity_hunter.app.dashboard_data import (
    _STRUCTURAL_ANCHOR_REGION,
    _build_anchored_vwaps,
    _build_internal_detector,
    _drop_failed_refire_cycles,
    _drop_pre_break_reference_bos,
    _drop_resumed_fizzle_markers,
    _drop_superseded_provisional_choch,
    _reanchor_bos_close_break,
    _run_internal_structure,
    _scope_resets_to_live_range,
    _structural_anchor_index,
    load_dashboard_data,
)
from liquidity_hunter.core.domain import (
    Candle,
    ConsolidationStatus,
    FundingRate,
    LongShortRatio,
    MarketDirection,
    MarketStructure,
    OIRegime,
    OpenInterestPoint,
    RetailPositioning,
    StructureEvent,
    StructureScope,
    TimeFrame,
    VWAPAnchor,
)
from liquidity_hunter.data.exceptions import DataProviderConnectionError
from liquidity_hunter.data.providers.base import FuturesDataProvider, OHLCVProvider
from liquidity_hunter.liquidity import InternalStructureDetector
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle, make_series

# Two equal highs (110 at index 10 and 21) and one swing low (90 at index
# 16), each with ten flat candles either side so they are pivots under the
# equal-level detectors' production lookback of 10.
HIGHS = [102.0] * 33
HIGHS[10] = HIGHS[21] = 110.0
LOWS = [high - 5 for high in HIGHS]
LOWS[16] = 90.0

_FUTURES_TS = datetime(2026, 6, 22, tzinfo=UTC)

# A short series whose only swing high (200) and lows (140, then 130) trigger
# one BOS event with `swing_lookback=2` (see test_market_structure.py for the
# full state-machine walkthrough).
STRUCTURE_HIGHS = [150.0] * 15
STRUCTURE_HIGHS[2] = 200.0

STRUCTURE_LOWS = [145.0] * 15
STRUCTURE_LOWS[7] = 140.0
STRUCTURE_LOWS[12] = 130.0


class _FakeProvider(OHLCVProvider):
    def __init__(self, candles: list[Candle]) -> None:
        self._candles = candles
        self.requested_timeframes: list[TimeFrame] = []
        self.requested_limits: list[int] = []

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        self.requested_timeframes.append(timeframe)
        self.requested_limits.append(limit)
        return self._candles


class _PerTimeframeFakeProvider(OHLCVProvider):
    """Returns the trailing `limit` candles of a per-timeframe series.

    Models a real provider where a larger `limit` request returns more
    history ending at the same "now" -- unlike `_FakeProvider`, which
    ignores `limit`/`timeframe` entirely.
    """

    def __init__(self, series_by_timeframe: dict[TimeFrame, list[Candle]]) -> None:
        self._series_by_timeframe = series_by_timeframe
        self.requested_timeframes: list[TimeFrame] = []
        self.requested_limits: list[int] = []

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        self.requested_timeframes.append(timeframe)
        self.requested_limits.append(limit)
        return self._series_by_timeframe[timeframe][-limit:]


def _trending_zigzag(num_candles: int) -> tuple[list[float], list[float]]:
    """A steadily rising zigzag: repeated higher highs and higher lows.

    Generates a `BREAK_OF_STRUCTURE` roughly every half-period, giving
    `InternalStructureDetector` a long run of internal structure events to
    filter down to a visible window.
    """
    drift, amplitude, period = 1.0, 15.0, 20
    highs, lows = [], []
    for i in range(num_candles):
        phase = i % period
        half = period // 2
        if phase < half:
            wave = amplitude * (phase / half)
        else:
            wave = amplitude * (1 - (phase - half) / half)
        level = 100.0 + drift * i + wave
        highs.append(level + 2.0)
        lows.append(level - 2.0)
    return highs, lows


class _FakeFuturesProvider(FuturesDataProvider):
    """Returns canned futures state with no network access.

    Defaults model a crowded-long book (positive funding, ratio > 1, rising
    open interest) so the estimator produces a non-empty liquidation map.
    """

    def __init__(
        self,
        funding_rate: float = 0.0006,
        ratio: float = 1.85,
        oi: tuple[float, float] = (1000.0, 1200.0),
        oi_points: list[OpenInterestPoint] | None = None,
    ) -> None:
        self._funding_rate = funding_rate
        self._ratio = ratio
        self._oi = oi
        self._oi_points = oi_points

    def get_open_interest_history(
        self, symbol: str, timeframe: TimeFrame, limit: int = 500
    ) -> list[OpenInterestPoint]:
        if self._oi_points is not None:
            return self._oi_points
        return [
            OpenInterestPoint(symbol=symbol, timestamp=_FUTURES_TS, open_interest=value)
            for value in self._oi
        ]

    def get_funding_rate_history(self, symbol: str, limit: int = 500) -> list[FundingRate]:
        return [FundingRate(symbol=symbol, timestamp=_FUTURES_TS, funding_rate=self._funding_rate)]

    def get_long_short_ratio(
        self, symbol: str, timeframe: TimeFrame, limit: int = 500
    ) -> list[LongShortRatio]:
        long_pct = self._ratio / (1 + self._ratio)
        return [
            LongShortRatio(
                symbol=symbol,
                timestamp=_FUTURES_TS,
                long_account_pct=long_pct,
                short_account_pct=1 - long_pct,
                ratio=self._ratio,
            )
        ]


class _RaisingFuturesProvider(FuturesDataProvider):
    """Simulates an unreachable futures venue (e.g. a spot-only symbol)."""

    def get_open_interest_history(
        self, symbol: str, timeframe: TimeFrame, limit: int = 500
    ) -> list[OpenInterestPoint]:
        raise DataProviderConnectionError("no perpetual contract")

    def get_funding_rate_history(self, symbol: str, limit: int = 500) -> list[FundingRate]:
        raise DataProviderConnectionError("no perpetual contract")

    def get_long_short_ratio(
        self, symbol: str, timeframe: TimeFrame, limit: int = 500
    ) -> list[LongShortRatio]:
        raise DataProviderConnectionError("no perpetual contract")


_FAKE_FUTURES = _FakeFuturesProvider()


def test_load_dashboard_data_assembles_research_snapshot() -> None:
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")

    data = load_dashboard_data(
        provider=_FakeProvider(candles), symbol="BTCUSDT", futures_provider=_FAKE_FUTURES
    )

    assert data.symbol == "BTCUSDT"
    assert data.candles == candles
    assert data.current_price == candles[-1].close
    assert data.market_structure_events == []
    assert isinstance(data.internal_structure_events, list)

    # 2 swing highs + 1 swing low + 1 equal-highs zone (see detector tests)
    assert len(data.liquidity_zones) == 4
    assert len(data.ranked_zones) == len(data.liquidity_zones)
    scores = [scored.score for scored in data.ranked_zones]
    assert scores == sorted(scores, reverse=True)

    assert data.retail_bias.symbol == "BTCUSDT"
    assert isinstance(data.retail_bias.dominant_side, RetailPositioning)


def test_load_dashboard_data_derives_major_structure_events() -> None:
    candles = make_series(STRUCTURE_HIGHS, STRUCTURE_LOWS, symbol="BTCUSDT")
    # Index 12 breaks active_low (140 from index 7): give it a close beyond
    # 140 so the break is confirmed as a BOS rather than a liquidity sweep.
    candles[12] = make_candle(
        12,
        STRUCTURE_HIGHS[12],
        STRUCTURE_LOWS[12],
        symbol="BTCUSDT",
        close=135.0,
    )

    data = load_dashboard_data(
        provider=_FakeProvider(candles),
        symbol="BTCUSDT",
        swing_lookback=2,
        confluence_filter=False,
        futures_provider=_FAKE_FUTURES,
    )

    assert len(data.market_structure_events) == 1
    event = data.market_structure_events[0]
    assert event.event is StructureEvent.BREAK_OF_STRUCTURE
    assert event.direction is MarketDirection.BEARISH


def test_load_dashboard_data_higher_tf_direction_uses_internal_detector_on_htf() -> None:
    # Current-TF (H1) series: flat, no structure of its own. HTF (H4) series:
    # a bearish close-break crafted for the *internal* H4 wiring (swing
    # lookback 5) -- pivot spacing too tight for the major detector at the
    # default lookback 10, which finds no pivots here and would read NEUTRAL.
    # A BEARISH read therefore proves the HTF trend comes from the internal
    # run (the same wiring the H4 view renders), not the old major-detector
    # source.
    flat = make_series([150.0] * 40, [145.0] * 40, symbol="BTCUSDT", timeframe=TimeFrame.H1)

    htf_highs = [150.0] * 35
    htf_lows = [145.0] * 35
    htf_lows[10] = 140.0  # low pivot (internal H4 lookback 5)
    htf_lows[20] = 130.0  # new low pivot whose candle closes below 140
    htf_candles = make_series(htf_highs, htf_lows, symbol="BTCUSDT", timeframe=TimeFrame.H4)
    htf_candles[20] = make_candle(
        20,
        htf_highs[20],
        htf_lows[20],
        symbol="BTCUSDT",
        close=132.0,
        timeframe=TimeFrame.H4,
    )

    data = load_dashboard_data(
        provider=_PerTimeframeFakeProvider(
            {TimeFrame.H1: flat, TimeFrame.H4: htf_candles}
        ),
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        futures_provider=_FAKE_FUTURES,
    )

    assert data.higher_timeframe_direction is MarketDirection.BEARISH
    # The anchor pair is exposed so the frontend can label the reading.
    assert data.higher_timeframe is TimeFrame.H4


def test_load_dashboard_data_top_timeframe_has_no_higher_timeframe() -> None:
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")

    data = load_dashboard_data(
        provider=_FakeProvider(candles),
        symbol="BTCUSDT",
        timeframe=TimeFrame.W1,
        futures_provider=_FAKE_FUTURES,
    )

    # W1 has no higher timeframe: no anchor pair, and the direction degrades
    # to the current series' own internal trend (flat here -> NEUTRAL).
    assert data.higher_timeframe is None
    assert data.higher_timeframe_direction is MarketDirection.NEUTRAL


def test_load_dashboard_data_internal_structure_events_use_internal_scope() -> None:
    # Extended series: bearish BOS at L130 confirmed by LH pullback at H180.
    # Pivot spacing sized for the production M5 internal swing lookback (6):
    # each pivot needs 6 flat candles on both sides to form.
    int_highs = [150.0] * 34
    int_highs[6] = 200.0
    int_highs[27] = 180.0
    int_lows = [145.0] * 34
    int_lows[13] = 140.0
    int_lows[20] = 130.0
    candles = make_series(int_highs, int_lows, symbol="BTCUSDT")
    candles[20] = make_candle(
        20,
        int_highs[20],
        int_lows[20],
        symbol="BTCUSDT",
        close=135.0,
        taker_buy_volume=0.3,
    )
    # The confirming LH pullback closes near its high (a real bounce, not a
    # midpoint doji) so it passes the BOS pullback wick filter.
    candles[27] = make_candle(27, int_highs[27], int_lows[27], symbol="BTCUSDT", close=178.0)

    data = load_dashboard_data(
        provider=_FakeProvider(candles),
        symbol="BTCUSDT",
        timeframe=TimeFrame.M5,
        confluence_filter=False,
        futures_provider=_FAKE_FUTURES,
    )

    assert data.internal_structure_events
    assert all(
        event.scope is StructureScope.INTERNAL for event in data.internal_structure_events
    )


def test_load_dashboard_data_neutral_trend_with_no_structure_events() -> None:
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")

    data = load_dashboard_data(
        provider=_FakeProvider(candles), symbol="BTCUSDT", futures_provider=_FAKE_FUTURES
    )

    assert data.market_structure_events == []
    assert data.higher_timeframe_direction is MarketDirection.NEUTRAL


def test_load_dashboard_data_fetches_buffered_series_for_internal_structure() -> None:
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")
    provider = _FakeProvider(candles)

    load_dashboard_data(
        provider=provider,
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        limit=500,
        futures_provider=_FAKE_FUTURES,
    )

    # The buffered series (limit + 300 bootstrap buffer) is fetched once; the
    # visible window is its tail. Then the HTF series is fetched with the same
    # buffered limit (the internal detector needs the same warm-up there).
    assert provider.requested_timeframes == [TimeFrame.H1, TimeFrame.H4]
    assert provider.requested_limits == [800, 800]


def test_load_dashboard_data_internal_structure_fetch_limit_capped_at_klines_max() -> None:
    # limit=900 + 300 buffer = 1200, capped to Binance's klines max of 1000.
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")
    provider = _FakeProvider(candles)

    load_dashboard_data(
        provider=provider,
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        limit=900,
        futures_provider=_FAKE_FUTURES,
    )

    assert provider.requested_limits == [1000, 1000]


def test_load_dashboard_data_internal_structure_filters_to_visible_window() -> None:
    # A long zigzag produces internal structure events spanning the whole
    # series, but only the visible window (the trailing `limit` candles)
    # should be reported.
    highs, lows = _trending_zigzag(340)
    full_candles = make_series(highs, lows, symbol="BTCUSDT", timeframe=TimeFrame.H1)

    htf_candles = make_series(highs[:100], lows[:100], symbol="BTCUSDT", timeframe=TimeFrame.H4)
    provider = _PerTimeframeFakeProvider({
        TimeFrame.H1: full_candles,
        TimeFrame.H4: htf_candles,
    })

    data = load_dashboard_data(
        provider=provider,
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        limit=40,
        futures_provider=_FAKE_FUTURES,
    )

    # limit=40 + 300 buffer = 340 (fetched once), plus the HTF fetch at the
    # same buffered limit.
    assert provider.requested_limits == [340, 340]

    visible_start = data.candles[0].timestamp
    visible_end = data.candles[-1].timestamp
    assert data.internal_structure_events
    assert all(
        visible_start <= event.timestamp <= visible_end
        for event in data.internal_structure_events
    )

    # The buffered series produces many more events than fall in the visible
    # window -- proving the buffer/filter actually discards out-of-window
    # events rather than the window happening to cover everything.
    unfiltered_events = InternalStructureDetector(swing_lookback=10).detect(full_candles)
    assert len(unfiltered_events) > len(data.internal_structure_events)


def test_load_dashboard_data_populates_liquidation_map() -> None:
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")

    data = load_dashboard_data(
        provider=_FakeProvider(candles), symbol="BTCUSDT", futures_provider=_FAKE_FUTURES
    )

    assert data.liquidation_map is not None
    # Crowded-long futures state -> long is the over-leveraged side.
    assert data.liquidation_map.dominant_leveraged_side is RetailPositioning.LONG
    assert data.liquidation_map.bands


def test_load_dashboard_data_degrades_when_futures_unavailable() -> None:
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")

    data = load_dashboard_data(
        provider=_FakeProvider(candles),
        symbol="BTCUSDT",
        futures_provider=_RaisingFuturesProvider(),
    )

    # Spot-only symbol: liquidation map and OI analysis are absent but the
    # rest is intact.
    assert data.liquidation_map is None
    assert data.oi_analysis is None
    assert data.candles == candles
    assert data.narrative is not None


def test_load_dashboard_data_skips_narrative_when_disabled() -> None:
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")

    data = load_dashboard_data(
        provider=_FakeProvider(candles),
        symbol="BTCUSDT",
        futures_provider=_FAKE_FUTURES,
        compute_narrative=False,
    )

    # Only the narrative synthesis is skipped; the hunt still runs.
    assert data.narrative is None
    assert data.liquidity_hunt is not None


def test_load_dashboard_data_populates_oi_analysis() -> None:
    # This test needs a falling tail, not pivots: its own series, so the
    # equal-level fixture stays free to change shape.
    oi_highs = [102.0 - i * 0.1 for i in range(33)]
    candles = make_series(oi_highs, [high - 5 for high in oi_highs], symbol="BTCUSDT")
    # An OI sample per candle, steadily rising, while the series tail drifts
    # down (the last window's closes fall ~1%): price down + OI up.
    oi_points = [
        OpenInterestPoint(
            symbol="BTCUSDT", timestamp=candle.timestamp, open_interest=1000.0 + i * 10
        )
        for i, candle in enumerate(candles)
    ]

    data = load_dashboard_data(
        provider=_FakeProvider(candles),
        symbol="BTCUSDT",
        futures_provider=_FakeFuturesProvider(oi_points=oi_points),
    )

    assert data.oi_analysis is not None
    assert data.oi_analysis.coverage_start == candles[0].timestamp
    regime = data.oi_analysis.current_regime
    assert regime is not None
    assert regime.regime is OIRegime.SHORT_BUILDUP


def test_structural_anchor_index_picks_most_recent_low() -> None:
    highs = [100.0] * 20
    lows = [90.0] * 20
    highs[5] = 110.0  # high spike (older)
    lows[10] = 80.0  # deep low (more recent) -> the anchor
    candles = make_series(highs, lows)

    assert _structural_anchor_index(candles, candles[15].timestamp) == 10


def test_structural_anchor_index_picks_most_recent_high() -> None:
    highs = [100.0] * 20
    lows = [90.0] * 20
    lows[4] = 80.0  # deep low (older)
    highs[12] = 110.0  # high spike (more recent) -> the anchor
    candles = make_series(highs, lows)

    assert _structural_anchor_index(candles, candles[15].timestamp) == 12


def test_structural_anchor_index_no_buffer_returns_zero() -> None:
    candles = make_series([100.0] * 10, [90.0] * 10)
    # The visible window is the entire series: no pre-visible candles to anchor in.
    assert _structural_anchor_index(candles, candles[0].timestamp) == 0


def test_structural_anchor_index_ignores_extreme_outside_region() -> None:
    n = _STRUCTURAL_ANCHOR_REGION + 20
    highs = [100.0] * n
    lows = [90.0] * n
    lows[5] = 70.0  # deepest low, but OUTSIDE the anchor region
    lows[n - 60] = 80.0  # milder low, inside the region -> the anchor
    candles = make_series(highs, lows)

    assert _structural_anchor_index(candles, candles[n - 5].timestamp) == n - 60


def test_scope_resets_keeps_only_the_active_range() -> None:
    from liquidity_hunter.core.domain import ConsolidationRange
    from liquidity_hunter.liquidity.detectors._common import RangeReset

    candles = make_series([100.0] * 40, [90.0] * 40)
    resolved = ConsolidationRange(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        start_timestamp=candles[0].timestamp,
        end_timestamp=candles[10].timestamp,
        price_low=90.0,
        price_high=100.0,
        status=ConsolidationStatus.RESOLVED,
        resolved_direction=MarketDirection.BULLISH,
        candle_count=10,
    )
    active = ConsolidationRange(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        start_timestamp=candles[20].timestamp,
        price_low=95.0,
        price_high=105.0,
        status=ConsolidationStatus.ACTIVE,
        candle_count=20,
    )
    # One directive inside the resolved range, one inside the active range.
    resolved_reset = RangeReset(
        candle_index=8, price_low=90.0, price_high=100.0,
        low_formed_timestamp=candles[1].timestamp,
        high_formed_timestamp=candles[0].timestamp,
    )
    active_reset = RangeReset(
        candle_index=32, price_low=95.0, price_high=105.0,
        low_formed_timestamp=candles[21].timestamp,
        high_formed_timestamp=candles[20].timestamp,
    )

    scoped = _scope_resets_to_live_range(
        [resolved_reset, active_reset], [resolved, active], candles
    )

    # Only the active range's directive survives: re-seeding the resolved
    # range would rewrite the settled structure after it.
    assert scoped == [active_reset]


def test_scope_resets_empty_when_no_active_range() -> None:
    from liquidity_hunter.core.domain import ConsolidationRange
    from liquidity_hunter.liquidity.detectors._common import RangeReset

    candles = make_series([100.0] * 20, [90.0] * 20)
    resolved = ConsolidationRange(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        start_timestamp=candles[0].timestamp,
        end_timestamp=candles[10].timestamp,
        price_low=90.0,
        price_high=100.0,
        status=ConsolidationStatus.RESOLVED,
        resolved_direction=MarketDirection.BULLISH,
        candle_count=10,
    )
    reset = RangeReset(
        candle_index=8, price_low=90.0, price_high=100.0,
        low_formed_timestamp=candles[1].timestamp,
        high_formed_timestamp=candles[0].timestamp,
    )

    assert _scope_resets_to_live_range([reset], [resolved], candles) == []


def _structure_event(
    minute: int,
    event: StructureEvent,
    direction: MarketDirection,
    *,
    reference_minute: int | None = None,
    reference_level: float = 90.0,
    provisional: bool = False,
) -> MarketStructure:
    return MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.M15,
        timestamp=datetime(2026, 7, 2, 0, minute, tzinfo=UTC),
        event=event,
        direction=direction,
        price_level=100.0,
        reference_price_level=reference_level,
        reference_timestamp=(
            datetime(2026, 7, 2, 0, reference_minute, tzinfo=UTC)
            if reference_minute is not None
            else None
        ),
        scope=StructureScope.INTERNAL,
        provisional=provisional,
    )


def test_drop_pre_break_reference_bos_drops_wick_attempt_reference() -> None:
    # BOS at :10; the next continuation's reference formed at :05 -- while the
    # first BOS was still unbroken (a wick attempt at its level) -- so it is
    # pre-break liquidity, not structure of the new leg.
    events = [
        _structure_event(10, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH),
        _structure_event(
            20, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, reference_minute=5
        ),
    ]

    kept = _drop_pre_break_reference_bos(events)

    assert [e.timestamp.minute for e in kept] == [10]


def test_drop_pre_break_reference_bos_keeps_post_break_reference() -> None:
    # Reference formed at :15, after the prior BOS's confirming close at :10:
    # a genuine formed level of the new leg. Equality (:10) is also kept -- the
    # break candle's own extreme forms as part of the break.
    events = [
        _structure_event(10, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH),
        _structure_event(
            20, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, reference_minute=15
        ),
        _structure_event(
            30, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, reference_minute=20
        ),
    ]

    assert _drop_pre_break_reference_bos(events) == events


def test_drop_pre_break_reference_bos_choch_starts_new_leg() -> None:
    # The first BOS of a leg references the CHoCH-seeded level, which formed
    # before the flip -- the CHoCH resets the constraint for its direction.
    events = [
        _structure_event(10, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH),
        _structure_event(20, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH),
        _structure_event(30, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH),
        _structure_event(
            40, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, reference_minute=5
        ),
    ]

    assert _drop_pre_break_reference_bos(events) == events


def test_drop_pre_break_reference_bos_choch_failed_starts_new_leg() -> None:
    # A failed CHoCH flips the trend to the *opposite* of its direction, starting
    # a new leg there; that leg's first BOS references the CHoCH-seeded level
    # (formed before the flip), so the CHOCH_FAILED must reset the constraint for
    # the flipped (bearish) direction -- the AAVE H4 first bearish BOS at 122.72.
    events = [
        _structure_event(10, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH),
        _structure_event(20, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH),
        _structure_event(30, StructureEvent.CHOCH_FAILED, MarketDirection.BULLISH),
        _structure_event(
            40, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH, reference_minute=5
        ),
    ]

    assert _drop_pre_break_reference_bos(events) == events


def test_drop_failed_refire_cycles_groups_same_level_structural_reattempt() -> None:
    # ENAUSDT 4H 0.07463: a bearish CHoCH failed, the resumed bullish leg
    # printed a BOS, then a second bearish CHoCH re-attempted the *same level*
    # through a structural reference (its reference_timestamp is the level's
    # formation, not the failure) and died within a day. Same one-line story as
    # a failed re-fire -- the level-match must group it with the original ✕.
    events = [
        _structure_event(
            10, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH, reference_minute=5
        ),
        _structure_event(20, StructureEvent.CHOCH_FAILED, MarketDirection.BEARISH),
        _structure_event(30, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH),
        _structure_event(
            40, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH, reference_minute=5
        ),
        _structure_event(50, StructureEvent.CHOCH_FAILED, MarketDirection.BEARISH),
    ]

    kept = _drop_failed_refire_cycles(events)

    assert [e.timestamp.minute for e in kept] == [10, 20, 30]


def test_drop_failed_refire_cycles_keeps_surviving_same_level_reattempt() -> None:
    # A same-level re-attempt whose trend still stands is kept -- hiding it
    # would desync the chart from final_trend.
    events = [
        _structure_event(
            10, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH, reference_minute=5
        ),
        _structure_event(20, StructureEvent.CHOCH_FAILED, MarketDirection.BEARISH),
        _structure_event(
            40, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH, reference_minute=5
        ),
    ]

    assert _drop_failed_refire_cycles(events) == events


def test_drop_failed_refire_cycles_keeps_different_level_failure() -> None:
    # A later failed CHoCH at a *different* level is an independent attempt,
    # not a re-fire of the first failure -- both cycles stay visible.
    events = [
        _structure_event(
            10, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH, reference_minute=5
        ),
        _structure_event(20, StructureEvent.CHOCH_FAILED, MarketDirection.BEARISH),
        _structure_event(
            40,
            StructureEvent.CHANGE_OF_CHARACTER,
            MarketDirection.BEARISH,
            reference_minute=35,
            reference_level=85.0,
        ),
        _structure_event(
            50, StructureEvent.CHOCH_FAILED, MarketDirection.BEARISH, reference_level=85.0
        ),
    ]

    assert _drop_failed_refire_cycles(events) == events


def test_drop_pre_break_reference_bos_provisional_choch_failed_does_not_reset() -> None:
    # The provisional fizzle marker does not move the trend, so it must not reset
    # the constraint: a pre-break-reference bearish continuation is still dropped.
    events = [
        _structure_event(10, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH),
        _structure_event(
            30, StructureEvent.CHOCH_FAILED, MarketDirection.BULLISH, provisional=True
        ),
        _structure_event(
            40, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH, reference_minute=5
        ),
    ]

    kept = _drop_pre_break_reference_bos(events)

    assert [e.timestamp.minute for e in kept] == [10, 30]


def test_reanchor_fills_none_reference_from_opposite_polarity_origin() -> None:
    # The first BOS of a leg reports the CHoCH-seeded floor, whose origin is the
    # reversal *top* (a high) for a bearish BOS -- the detector's own-side (low)
    # scan finds nothing and leaves reference_timestamp None (the ETH H4 1721.57
    # case, which drew from the chart edge). The re-anchor pass must fill it from
    # the level's opposite-polarity origin.
    candles = make_series(
        [99.0, 99.0, 100.0, 98.0, 97.0, 96.0],
        [95.0, 94.0, 96.0, 92.0, 90.0, 88.0],
    )
    bos = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=candles[3].timestamp,
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=MarketDirection.BEARISH,
        price_level=88.0,
        reference_price_level=100.0,
        reference_timestamp=None,
        scope=StructureScope.INTERNAL,
    )

    (result,) = _reanchor_bos_close_break([bos], candles)

    assert result.reference_timestamp == candles[2].timestamp


def _event_at(
    candles: list[Candle],
    index: int,
    event: StructureEvent,
    direction: MarketDirection,
    *,
    reference_level: float,
    price_level: float = 100.0,
) -> MarketStructure:
    return MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=candles[index].timestamp,
        event=event,
        direction=direction,
        price_level=price_level,
        reference_price_level=reference_level,
        reference_timestamp=None,
        scope=StructureScope.INTERNAL,
    )


def _leg_launch_series() -> list[Candle]:
    # Bearish leg modeled on the ENA M30 case: the launch fundo (low 90.0)
    # forms at candle 1, price retests the CHoCH, and the first close below
    # 90.0 (candle 6, close 89.0) lands *after* the successor BOS's raw stamp
    # (candle 5) -- inside its territory. Closes are high/low midpoints.
    return make_series(
        [100.0, 96.0, 98.0, 97.0, 95.0, 94.0, 90.0, 95.0, 99.0],
        [95.0, 90.0, 94.0, 93.0, 91.0, 90.5, 88.0, 91.0, 95.0],
    )


def test_reanchor_rescues_leg_launch_bos_and_suppresses_passed_over_continuation() -> None:
    # The leg-launch BOS (ref 90.0, the CHoCH-seeded fundo) finds no close in
    # its own window (closes 93.0, 92.25 at candles 4-5); the first close below
    # lands at candle 6 (89.0), inside the successor's territory. Rescued, it is
    # re-timed to that close, its line starts at the fundo (candle 1), and the
    # passed-over shallower continuation (ref 92.0) is suppressed.
    candles = _leg_launch_series()
    choch = _event_at(
        candles, 2, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH,
        reference_level=95.0,
    )
    launch = _event_at(
        candles, 4, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH,
        reference_level=90.0,
    )
    shallow = _event_at(
        candles, 5, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH,
        reference_level=92.0,
    )
    reversal = _event_at(
        candles, 8, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH,
        reference_level=94.0,
    )

    kept = _reanchor_bos_close_break(
        [choch, launch, shallow, reversal], candles, rescue_leg_launch=True
    )

    bos = [e for e in kept if e.event is StructureEvent.BREAK_OF_STRUCTURE]
    assert [e.reference_price_level for e in bos] == [90.0]
    assert bos[0].timestamp == candles[6].timestamp
    assert bos[0].reference_timestamp == candles[1].timestamp
    assert choch in kept and reversal in kept


def test_reanchor_rescue_off_drops_leg_launch_and_keeps_shallow_successor() -> None:
    # Without the rescue (the pre-existing behavior), the launch BOS is dropped
    # as wick-only and the shallow successor survives as first-of-leg.
    candles = _leg_launch_series()
    choch = _event_at(
        candles, 2, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH,
        reference_level=95.0,
    )
    launch = _event_at(
        candles, 4, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH,
        reference_level=90.0,
    )
    shallow = _event_at(
        candles, 5, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH,
        reference_level=92.0,
    )

    kept = _reanchor_bos_close_break([choch, launch, shallow], candles)

    bos = [e for e in kept if e.event is StructureEvent.BREAK_OF_STRUCTURE]
    assert [e.reference_price_level for e in bos] == [92.0]


def test_reanchor_rescue_only_applies_to_the_leg_launch_bos() -> None:
    # A mid-leg continuation (a same-direction BOS precedes it in the leg) gets
    # no extended search: failing its own window still drops it, even though a
    # qualifying close exists later.
    candles = _leg_launch_series()
    choch = _event_at(
        candles, 2, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH,
        reference_level=95.0,
    )
    first = _event_at(
        candles, 3, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH,
        reference_level=95.0,
    )
    target = _event_at(
        candles, 4, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH,
        reference_level=90.0,
    )
    successor = _event_at(
        candles, 5, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH,
        reference_level=92.0,
    )

    kept = _reanchor_bos_close_break(
        [choch, first, target, successor], candles, rescue_leg_launch=True
    )

    assert 90.0 not in [
        e.reference_price_level for e in kept if e.event is StructureEvent.BREAK_OF_STRUCTURE
    ]


def test_reanchor_rescue_still_drops_when_leg_dies_without_close_through() -> None:
    # The leg reverses (opposite CHoCH at candle 6) before any close through the
    # launch floor: the wick-only protection stands and the BOS is dropped.
    candles = make_series(
        [100.0, 96.0, 98.0, 97.0, 95.0, 94.0, 95.0, 99.0],
        [95.0, 90.0, 94.0, 93.0, 91.0, 90.5, 91.0, 95.0],
    )
    choch = _event_at(
        candles, 2, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH,
        reference_level=95.0,
    )
    launch = _event_at(
        candles, 4, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH,
        reference_level=90.0,
    )
    reversal = _event_at(
        candles, 6, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH,
        reference_level=94.0,
    )

    kept = _reanchor_bos_close_break([choch, launch, reversal], candles, rescue_leg_launch=True)

    assert [e.event for e in kept] == [
        StructureEvent.CHANGE_OF_CHARACTER,
        StructureEvent.CHANGE_OF_CHARACTER,
    ]


def test_reanchor_rescue_is_bounded_to_one_continuation() -> None:
    # The AAVEUSDT D1 shape: a launch BOS whose floor (85.0) sits far beyond the
    # leg. An unbounded search would scan until the leg died and suppress the
    # real staircase it passed (the measured 176.46 -> 145.0 -> 91.85 loss); the
    # bound stops at the *second* continuation, so the launch BOS is dropped as
    # before and both continuations survive.
    candles = make_series(
        [100.0, 96.0, 98.0, 97.0, 95.0, 94.0, 93.0, 92.0, 91.0, 84.0],
        [95.0, 90.0, 94.0, 93.0, 91.0, 90.5, 89.0, 88.0, 87.0, 82.0],
    )
    choch = _event_at(
        candles, 2, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH,
        reference_level=95.0,
    )
    launch = _event_at(
        candles, 4, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH,
        reference_level=85.0,
    )
    first_successor = _event_at(
        candles, 5, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH,
        reference_level=92.0,
    )
    second_successor = _event_at(
        candles, 7, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH,
        reference_level=90.0,
    )

    kept = _reanchor_bos_close_break(
        [choch, launch, first_successor, second_successor], candles, rescue_leg_launch=True
    )

    # The close through 85.0 only comes at candle 9, past the second
    # continuation -- too late to be the leg's launch break.
    bos = [e for e in kept if e.event is StructureEvent.BREAK_OF_STRUCTURE]
    assert [e.reference_price_level for e in bos] == [92.0, 90.0]


def test_reanchor_rescues_bullish_leg_launch_bos() -> None:
    # Bullish mirror: launch topo 110.0 at candle 1, first close above it at
    # candle 6 (111.0), past the shallow successor's (ref 108.0) raw stamp.
    candles = make_series(
        [105.0, 110.0, 106.0, 107.0, 109.0, 109.5, 112.0, 109.0, 105.0],
        [95.0, 104.0, 100.0, 101.0, 105.0, 106.0, 110.0, 105.0, 95.0],
    )
    choch = _event_at(
        candles, 2, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH,
        reference_level=105.0,
    )
    launch = _event_at(
        candles, 4, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH,
        reference_level=110.0,
    )
    shallow = _event_at(
        candles, 5, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH,
        reference_level=108.0,
    )
    reversal = _event_at(
        candles, 8, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH,
        reference_level=106.0,
    )

    kept = _reanchor_bos_close_break(
        [choch, launch, shallow, reversal], candles, rescue_leg_launch=True
    )

    bos = [e for e in kept if e.event is StructureEvent.BREAK_OF_STRUCTURE]
    assert [e.reference_price_level for e in bos] == [110.0]
    assert bos[0].timestamp == candles[6].timestamp
    assert bos[0].reference_timestamp == candles[1].timestamp


def test_drop_pre_break_reference_bos_keeps_unresolved_reference() -> None:
    # No reference_timestamp resolved: nothing to judge, keep the event.
    events = [
        _structure_event(10, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH),
        _structure_event(20, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH),
    ]

    assert _drop_pre_break_reference_bos(events) == events


def test_drop_pre_break_reference_bos_tracks_directions_independently() -> None:
    # A bearish BOS does not constrain the bullish staircase (and vice versa).
    events = [
        _structure_event(10, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH),
        _structure_event(
            20, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, reference_minute=5
        ),
    ]

    assert _drop_pre_break_reference_bos(events) == events


def test_drop_pre_break_reference_bos_same_timestamp_judges_earlier_reference_first() -> None:
    # Two BOS re-timed to the same confirming candle: the one whose reference
    # formed earlier is the earlier structural break -- it is kept and sets the
    # leg's close, so the later-referenced (staged) one is judged against it
    # and dropped, regardless of list order.
    first_of_leg = _structure_event(
        20, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, reference_minute=5
    )
    staged = _structure_event(
        20, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, reference_minute=15
    )

    assert _drop_pre_break_reference_bos([staged, first_of_leg]) == [first_of_leg]


def test_drop_resumed_fizzle_markers_drops_marker_before_same_direction_bos() -> None:
    # A fizzle marker (provisional CHOCH_FAILED) followed by a surviving
    # same-direction BOS: the reclaim was a deep pullback the reversal
    # recovered from, so the marker is a false invalidation and is dropped.
    choch = _structure_event(10, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH)
    fizzle = _structure_event(
        20, StructureEvent.CHOCH_FAILED, MarketDirection.BULLISH, provisional=True
    )
    bos = _structure_event(30, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH)

    assert _drop_resumed_fizzle_markers([choch, fizzle, bos], []) == [choch, bos]


def test_drop_resumed_fizzle_markers_keeps_marker_with_no_later_bos() -> None:
    # No same-direction BOS after the reclaim: the reversal genuinely fizzled
    # (price ranges beyond the reclaimed level), the marker stands.
    choch = _structure_event(10, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH)
    fizzle = _structure_event(
        20, StructureEvent.CHOCH_FAILED, MarketDirection.BEARISH, provisional=True
    )

    assert _drop_resumed_fizzle_markers([choch, fizzle], []) == [choch, fizzle]


def test_drop_resumed_fizzle_markers_ignores_opposite_direction_bos() -> None:
    # An opposite-direction BOS is not the marked reversal resuming.
    fizzle = _structure_event(
        20, StructureEvent.CHOCH_FAILED, MarketDirection.BEARISH, provisional=True
    )
    bos = _structure_event(30, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH)

    assert _drop_resumed_fizzle_markers([fizzle, bos], []) == [fizzle, bos]


def test_drop_resumed_fizzle_markers_ignores_earlier_and_provisional_bos() -> None:
    # A BOS before the reclaim does not prove recovery, and a provisional
    # (live-edge) BOS may still vanish -- neither cancels the marker.
    early_bos = _structure_event(10, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH)
    fizzle = _structure_event(
        20, StructureEvent.CHOCH_FAILED, MarketDirection.BEARISH, provisional=True
    )
    prov_bos = _structure_event(
        30, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH, provisional=True
    )

    events = [early_bos, fizzle, prov_bos]
    assert _drop_resumed_fizzle_markers(events, []) == events


def _minute_candle(minute: int, *, low: float, high: float, close: float) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.M15,
        timestamp=datetime(2026, 7, 2, 0, minute, tzinfo=UTC),
        open=close,
        high=high,
        low=low,
        close=close,
        volume=1.0,
        taker_buy_volume=0.5,
    )


def test_drop_resumed_fizzle_markers_drops_marker_on_new_extreme_close() -> None:
    # After the marker, a candle *closes* beyond the marked CHoCH's own
    # extreme (price_level=100.0, the fundo of the bearish reversal): the leg
    # resumed even though its BOS has not confirmed a pullback yet, so the
    # marker is a false invalidation (the SOL M15 2026-07-16 case).
    choch = _structure_event(10, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH)
    fizzle = _structure_event(
        20, StructureEvent.CHOCH_FAILED, MarketDirection.BEARISH, provisional=True
    )
    candles = [_minute_candle(30, low=94.0, high=99.0, close=95.0)]

    assert _drop_resumed_fizzle_markers([choch, fizzle], candles) == [choch]


def test_drop_resumed_fizzle_markers_keeps_marker_without_new_extreme() -> None:
    # Price stays on the reclaim side of the CHoCH extreme: genuine fizzle.
    choch = _structure_event(10, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH)
    fizzle = _structure_event(
        20, StructureEvent.CHOCH_FAILED, MarketDirection.BEARISH, provisional=True
    )
    candles = [_minute_candle(30, low=101.0, high=106.0, close=105.0)]

    assert _drop_resumed_fizzle_markers([choch, fizzle], candles) == [choch, fizzle]


def test_drop_resumed_fizzle_markers_wick_beyond_extreme_does_not_cancel() -> None:
    # A wick through the CHoCH extreme that closes back inside is a sweep,
    # not resumption -- the cancel is close-based.
    choch = _structure_event(10, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH)
    fizzle = _structure_event(
        20, StructureEvent.CHOCH_FAILED, MarketDirection.BEARISH, provisional=True
    )
    candles = [_minute_candle(30, low=95.0, high=103.0, close=101.0)]

    assert _drop_resumed_fizzle_markers([choch, fizzle], candles) == [choch, fizzle]


def test_drop_superseded_provisional_choch_drops_on_later_opposite_advance() -> None:
    # A provisional CHoCH? (e.g. a staged range-breakout reversal) invalidated by
    # a later real opposite-direction BOS -- the reversal failed, so it is dropped.
    prov_choch = _structure_event(
        20, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH, provisional=True
    )
    bos = _structure_event(30, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH)

    assert _drop_superseded_provisional_choch([prov_choch, bos]) == [bos]


def test_drop_superseded_provisional_choch_drops_on_later_same_advance() -> None:
    # A provisional CHoCH? superseded by a later real same-direction CHoCH -- real
    # structure has resumed the reversal, so the stale mark is redundant and dropped.
    prov_choch = _structure_event(
        20, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH, provisional=True
    )
    real_choch = _structure_event(
        40, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH
    )

    assert _drop_superseded_provisional_choch([prov_choch, real_choch]) == [real_choch]


def test_drop_superseded_provisional_choch_keeps_live_edge_mark() -> None:
    # No real advance after the provisional CHoCH? -- it is a genuine live-edge
    # forming mark (its fate is not yet settled), so it survives, honoring the `?`.
    real_bos = _structure_event(10, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH)
    prov_choch = _structure_event(
        30, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH, provisional=True
    )

    events = [real_bos, prov_choch]
    assert _drop_superseded_provisional_choch(events) == events


def test_drop_superseded_provisional_choch_ignores_later_provisional() -> None:
    # A later *provisional* advance is not settled structure -- it may itself
    # vanish -- so it does not supersede the CHoCH? mark.
    prov_choch = _structure_event(
        20, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH, provisional=True
    )
    prov_bos = _structure_event(
        30, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, provisional=True
    )

    events = [prov_choch, prov_bos]
    assert _drop_superseded_provisional_choch(events) == events


def test_drop_resumed_fizzle_markers_keeps_genuine_choch_failed() -> None:
    # A non-provisional CHOCH_FAILED is a real state-machine failure (the trend
    # flipped back); the resumed trend's BOS after it is expected, not a cancel.
    failed = _structure_event(20, StructureEvent.CHOCH_FAILED, MarketDirection.BULLISH)
    bos = _structure_event(30, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH)

    assert _drop_resumed_fizzle_markers([failed, bos], []) == [failed, bos]


def _load_eth_1h_fizzle_candles() -> list[Candle]:
    """ETHUSDT 1h 2026-05-10..07-11, the full 1500-candle production slice.

    Real-data regression for the resumed-fizzle drop: the 2026-06-29 16:00
    bullish CHoCH (ref 1583) was reclaim-marked by the detector's fizzle on
    06-30 11:00, but the reversal then resumed with a bullish BOS staircase
    (07-01 onward, 1630 -> 1833) -- the marker is a false invalidation, and
    on the chart it also let the 06-19 bearish CHoCH line run to the edge
    (the failed-CHoCH transparency rule). 5-column rows: ts/open/high/low/
    close (volume is irrelevant to structure detection).
    """
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "ethusdt_1h_2026_05_10_07_11.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    return [
        Candle(
            symbol="ETHUSDT",
            timeframe=TimeFrame.H1,
            timestamp=datetime.fromisoformat(row[0]),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for row in rows
    ]


class _FuturesLimitFakeProvider(_PerTimeframeFakeProvider):
    """The production candle source's 1500-candle per-request window."""

    max_fetch_limit = 1500


def test_run_internal_structure_drops_eth_1h_resumed_fizzle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With the pending-fail wired on, the 06-27 reclaim of 1583 is a *real*
    # CHOCH_FAILED (it preempts the additive fizzle marker on this window).
    # The fizzle marker still exists for reclaims that sustain fewer closes
    # than the pending-fail persistence, so the composition drop it exercises
    # here needs the flag off to stay reproducible on this fixture.
    monkeypatch.setattr(dashboard_data, "_CHOCH_PENDING_FAIL_AT_BROKEN_LEVEL", False)
    # The origin-buffer gate (`choch_fizzle_reclaim_origin_buffer_atr`) would
    # otherwise suppress this fizzle at the source -- the 1583 reclaim only
    # half-retraces the rally (down to ~1556, the leg launched from ~1510) -- but
    # this test exercises the *composition* drop (`_drop_resumed_fizzle_markers`),
    # which needs a raw marker to exist. Disable the gate so the raw detector
    # still emits it (production catches this window via the real pending-fail).
    monkeypatch.setattr(
        dashboard_data, "_CHOCH_FIZZLE_RECLAIM_ORIGIN_BUFFER_ATR", None
    )
    candles = _load_eth_1h_fizzle_candles()
    provider = _FuturesLimitFakeProvider({TimeFrame.H1: candles})

    run = _run_internal_structure(provider, "ETHUSDT", TimeFrame.H1, 1200, False)

    # The raw detector run emits the fizzle marker (the reclaim of 1583 is
    # real) -- the composition pass is what must cancel it, because the
    # cancelling BOS staircase survives the close-break re-anchor.
    raw = _build_internal_detector(TimeFrame.H1, confluence_filter=False).detect(
        run.internal_candles
    )
    assert any(
        e.event is StructureEvent.CHOCH_FAILED and e.provisional for e in raw
    )
    assert not any(
        e.event is StructureEvent.CHOCH_FAILED and e.provisional for e in run.events
    )
    # The standing bullish CHoCH itself is untouched.
    assert any(
        e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BULLISH
        and e.timestamp == datetime(2026, 6, 27, 10, tzinfo=UTC)
        for e in run.events
    )


def _load_btc_1d_weak_fail_candles() -> list[Candle]:
    """BTCUSDT 1d 2022-06-03..2026-07-11, the full 1500-candle production slice.

    Originally the real-data regression for
    ``choch_weak_ref_fail_at_broken_level`` (at base persistence 12 the trend
    sat bullish through the entire 82.8k -> 57.7k crash: the 2026-04-30
    bullish CHoCH fired against the weak 75998.9 re-anchor and its origin was
    never sustained-broken). At base persistence 2 with the pending-fail
    wired the window resolves upstream -- see the two tests below for the
    current production reading. 5-column rows: ts/open/high/low/close.
    """
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "btcusdt_1d_2022_06_03_2026_07_11.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    return [
        Candle(
            symbol="BTCUSDT",
            timeframe=TimeFrame.D1,
            timestamp=datetime.fromisoformat(row[0]),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for row in rows
    ]


def test_btc_1d_crash_resolves_bearish_with_bottom_bos() -> None:
    """Production reading of the 2026 BTC D1 crash window. Originally the
    real-data lock for ``choch_weak_ref_fail_at_broken_level`` (the trend sat
    bullish through the whole -30%); at base persistence 2 with the
    pending-fail wired, the window resolves upstream instead. With
    ``choch_failed_rearm`` wired (2026-07-16) the upstream history shifts
    once more (the August 2024 flush -- a re-fired bearish CHoCH where the
    old reading kept a bullish trend through the 70k -> 49k crash -- cascades
    different trailing references into 2026). With
    ``choch_failed_rearm_persistent`` wired (2026-07-17) this fixture is also
    the real-data lock for the persistent re-arm itself: the August 2025
    bearish CHoCH at 111850 fails (09-10), its September re-fire dies on the
    October rally (collapsed by ``_drop_failed_refire_cycles``), the rally
    tops in a marginal-high sweep (the 10-06 BOS at 126208), and the
    persistent memory re-fires the CHoCH at the proven 111850 level on 10-15
    -- ``reference_timestamp`` re-anchored to the surviving 09-10 failure, so
    the chart draws consecutive ``✕ -> ↻`` segments along the level -- after
    which the crash prints as a bearish BOS staircase. The protected
    conclusion is unchanged: the January bullish CHoCH dies on the crash's
    first leg (pending-fail, now 01-18), and June prints the continuation
    BOS at the bottom with the standing trend bearish. (The April 2026
    bullish CHoCH the pre-flag reading showed at 75998.9 is window-sensitive
    on this frozen series -- the January cascade leaves a trailing
    ``active_high`` that blocks the staleness re-anchor's establish -- and
    reads as sweeps here; the live window keeps it, see
    ``_CHOCH_FAILED_REARM_PERSISTENT``.)"""
    candles = _load_btc_1d_weak_fail_candles()
    provider = _FuturesLimitFakeProvider({TimeFrame.D1: candles})

    run = _run_internal_structure(provider, "BTCUSDT", TimeFrame.D1, 1200, False)

    # August 2025: the bearish CHoCH at 111850 fails on the sustained reclaim.
    assert any(
        e.event is StructureEvent.CHOCH_FAILED
        and not e.provisional
        and e.direction is MarketDirection.BEARISH
        and e.timestamp == datetime(2025, 9, 10, tzinfo=UTC)
        and e.reference_price_level == pytest.approx(111850.0)
        for e in run.events
    )
    # October: the persistent re-arm re-fires the CHoCH at the proven level
    # once the sweep-shaped top is given back, anchored at the surviving
    # failure (the collapsed September cycle's own failure is remapped).
    assert any(
        e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BEARISH
        and e.timestamp == datetime(2025, 10, 15, tzinfo=UTC)
        and e.reference_price_level == pytest.approx(111850.0)
        and e.reference_timestamp == datetime(2025, 9, 10, tzinfo=UTC)
        for e in run.events
    )
    # January: the bullish CHoCH is invalidated by the crash's first leg
    # (a real pending-fail, not a fizzle marker). It confirms on 01-19, the
    # first close that clears the level by the `_CHOCH_FAIL_LEVEL_BUFFER_ATR`
    # noise band -- the crash keeps going, so the reading is unchanged.
    assert any(
        e.event is StructureEvent.CHOCH_FAILED
        and not e.provisional
        and e.direction is MarketDirection.BULLISH
        and e.timestamp == datetime(2026, 1, 19, tzinfo=UTC)
        for e in run.events
    )
    # ... and June prints the continuation BOS at the bottom.
    assert any(
        e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BEARISH
        and e.timestamp == datetime(2026, 6, 25, tzinfo=UTC)
        and e.reference_price_level == pytest.approx(59800.0)
        for e in run.events
    )
    assert run.trend is MarketDirection.BEARISH


def test_btc_1d_rearm_persistent_off_refire_lost_to_late_weak_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pathology lock (`choch_failed_rearm_persistent` off): the one-shot
    re-arm chain dies with the collapsed September re-fire cycle, so when the
    October rally's sweep-shaped top (126208 over 124546) is fully given back
    nothing re-fires at the proven 111850 level -- the first break below it
    reads as a sweep and the crash's reversal waits for the late, weak
    trailing reference at 98888.8 (11-14, eleven candles after the 111850
    break)."""
    monkeypatch.setattr(dashboard_data, "_CHOCH_FAILED_REARM_PERSISTENT", False)
    candles = _load_btc_1d_weak_fail_candles()
    provider = _FuturesLimitFakeProvider({TimeFrame.D1: candles})

    run = _run_internal_structure(provider, "BTCUSDT", TimeFrame.D1, 1200, False)

    # No October re-fire at the proven level...
    assert not any(
        e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BEARISH
        and datetime(2025, 10, 1, tzinfo=UTC) <= e.timestamp <= datetime(2025, 11, 1, tzinfo=UTC)
        for e in run.events
    )
    # ... the break below it reads as a sweep, and the reversal only lands at
    # the weak trailing low a month later.
    assert any(
        e.event is StructureEvent.LIQUIDITY_SWEEP
        and e.direction is MarketDirection.BEARISH
        and e.timestamp == datetime(2025, 11, 4, tzinfo=UTC)
        for e in run.events
    )
    assert any(
        e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BEARISH
        and e.timestamp == datetime(2025, 11, 14, tzinfo=UTC)
        and e.reference_price_level == pytest.approx(98888.8)
        for e in run.events
    )


def test_btc_1d_weak_fail_off_conclusion_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``choch_weak_ref_fail_at_broken_level`` off, the crash window
    reaches the same conclusion: at base persistence 2 + the pending-fail,
    the stuck-bullish pathology this flag was built for (2026-07-12) no
    longer returns on this fixture -- the flag is belt-and-suspenders here,
    still load-bearing for weak references generally."""
    monkeypatch.setattr(dashboard_data, "_CHOCH_WEAK_REF_FAIL_AT_BROKEN_LEVEL", False)
    candles = _load_btc_1d_weak_fail_candles()
    provider = _FuturesLimitFakeProvider({TimeFrame.D1: candles})

    run = _run_internal_structure(provider, "BTCUSDT", TimeFrame.D1, 1200, False)

    assert any(
        e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BEARISH
        and e.timestamp == datetime(2026, 5, 27, tzinfo=UTC)
        and e.reference_price_level == pytest.approx(74868.0)
        for e in run.events
    )
    assert any(
        e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BEARISH
        and e.timestamp == datetime(2026, 6, 30, tzinfo=UTC)
        and e.reference_price_level == pytest.approx(59080.0)
        for e in run.events
    )
    assert run.trend is MarketDirection.BEARISH


def _load_near_1h_success_displacement_candles() -> list[Candle]:
    """NEARUSDT 1h 2026-05-12..07-13, the full 1500-candle production slice.

    Real-data regression for ``choch_success_displacement_atr``: two bullish
    CHoCHs (2026-06-08 at 2.045, 2026-06-14 at 2.173) each rallied hard -- to
    2.264 (~5.0 ATR) and 2.562 (~7.6 ATR) -- but the impulsive legs emitted no
    confirming BOS (no pullback pivot formed in the impulse), so their origins
    stayed armed and the later mean-reversion marked both a false CHOCH_FAILED
    even though the reversals plainly succeeded. 5-column rows:
    ts/open/high/low/close.
    """
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "nearusdt_1h_2026_05_11_07_13.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    return [
        Candle(
            symbol="NEARUSDT",
            timeframe=TimeFrame.H1,
            timestamp=datetime.fromisoformat(row[0]),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for row in rows
    ]


def test_near_1h_displaced_bullish_choch_not_marked_failed() -> None:
    candles = _load_near_1h_success_displacement_candles()
    provider = _FuturesLimitFakeProvider({TimeFrame.H1: candles})

    run = _run_internal_structure(provider, "NEARUSDT", TimeFrame.H1, 1200, False)

    # Neither displaced bullish CHoCH is marked failed: their origins retire on
    # the displacement, so the pullbacks are ordinary structure, not failures.
    bullish_fails = [
        e
        for e in run.events
        if e.event is StructureEvent.CHOCH_FAILED
        and e.direction is MarketDirection.BULLISH
        and datetime(2026, 6, 6, tzinfo=UTC) <= e.timestamp <= datetime(2026, 6, 22, tzinfo=UTC)
    ]
    assert bullish_fails == []
    # The 2026-06-08 bullish CHoCH still stands (the reversal that succeeded).
    assert any(
        e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BULLISH
        and e.timestamp == datetime(2026, 6, 8, 5, tzinfo=UTC)
        for e in run.events
    )


def test_near_1h_displacement_retirement_off_marks_false_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dashboard_data, "_CHOCH_SUCCESS_DISPLACEMENT_ATR", None)
    candles = _load_near_1h_success_displacement_candles()
    provider = _FuturesLimitFakeProvider({TimeFrame.H1: candles})

    run = _run_internal_structure(provider, "NEARUSDT", TimeFrame.H1, 1200, False)

    # Off: the displaced bullish CHoCHs are falsely marked failed on their
    # pullbacks -- the 06-08 rally (~5 ATR to 2.264, no confirming BOS) at
    # the 2.045 origin it launched from, plus the 06-12 leg at its broken
    # level (2.083 -- the pending-fail arms it, and without the displacement
    # retirement an impulsive success dies on its own pullback). With
    # `choch_failed_rearm` wired the third false failure the pre-rearm
    # reading showed (2.173) no longer exists: the 2.083 failure arms a
    # re-arm, the 06-13 push re-fires the bullish CHoCH at the same 2.083
    # level, and the 06-14 rally BOS confirms it -- the re-arm partially
    # mitigates the very pathology this off-mode test documents. With the
    # displacement flag on (production) none of these failures fire.
    fail_refs = {
        round(e.reference_price_level, 3)
        for e in run.events
        if e.event is StructureEvent.CHOCH_FAILED
        and e.direction is MarketDirection.BULLISH
        and datetime(2026, 6, 6, tzinfo=UTC) <= e.timestamp <= datetime(2026, 6, 22, tzinfo=UTC)
    }
    assert fail_refs == {2.045, 2.083}
    # The re-fired CHoCH at the failed level (the re-arm in action).
    assert any(
        e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BULLISH
        and e.timestamp == datetime(2026, 6, 13, 15, tzinfo=UTC)
        and e.reference_price_level == pytest.approx(2.083)
        for e in run.events
    )


def test_near_1h_choch_failed_never_predates_its_choch() -> None:
    candles = _load_near_1h_success_displacement_candles()
    provider = _FuturesLimitFakeProvider({TimeFrame.H1: candles})

    run = _run_internal_structure(provider, "NEARUSDT", TimeFrame.H1, 1200, False)

    # A CHOCH_FAILED can never be timestamped before the CHoCH it invalidates:
    # every non-provisional failure must have a preceding same-direction CHoCH.
    # (Before the arm-index scan bound, the backward reclaim scan grabbed the
    # pre-CHoCH rally through the fail level and stamped a phantom bearish
    # `CHoCH ✕ ▼` at 2026-06-15 16:00 -- mid rally, before the 06-16 14:00
    # bearish CHoCH it supposedly failed.)
    chochs = [e for e in run.events if e.event is StructureEvent.CHANGE_OF_CHARACTER]
    for failure in run.events:
        if failure.event is StructureEvent.CHOCH_FAILED and not failure.provisional:
            assert any(
                c.direction is failure.direction and c.timestamp < failure.timestamp
                for c in chochs
            ), f"CHOCH_FAILED at {failure.timestamp} has no preceding {failure.direction} CHoCH"

    # No bearish failure survives in the rally/top window (06-15..06-16 12:00)
    # -- the old phantom `CHoCH ✕ ▼` was stamped mid-rally, before the bearish
    # CHoCH it supposedly failed. The genuine bearish reversal off the 2.543
    # top confirms on 06-19 (at base persistence 2 + the confirmed-trend
    # barrier, the earlier pokes at the top are counter-trend sweeps).
    rally_top_window = (datetime(2026, 6, 15, tzinfo=UTC), datetime(2026, 6, 16, 12, tzinfo=UTC))
    assert not any(
        e.event is StructureEvent.CHOCH_FAILED
        and e.direction is MarketDirection.BEARISH
        and rally_top_window[0] <= e.timestamp <= rally_top_window[1]
        for e in run.events
    )
    assert any(
        e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BEARISH
        and e.timestamp == datetime(2026, 6, 19, 7, tzinfo=UTC)
        for e in run.events
    )


def _load_aave_1h_pending_fail_candles() -> list[Candle]:
    """AAVEUSDT 1h 2026-05-15..07-16, the full 1500-candle production slice.

    Real-data regression for ``choch_pending_fail_at_broken_level``: the
    2026-07-08 12:00 bearish CHoCH broke the *structural* 87.90 leg origin,
    no bearish BOS ever confirmed it (the drop was impulsive, no pullback
    pivot), and both exits -- the origin CHOCH_FAILED and the reverse-CHoCH
    reference -- sat at the reversed leg's 97.4 extreme. Without the
    pending-fail, the +14% recovery rally printed as three bullish sweeps
    (88.85, 93.1, 98.28) under a stale bearish trend until 97.4 broke on
    07-11. 5-column rows: ts-ms/open/high/low/close.
    """
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "aaveusdt_1h_2026_05_15_07_16.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    return [
        Candle(
            symbol="AAVEUSDT",
            timeframe=TimeFrame.H1,
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for row in rows
    ]


def test_aave_1h_pending_choch_fails_at_broken_level() -> None:
    candles = _load_aave_1h_pending_fail_candles()
    provider = _FuturesLimitFakeProvider({TimeFrame.H1: candles})

    run = _run_internal_structure(provider, "AAVEUSDT", TimeFrame.H1, 1200, False)

    # The unconfirmed 07-08 bearish CHoCH still fires (it broke a structural
    # level and price held below for the base persistence)...
    assert any(
        e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BEARISH
        and e.timestamp == datetime(2026, 7, 8, 12, tzinfo=UTC)
        and e.reference_price_level == pytest.approx(87.9)
        for e in run.events
    )
    # ... but dies for real once the rally sustains closes back above the
    # 87.90 level it broke (no confirming BOS ever retired the level). The
    # failure lands at 11:00, the first close that clears the level by the
    # `_CHOCH_FAIL_LEVEL_BUFFER_ATR` band rather than by a hair; the rally runs
    # to 98 either way, so the conclusion is untouched.
    assert any(
        e.event is StructureEvent.CHOCH_FAILED
        and not e.provisional
        and e.direction is MarketDirection.BEARISH
        and e.timestamp == datetime(2026, 7, 9, 11, tzinfo=UTC)
        and e.reference_price_level == pytest.approx(87.9)
        for e in run.events
    )
    # The resumed bullish trend prints the rally's continuation BOS against
    # the restored pre-CHoCH staircase (the genuine 97.4 top), instead of the
    # whole recovery reading as sweeps.
    assert any(
        e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BULLISH
        and e.timestamp == datetime(2026, 7, 10, 9, tzinfo=UTC)
        and e.reference_price_level == pytest.approx(97.4)
        for e in run.events
    )


def test_aave_1h_pending_fail_off_leaves_stale_bearish_trend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dashboard_data, "_CHOCH_PENDING_FAIL_AT_BROKEN_LEVEL", False)
    candles = _load_aave_1h_pending_fail_candles()
    provider = _FuturesLimitFakeProvider({TimeFrame.H1: candles})

    run = _run_internal_structure(provider, "AAVEUSDT", TimeFrame.H1, 1200, False)

    # Off: the 07-08 bearish CHoCH is never invalidated -- no CHOCH_FAILED
    # before the far 97.4 origin finally breaks, and the recovery rally
    # prints as counter-trend sweeps (the motivating stale-trend pathology).
    assert not any(
        e.event is StructureEvent.CHOCH_FAILED
        and datetime(2026, 7, 8, 12, tzinfo=UTC) < e.timestamp < datetime(2026, 7, 11, tzinfo=UTC)
        for e in run.events
    )
    assert any(
        e.event is StructureEvent.LIQUIDITY_SWEEP
        and e.direction is MarketDirection.BULLISH
        and e.timestamp == datetime(2026, 7, 10, 1, tzinfo=UTC)
        for e in run.events
    )


def _load_sol_m15_fizzle_cancel_candles() -> list[Candle]:
    """SOLUSDT 15m 2026-06-30..07-16, the full 1500-candle production slice.

    Real-data regression for the price-based resumed-fizzle cancel: the
    2026-07-16 00:15 bearish CHoCH (ref 77.21) got a fizzle marker at 05:30
    when a shallow bounce sustained six closes back above the level -- then
    price crashed straight through the CHoCH's own 76.64 fundo two hours
    later. The reversal plainly resumed, but no bearish BOS had confirmed a
    pullback yet, so the BOS-based cancel alone left a `✕` next to a CHoCH
    that worked. Reclaim depth cannot separate this false marker (1.18 ATR)
    from the genuine June fizzle (0.98 ATR); the resumption close can.
    5-column rows: ts-ms/open/high/low/close.
    """
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "solusdt_15m_2026_07_01_07_16.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    return [
        Candle(
            symbol="SOLUSDT",
            timeframe=TimeFrame.M15,
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for row in rows
    ]


def test_sol_m15_resumed_fizzle_cancelled_by_new_extreme() -> None:
    candles = _load_sol_m15_fizzle_cancel_candles()
    provider = _FuturesLimitFakeProvider({TimeFrame.M15: candles})

    run = _run_internal_structure(provider, "SOLUSDT", TimeFrame.M15, 1200, False)

    # The bearish CHoCH stands, un-invalidated: no CHOCH_FAILED (real or
    # marker) after it, and the shallow reclaim reads as a bullish sweep.
    assert any(
        e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BEARISH
        and e.timestamp == datetime(2026, 7, 16, 0, 15, tzinfo=UTC)
        and e.reference_price_level == pytest.approx(77.21)
        for e in run.events
    )
    assert not any(
        e.event is StructureEvent.CHOCH_FAILED
        and e.timestamp > datetime(2026, 7, 16, tzinfo=UTC)
        for e in run.events
    )
    assert run.trend is MarketDirection.BEARISH


def test_sol_m15_shallow_reclaim_does_not_fizzle_at_live_edge() -> None:
    # Truncated right after the reclaim (before the crash). The reclaim only
    # tags the broken level (77.21 -> ~77.42, +0.27%), nowhere near recovering
    # the leg's origin (~78-79). Under the origin-buffer gate
    # (`choch_fizzle_reclaim_origin_buffer_atr`) a retest of the broken level is
    # a routine pullback into the counter-zone, not a fizzle, so no marker fires
    # even at the live edge -- the bearish CHoCH stands.
    candles = [
        c
        for c in _load_sol_m15_fizzle_cancel_candles()
        if c.timestamp <= datetime(2026, 7, 16, 6, 45, tzinfo=UTC)
    ]
    provider = _FuturesLimitFakeProvider({TimeFrame.M15: candles})

    run = _run_internal_structure(provider, "SOLUSDT", TimeFrame.M15, 1200, False)

    assert not any(
        e.event is StructureEvent.CHOCH_FAILED
        and e.timestamp > datetime(2026, 7, 16, tzinfo=UTC)
        for e in run.events
    )
    assert run.trend is MarketDirection.BEARISH


def _load_range_lock_candles(name: str, symbol: str) -> list[Candle]:
    """BTC/ETH 1h 2026-05-13..07-14, the full 1500-candle production slice.

    Real-data regressions for consolidation (lateral range) detection: the
    July 2026 H1 locks where the structure detector went correctly silent for
    ~10 days (both references pinned outside the box: the BOS staircase at a
    pre-range wick above, the CHoCH reference at the leg origin below) and
    the chart read as stuck. 5-column rows: ts-ms/open/high/low/close.
    """
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent / "liquidity" / "detectors" / "data" / name
    )
    with data_path.open() as f:
        rows = json.load(f)
    return [
        Candle(
            symbol=symbol,
            timeframe=TimeFrame.H1,
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for row in rows
    ]


def test_btc_1h_july_range_lock_is_a_live_consolidation() -> None:
    candles = _load_range_lock_candles("btcusdt_1h_2026_05_13_07_14.json", "BTCUSDT")
    provider = _FuturesLimitFakeProvider({TimeFrame.H1: candles})

    run = _run_internal_structure(provider, "BTCUSDT", TimeFrame.H1, 1200, False)

    live = [r for r in run.consolidation_ranges if r.status is ConsolidationStatus.ACTIVE]
    assert len(live) == 1
    r = live[0]
    # One box for the whole lock -- from just after the 07-06 21:00 BOS (the
    # last surviving advance of the July rally, a close-confirmed step to
    # 64691.9 above the 63990.7 prior BOS top) to the series end -- not split at
    # the detector's dropped (wick-only) 07-10 advance, since segment boundaries
    # are the *surviving* chart events.
    assert r.start_timestamp == datetime(2026, 7, 6, 22, tzinfo=UTC)
    assert r.price_low == pytest.approx(61520.0)
    assert r.price_high == pytest.approx(64680.0)
    # The June bottom basing resolved bullish into the July rally.
    assert any(
        rng.status is ConsolidationStatus.RESOLVED
        and rng.resolved_direction is MarketDirection.BULLISH
        and rng.start_timestamp == datetime(2026, 6, 25, 14, tzinfo=UTC)
        and rng.end_timestamp == datetime(2026, 7, 2, 9, tzinfo=UTC)
        for rng in run.consolidation_ranges
    )
    # ... and stages nothing: the real 07-02 09:00 bullish CHoCH caught the
    # same break (breakout staging dedups against it), so exactly one event
    # marks that candle.
    breakout_marks = [
        e for e in run.events if e.timestamp == datetime(2026, 7, 2, 9, tzinfo=UTC)
    ]
    assert len(breakout_marks) == 1
    assert breakout_marks[0].event is StructureEvent.CHANGE_OF_CHARACTER


def test_sol_4h_range_breakouts_stage_additive_events() -> None:
    """Real-data regression for phase-2 breakout staging (SOLUSDT 4h).

    With the per-timeframe absolute height cap (2026-07-19) the Feb-May
    16%-tall boxes re-cut into three <= 14% ranges. The March box resolves
    bearish at a boundary break the state machine never marked (no real
    same-direction BOS/CHoCH within the dedup window), staging a continuation
    BOS at the defended floor; the April box's bullish resolution against the
    standing bearish trend stages a provisional ``CHoCH?`` that a later real
    advance supersedes, so ``_drop_superseded_provisional_choch`` removes it
    -- a provisional mark survives only at the live edge, never once real
    structure has settled its fate. The staged marks never touch the trend.
    """
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "solusdt_4h_2025_11_06_2026_07_14.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    candles = [
        Candle(
            symbol="SOLUSDT",
            timeframe=TimeFrame.H4,
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for row in rows
    ]
    provider = _FuturesLimitFakeProvider({TimeFrame.H4: candles})

    run = _run_internal_structure(provider, "SOLUSDT", TimeFrame.H4, 1200, False)

    # The staged continuation BOS at the defended floor survives (non-provisional).
    assert any(
        e.timestamp == datetime(2026, 3, 27, 8, tzinfo=UTC)
        and e.event is StructureEvent.BREAK_OF_STRUCTURE
        and not e.provisional
        for e in run.events
    ), "missing staged continuation BOS at 2026-03-27 08:00"
    # Staged reversal CHoCH? superseded by a later real advance are removed by
    # the drop pass -- no stale `CHoCH?` lingers in history.
    superseded_reversals = [
        datetime(2026, 4, 16, 16, tzinfo=UTC),
        datetime(2026, 5, 8, 16, tzinfo=UTC),
    ]
    for timestamp in superseded_reversals:
        assert not any(
            e.timestamp == timestamp
            and e.event is StructureEvent.CHANGE_OF_CHARACTER
            and e.provisional
            for e in run.events
        ), f"superseded staged CHoCH? at {timestamp} should have been dropped"
    # The staged reversal marks never touch the state-machine trend.
    assert run.trend is MarketDirection.BULLISH


def test_ethusdt_5m_range_breakout_below_live_bos_is_not_staged() -> None:
    """A range-breakout BOS must not plot beneath a still-standing real BOS.

    ETHUSDT 5m, 2026-07-20: a bullish BOS at 17:50 tops the leg at 1917.92, then
    price consolidates 18:20 -> 00:35 in a box capped at 1912.05 (a lower high,
    ~1.9 ATR under the swing high) and breaks the box top upward at 00:35 -- but
    without ever clearing 1917.92, whose real BOS line is still live. Staging a
    continuation BOS at 1912.05 there would draw a weaker mark beneath the real
    staircase; the redundant-BOS ATR gate suppresses it. The genuine
    continuation only emits at 04:00 (1931.00), above the prior top. The
    consolidation box itself is untouched.
    """
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "ethusdt_5m_2026_07_21_range_breakout.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    candles = [
        Candle(
            symbol="ETHUSDT",
            timeframe=TimeFrame.M5,
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=row[5],
            taker_buy_volume=row[6],
        )
        for row in rows
    ]
    provider = _FuturesLimitFakeProvider({TimeFrame.M5: candles})

    run = _run_internal_structure(provider, "ETHUSDT", TimeFrame.M5, 1200, True)

    # No phantom continuation BOS at the box top (1912.05) on 2026-07-21 00:35.
    assert not any(
        e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.reference_price_level is not None
        and abs(e.reference_price_level - 1912.05) < 0.5
        for e in run.events
    ), "range-breakout BOS at 1912.05 should be suppressed beneath the live 1917.92 BOS"
    # The real staircase survives: the leg top at 17:50 and the true
    # continuation at 04:00 above it.
    assert any(
        e.timestamp == datetime(2026, 7, 20, 17, 50, tzinfo=UTC)
        and e.event is StructureEvent.BREAK_OF_STRUCTURE
        for e in run.events
    )
    assert any(
        e.timestamp == datetime(2026, 7, 21, 4, tzinfo=UTC)
        and e.event is StructureEvent.BREAK_OF_STRUCTURE
        for e in run.events
    )
    # The genuine continuation that breaks the 1917.92 top is recovered at its
    # first close above it (03:00, referencing 1917.92) -- an impulsive run of
    # two high pivots (1927.96, 1931.00) with no pullback between superseded this
    # pending, and the staged-BOS dedup used to drop it against the later real
    # BOS's nearby pivot despite their distinct staircase floors.
    assert any(
        e.timestamp == datetime(2026, 7, 21, 3, tzinfo=UTC)
        and e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.reference_price_level == pytest.approx(1917.92)
        for e in run.events
    ), "recovered continuation BOS at 1917.92 (03:00) is missing"
    # The consolidation box is still detected (only the phantom BOS was dropped).
    assert any(
        r.end_timestamp == datetime(2026, 7, 21, 0, 35, tzinfo=UTC)
        for r in run.consolidation_ranges
    )


def test_btcusdt_5m_strong_close_bos_passes_confluence() -> None:
    """A momentum breakout that closes at its extreme still emits a BOS.

    BTCUSDT 5m, 2026-07-21 13:45: the continuation that breaks the 66618 top
    closes at 66627 (97% of its 66311-66636 range) after an early dip -- a
    decisive bullish close, but the pure LuxAlgo shadow-balance filter rejects
    it (upper shadow 9.1 < lower shadow 24.8), silently dropping the BOS and
    leaving the staircase to only mark a much higher level. The strong-close
    override (bos_confluence_strong_close_frac) lets a close in the extreme 20%
    of its range pass, so the BOS emits at the level price actually broke.
    """
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "btcusdt_5m_2026_07_21_strong_close_bos.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    candles = [
        Candle(
            symbol="BTCUSDT",
            timeframe=TimeFrame.M5,
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=row[5],
            taker_buy_volume=row[6],
        )
        for row in rows
    ]
    provider = _FuturesLimitFakeProvider({TimeFrame.M5: candles})

    run = _run_internal_structure(provider, "BTCUSDT", TimeFrame.M5, 1200, True)

    assert any(
        e.timestamp == datetime(2026, 7, 21, 13, 45, tzinfo=UTC)
        and e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.reference_price_level == pytest.approx(66618.0)
        for e in run.events
    ), "strong-close continuation BOS at 66618 (13:45) is missing"


def test_solusdt_30m_double_top_bos_anchors_at_original_swing() -> None:
    """A continuation BOS line anchors on the swing that formed the level.

    SOLUSDT 30m: two continuation BOS break levels that were double-topped --
    76.56 (formed 2026-07-19 02:30, retested 07-19 23:30 just before the break)
    and 78.37 (formed 2026-07-20 18:00, retested 07-21 05:00). The line-start
    anchor (``reference_timestamp``) must resolve to the *original* swing top
    within the leg, not the shallow retest immediately before the break, so the
    line runs from the real structural high rather than starting mid-move.
    """
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "solusdt_30m_2026_07_21_double_top_anchor.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    candles = [
        Candle(
            symbol="SOLUSDT",
            timeframe=TimeFrame.M30,
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=row[5],
            taker_buy_volume=row[6],
        )
        for row in rows
    ]
    provider = _FuturesLimitFakeProvider({TimeFrame.M30: candles})

    run = _run_internal_structure(provider, "SOLUSDT", TimeFrame.M30, 1200, True)

    def anchor_for(reference: float) -> datetime | None:
        for e in run.events:
            if (
                e.event is StructureEvent.BREAK_OF_STRUCTURE
                and not e.provisional
                and e.reference_price_level is not None
                and e.reference_price_level == pytest.approx(reference)
            ):
                return e.reference_timestamp
        return None

    assert anchor_for(76.56) == datetime(2026, 7, 19, 2, 30, tzinfo=UTC)
    assert anchor_for(78.37) == datetime(2026, 7, 20, 18, 0, tzinfo=UTC)


def test_zecusdt_1h_provisional_bos_not_backdated_before_floor() -> None:
    """A provisional continuation BOS cannot predate the floor it breaks.

    ZECUSDT 1h: after the 2026-07-19 21:00 bearish CHoCH the staircase floor
    ratchets down to 533.06, a low that only forms on 2026-07-21 10:00. An old
    2026-07-20 04:00 candle happened to close below 533.06 a day before that
    swing existed, so scanning the whole leg for the first close beyond the
    *final* floor back-dated a provisional BOS to 07-20 and left a stale "BOS?"
    for a day while price had actually recovered toward the CHoCH reference.
    The break must fall after the floor's origin, so no provisional BOS emits.
    """
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "zecusdt_1h_2026_07_21_stale_provisional_bos.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    candles = [
        Candle(
            symbol="ZECUSDT",
            timeframe=TimeFrame.H1,
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=row[5],
            taker_buy_volume=row[6],
        )
        for row in rows
    ]
    provider = _FuturesLimitFakeProvider({TimeFrame.H1: candles})

    run = _run_internal_structure(provider, "ZECUSDT", TimeFrame.H1, 1200, True)

    stale = [
        e
        for e in run.events
        if e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.provisional
        and e.reference_price_level == pytest.approx(533.06)
    ]
    assert not stale, "stale back-dated provisional BOS at 533.06 should not emit"


def test_eth_1h_july_range_lock_is_a_live_consolidation() -> None:
    candles = _load_range_lock_candles("ethusdt_1h_2026_05_13_07_14.json", "ETHUSDT")
    provider = _FuturesLimitFakeProvider({TimeFrame.H1: candles})

    run = _run_internal_structure(provider, "ETHUSDT", TimeFrame.H1, 1200, False)

    live = [r for r in run.consolidation_ranges if r.status is ConsolidationStatus.ACTIVE]
    assert len(live) == 1
    r = live[0]
    # The box opens right after the 07-06 21:00 BOS at 1833 ("travou um BOS em
    # cima") and holds 1712.45-1829.52; the 07-12 spike to 1848 stays outside
    # (a boundary sweep, not part of the box).
    assert r.start_timestamp == datetime(2026, 7, 6, 22, tzinfo=UTC)
    assert r.price_low == pytest.approx(1712.45)
    assert r.price_high == pytest.approx(1829.52)


def _load_mu_4h_rearm_candles() -> list[Candle]:
    """MUUSDT 4h 2026-04-07..07-16, the full production slice (a new listing,
    602 candles). The real-data regression for ``choch_failed_rearm``: the
    06-23 bearish CHoCH (ref 1120.6) failed on a genuinely violent reclaim
    (one candle 1050 -> 1215, days at 1120-1255), then the second bearish
    CHoCH (07-02, weak 1026.86) died on a flat drift hugging the level
    (closes 0.1-1.9%% above it) -- and the -19%% collapse that followed read
    as two sweeps (one inside the post-failure fallback suppression with no
    reference at all, one against the dead-cat bounce's 875.67 leg origin)
    while the trend sat bullish for weeks. 5-column rows: ts/open/high/low/
    close."""
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "muusdt_4h_2026_04_07_07_16.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    return [
        Candle(
            symbol="MUUSDT",
            timeframe=TimeFrame.H4,
            timestamp=datetime.fromisoformat(row[0]),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for row in rows
    ]


def test_mu_4h_failed_choch_rearm_refires_and_crash_prints_bos() -> None:
    """Production reading of the MUUSDT 4h June-July window with the re-arm
    wired: the 06-23 bearish CHoCH's failure stands (the reclaim was violent
    and sustained), but once price rolls back over and sustains below the
    broken 1120.6 the CHoCH *re-fires* (07-01) instead of the old reading's
    fresh weak CHoCH at 1026.86 that immediately died on a flat drift -- so
    the July collapse prints a bearish BOS staircase instead of sweeps under
    a stuck-bullish trend."""
    candles = _load_mu_4h_rearm_candles()
    provider = _FuturesLimitFakeProvider({TimeFrame.H4: candles})

    run = _run_internal_structure(provider, "MUUSDT", TimeFrame.H4, 1200, False)

    # The original bearish CHoCH and its genuine failure both stand.
    assert any(
        e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BEARISH
        and e.timestamp == datetime(2026, 6, 23, 12, tzinfo=UTC)
        and e.reference_price_level == pytest.approx(1120.6)
        for e in run.events
    )
    assert any(
        e.event is StructureEvent.CHOCH_FAILED
        and not e.provisional
        and e.direction is MarketDirection.BEARISH
        and e.timestamp == datetime(2026, 6, 24, 20, tzinfo=UTC)
        for e in run.events
    )
    # The re-fired bearish CHoCH at the same broken level, once price
    # sustains back below it (the re-arm in action). Its
    # `reference_timestamp` is the *failure's* timestamp -- the line starts
    # where the failure ended instead of re-drawing the original CHoCH's
    # whole span (and the frontend keys its `↻` re-activation suffix on
    # exactly this match).
    assert any(
        e.event is StructureEvent.CHANGE_OF_CHARACTER
        and not e.provisional
        and e.direction is MarketDirection.BEARISH
        and e.timestamp == datetime(2026, 7, 1, 12, tzinfo=UTC)
        and e.reference_price_level == pytest.approx(1120.6)
        and e.reference_timestamp == datetime(2026, 6, 24, 20, tzinfo=UTC)
        for e in run.events
    )
    # The old reading's false second failure (the 07-02 weak CHoCH at 1026.86
    # dying 07-03 on closes hugging the level) no longer exists...
    assert not any(
        e.event is StructureEvent.CHOCH_FAILED
        and e.direction is MarketDirection.BEARISH
        and datetime(2026, 7, 2, tzinfo=UTC) <= e.timestamp <= datetime(2026, 7, 6, tzinfo=UTC)
        for e in run.events
    )
    # ... and the collapse prints a real bearish continuation BOS.
    assert any(
        e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BEARISH
        and e.timestamp == datetime(2026, 7, 7, tzinfo=UTC)
        and e.reference_price_level == pytest.approx(951.42)
        for e in run.events
    )
    # The +18% bounce off 875 reads as a bullish CHoCH (07-09, at the 962.15
    # leg origin) that fails on 07-13 -- and the level's story ends there: the
    # 07-14 re-fire also died when the crash resumed, so the composition
    # collapse (`_drop_failed_refire_cycles`) removes the re-fire and its
    # failure, leaving a single ✕ instead of the ✕ → ↻ → ✕ stack.
    assert any(
        e.event is StructureEvent.CHOCH_FAILED
        and not e.provisional
        and e.direction is MarketDirection.BULLISH
        and e.timestamp == datetime(2026, 7, 13, tzinfo=UTC)
        and e.reference_price_level == pytest.approx(962.15)
        for e in run.events
    )
    assert not any(
        e.timestamp > datetime(2026, 7, 13, tzinfo=UTC)
        and e.event in (StructureEvent.CHANGE_OF_CHARACTER, StructureEvent.CHOCH_FAILED)
        and e.direction is MarketDirection.BULLISH
        for e in run.events
    )
    # And the standing trend is bearish: the fixture's vertical final drop
    # (875 -> 816 with no swing pivot forming) confirmed the re-fire's failure
    # at the live edge (`choch_fail_live_edge`) -- without it the trend sat
    # bullish 19% above price waiting for a pivot.
    assert run.trend is MarketDirection.BEARISH


def test_mu_4h_live_edge_fail_off_trend_hangs_bullish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live-edge gap lock (`choch_fail_live_edge` off): the re-fired
    bullish CHoCH's fail level is sustained-broken by six closes, but the
    vertical drop never forms the low pivot that would emit the failure, so
    the detector's standing trend hangs bullish 19% above price (only the
    additive fizzle marker shows, and it never flips the trend)."""
    monkeypatch.setattr(dashboard_data, "_CHOCH_FAIL_LIVE_EDGE", False)
    candles = _load_mu_4h_rearm_candles()
    provider = _FuturesLimitFakeProvider({TimeFrame.H4: candles})

    run = _run_internal_structure(provider, "MUUSDT", TimeFrame.H4, 1200, False)

    assert run.trend is MarketDirection.BULLISH


def test_mu_4h_rearm_off_crash_reads_as_sweeps_under_stuck_trend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pathology lock (re-arm off): after the second failure the fallback
    suppression leaves the crash's first break with no reference at all and
    the dead-cat bounce pins the next one at the 875.67 leg origin, so the
    -19%% July collapse prints zero bearish BOS/CHoCH and the standing trend
    stays bullish.

    The buffer that keeps a CHoCH alive through an ordinary retest
    (`_CHOCH_FAIL_LEVEL_BUFFER_ATR`) independently prevents this pathology --
    the false 07-03 failure never fires and the collapse prints its bearish BOS
    -- so it is switched off here: this lock is about what *re-arm* protects
    against.
    """
    monkeypatch.setattr(dashboard_data, "_CHOCH_FAILED_REARM", False)
    monkeypatch.setattr(dashboard_data, "_CHOCH_FAIL_LEVEL_BUFFER_ATR", None)
    candles = _load_mu_4h_rearm_candles()
    provider = _FuturesLimitFakeProvider({TimeFrame.H4: candles})

    run = _run_internal_structure(provider, "MUUSDT", TimeFrame.H4, 1200, False)

    # The false second failure fires...
    assert any(
        e.event is StructureEvent.CHOCH_FAILED
        and e.direction is MarketDirection.BEARISH
        and e.timestamp == datetime(2026, 7, 3, 20, tzinfo=UTC)
        and e.reference_price_level == pytest.approx(1026.86)
        for e in run.events
    )
    # ... and the whole collapse window prints no bearish structure at all
    # (only sweeps), leaving the trend stuck bullish.
    assert not any(
        e.event in (StructureEvent.BREAK_OF_STRUCTURE, StructureEvent.CHANGE_OF_CHARACTER)
        and e.direction is MarketDirection.BEARISH
        and not e.provisional
        and e.timestamp >= datetime(2026, 7, 4, tzinfo=UTC)
        for e in run.events
    )
    assert run.trend is MarketDirection.BULLISH


# --- Leg-launch BOS rescue (_RESCUE_LEG_LAUNCH_BOS) --------------------------
#
# ENAUSDT M30, the structurally anchored production slice. The bearish leg
# (bearish CHoCH 2026-07-11 22:00 -> bullish CHoCH 2026-07-14 04:00) launches
# from the 0.07908 fundo the CHoCH formed. Price retests the CHoCH before
# breaking down, so that launch level's first confirming close (07-13 03:00,
# 0.0787) lands three candles past the *next* continuation's stamp (07-13
# 01:30, ref 0.07954) -- outside the launch BOS's own re-anchor window. Without
# the rescue the launch BOS is dropped as wick-only and the chart promotes the
# shallow 0.07954 fundo (formed 22 hours later) to first-of-leg reference.


def _load_ena_30m_launch_candles() -> list[Candle]:
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "enausdt_30m_2026_06_20_07_15.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    return [
        Candle(
            symbol="ENAUSDT",
            timeframe=TimeFrame.M30,
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for row in rows
    ]


def _ena_bearish_leg_bos(run: dashboard_data.InternalStructureRun) -> list[MarketStructure]:
    # The bearish leg: bearish CHoCH 07-11 22:00 -> bullish CHoCH 07-14 04:00.
    leg_start = datetime(2026, 7, 11, 22, tzinfo=UTC)
    leg_end = datetime(2026, 7, 14, 4, tzinfo=UTC)
    return [
        e
        for e in run.events
        if e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BEARISH
        and not e.provisional
        and leg_start < e.timestamp < leg_end
    ]


def test_run_internal_structure_rescues_ena_30m_leg_launch_bos() -> None:
    provider = _FuturesLimitFakeProvider({TimeFrame.M30: _load_ena_30m_launch_candles()})

    run = _run_internal_structure(provider, "ENAUSDT", TimeFrame.M30, 1200, False)

    # The leg reads CHoCH -> BOS at the launch fundo -> BOS 0.07760 -> CHoCH:
    # the launch BOS references 0.07908 (the CHoCH fundo, formed 07-12 00:30)
    # and is timed at its first close through it, and the shallow 0.07954
    # continuation it passed over is suppressed.
    bos = _ena_bearish_leg_bos(run)
    assert [e.reference_price_level for e in bos] == [
        pytest.approx(0.07908),
        pytest.approx(0.07760),
    ]
    launch = bos[0]
    assert launch.timestamp == datetime(2026, 7, 13, 3, tzinfo=UTC)
    assert launch.reference_timestamp == datetime(2026, 7, 12, 0, 30, tzinfo=UTC)


def test_run_internal_structure_ena_30m_launch_bos_lost_without_rescue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Off-lock: the raw detector emits the launch BOS, but the re-anchor pass
    # drops it and the shallow successor stands in as first-of-leg.
    monkeypatch.setattr(dashboard_data, "_RESCUE_LEG_LAUNCH_BOS", False)
    provider = _FuturesLimitFakeProvider({TimeFrame.M30: _load_ena_30m_launch_candles()})

    run = _run_internal_structure(provider, "ENAUSDT", TimeFrame.M30, 1200, False)

    raw = _build_internal_detector(TimeFrame.M30, confluence_filter=False).detect(
        run.internal_candles
    )
    assert any(
        e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.reference_price_level == pytest.approx(0.07908)
        for e in raw
    )
    assert [e.reference_price_level for e in _ena_bearish_leg_bos(run)] == [
        pytest.approx(0.07954),
        pytest.approx(0.07760),
    ]


# --- First BOS references the leg's true swept fundo (_BOS_FIRST_FLOOR_LEG_EXTREME) -
#
# ETHBTC 1D. Bearish CHoCH confirms 2025-10-20 against the 0.036064 HL (formed
# 08-20). The reversal's real fundo is 0.03214 (10-10, swept earlier as price
# dove and retraced), but the lookback-delayed confirming pivot is only the
# shallow 0.0348 higher-low (10-22). `bos_first_floor_leg_extreme` seeds the
# first BOS of the leg with the deeper `pending_low` (0.03214), so it references
# that swept fundo -- not the shallow confirming pivot -- and confirms on the
# (late) close through it (2026-01-30). Off, the first BOS reports 0.0348.


def _load_ethbtc_1d_candles() -> list[Candle]:
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "ethbtc_1d_2023_2026.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    return [
        Candle(
            symbol="ETHBTC",
            timeframe=TimeFrame.D1,
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=row[5],
            taker_buy_volume=row[6],
        )
        for row in rows
    ]


def test_run_internal_structure_ethbtc_1d_first_bos_at_leg_fundo() -> None:
    provider = _FuturesLimitFakeProvider({TimeFrame.D1: _load_ethbtc_1d_candles()})

    run = _run_internal_structure(provider, "ETHBTC", TimeFrame.D1, 1200, False)

    leg_start = datetime(2025, 10, 20, tzinfo=UTC)
    leg_end = datetime(2026, 2, 15, tzinfo=UTC)
    bos = [
        e
        for e in run.events
        if e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BEARISH
        and not e.provisional
        and leg_start < e.timestamp < leg_end
    ]
    # The first BOS of the leg references the swept fundo (0.03194, the 10-10 low
    # in this spot fixture), not the shallow ~0.0348 confirming pivot, and is
    # timed at its first close through that level (2026-01-31).
    first = bos[0]
    assert first.reference_price_level == pytest.approx(0.03194)
    assert first.reference_timestamp == datetime(2025, 10, 10, tzinfo=UTC)
    assert first.timestamp == datetime(2026, 1, 31, tzinfo=UTC)


def _load_ethusdt_1d_candles() -> list[Candle]:
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "ethusdt_1d_2023_2026.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    return [
        Candle(
            symbol="ETHUSDT",
            timeframe=TimeFrame.D1,
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=row[5],
            taker_buy_volume=row[6],
        )
        for row in rows
    ]


def test_run_internal_structure_ethusdt_1d_first_bos_at_leg_fundo() -> None:
    # Sibling of the ETHBTC 1D case, but the leg's true fundo sits in the
    # trailing `active_low` (the 10-10 sweep to 3400), not `pending_low` (which
    # held the shallower 3822 from 09-25). The first BOS must still reference the
    # 3400 fundo (formed 10-10) rather than the 3671 confirming pivot -- and here
    # price closes through 3400 quickly (11-04), so the leg keeps its later
    # continuations (3055, 2620, ...).
    provider = _FuturesLimitFakeProvider({TimeFrame.D1: _load_ethusdt_1d_candles()})

    run = _run_internal_structure(provider, "ETHUSDT", TimeFrame.D1, 1200, False)

    leg_start = datetime(2025, 10, 15, tzinfo=UTC)
    leg_end = datetime(2025, 12, 1, tzinfo=UTC)
    bos = [
        e
        for e in run.events
        if e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BEARISH
        and not e.provisional
        and leg_start < e.timestamp < leg_end
    ]
    first = bos[0]
    assert first.reference_price_level == pytest.approx(3400.0)
    assert first.reference_timestamp == datetime(2025, 10, 10, tzinfo=UTC)
    assert first.timestamp == datetime(2025, 11, 4, tzinfo=UTC)
    # The continuation survives (close through 3400 came early).
    assert bos[1].reference_price_level == pytest.approx(3055.0)


# --- Superseded-continuation BOS staging (_STAGE_SUPERSEDED_CONTINUATION_BOS) --
#
# NEARUSDT M15. A BOS only emits once a confirming opposite pullback pivot
# forms. In the bullish leg of 2026-07-14 the pivots run
# 07:15 HIGH 2.0120 -> 10:15 HIGH 1.9960 -> 11:00 LOW 1.9670 ->
# 12:30 HIGH 2.0400 -> 15:30 HIGH 2.0660 -> 17:00 LOW 2.0180: no low pivot forms
# between the 12:30 and 15:30 advances, so the 12:30 pending (floor 2.0120, the
# topo that formed and was broken) is silently superseded and only the 15:30
# advance emits -- referencing 2.0400, its line starting at 12:30 instead of the
# 07:15 topo. Staged, the leg reads BOS 2.0120 then BOS 2.0400.


def _load_near_15m_superseded_candles() -> list[Candle]:
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "nearusdt_15m_2026_07_02_07_18.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    return [
        Candle(
            symbol="NEARUSDT",
            timeframe=TimeFrame.M15,
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for row in rows
    ]


def _near_bullish_leg_bos(run: dashboard_data.InternalStructureRun) -> list[MarketStructure]:
    leg_start = datetime(2026, 7, 14, 5, tzinfo=UTC)
    leg_end = datetime(2026, 7, 14, 18, tzinfo=UTC)
    return [
        e
        for e in run.events
        if e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BULLISH
        and not e.provisional
        and leg_start <= e.timestamp <= leg_end
    ]


def test_run_internal_structure_stages_near_15m_superseded_continuation() -> None:
    provider = _FuturesLimitFakeProvider({TimeFrame.M15: _load_near_15m_superseded_candles()})

    run = _run_internal_structure(provider, "NEARUSDT", TimeFrame.M15, 1200, False)

    # The staged mark restores the staircase: the 2.0120 topo (formed 07:15) is
    # referenced by its own BOS before the 2.0400 one.
    bos = _near_bullish_leg_bos(run)
    assert [e.reference_price_level for e in bos] == [
        pytest.approx(1.9760),
        pytest.approx(2.0120),
        pytest.approx(2.0400),
    ]
    staged = bos[1]
    assert staged.timestamp == datetime(2026, 7, 14, 12, 30, tzinfo=UTC)
    assert staged.reference_timestamp == datetime(2026, 7, 14, 7, 15, tzinfo=UTC)


def test_run_internal_structure_near_15m_superseded_lost_without_staging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Off-lock: the leg's only continuation references the later 2.0400 top --
    # the 2.0120 topo that formed and broke has no mark at all.
    monkeypatch.setattr(dashboard_data, "_STAGE_SUPERSEDED_CONTINUATION_BOS", False)
    provider = _FuturesLimitFakeProvider({TimeFrame.M15: _load_near_15m_superseded_candles()})

    run = _run_internal_structure(provider, "NEARUSDT", TimeFrame.M15, 1200, False)

    assert [e.reference_price_level for e in _near_bullish_leg_bos(run)] == [
        pytest.approx(1.9760),
        pytest.approx(2.0400),
    ]


def _event_at_candle(
    candle: Candle,
    event: StructureEvent,
    *,
    direction: MarketDirection = MarketDirection.BEARISH,
    provisional: bool = False,
) -> MarketStructure:
    return MarketStructure(
        symbol=candle.symbol,
        timeframe=candle.timeframe,
        timestamp=candle.timestamp,
        event=event,
        direction=direction,
        price_level=candle.close,
        scope=StructureScope.INTERNAL,
        provisional=provisional,
    )


def test_build_anchored_vwaps_anchors_at_the_flip_and_the_last_sweep() -> None:
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")
    events = [
        _event_at_candle(candles[2], StructureEvent.CHANGE_OF_CHARACTER),
        _event_at_candle(candles[5], StructureEvent.LIQUIDITY_SWEEP),
    ]

    series = _build_anchored_vwaps(
        candles, events, symbol="BTCUSDT", timeframe=TimeFrame.H1
    )

    assert [s.label for s in series] == ["CHoCH ▼", "Sweep ▼"]
    assert [s.anchor_timestamp for s in series] == [
        candles[2].timestamp,
        candles[5].timestamp,
    ]


def test_build_anchored_vwaps_skips_provisional_and_live_edge_anchors() -> None:
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")
    events = [
        # A live-edge mark: its anchor can vanish on the next refresh.
        _event_at_candle(
            candles[3], StructureEvent.CHANGE_OF_CHARACTER, provisional=True
        ),
        # Confirmed, but only two candles of accumulation behind it.
        _event_at_candle(candles[-2], StructureEvent.LIQUIDITY_SWEEP),
    ]

    assert _build_anchored_vwaps(
        candles, events, symbol="BTCUSDT", timeframe=TimeFrame.H1
    ) == []


def test_load_dashboard_data_populates_session_vwap() -> None:
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")

    data = load_dashboard_data(
        provider=_FakeProvider(candles), symbol="BTCUSDT", futures_provider=_FAKE_FUTURES
    )

    assert data.vwap is not None
    assert data.vwap.anchor is VWAPAnchor.SESSION
    assert data.vwap.points[-1].timestamp == candles[-1].timestamp


def test_load_dashboard_data_vwap_anchor_period_coarsens_on_high_timeframes() -> None:
    # A UTC day holds one D1 candle, so a day-anchored average there would
    # report the candle itself; the month is the finest period that accumulates.
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")

    data = load_dashboard_data(
        provider=_FakeProvider(candles),
        symbol="BTCUSDT",
        timeframe=TimeFrame.D1,
        futures_provider=_FAKE_FUTURES,
    )

    assert data.vwap is not None
    assert data.vwap.anchor is VWAPAnchor.MONTH
    assert data.vwap.label == "Monthly"


def test_ethusdt_15m_failed_choch_refires_at_live_edge() -> None:
    """A re-armed CHoCH level must speak at the live edge, not wait for a pivot.

    ETHUSDT 15m, 2026-07-27: a bearish CHoCH confirms at 14:30 against the
    structural 1935.29 (the higher low formed 01:30) and dives to 1917.90.
    Price then reclaims the level and holds above it for six hours -- a real
    reclaim by the calibrated pending-fail bar -- so a CHOCH_FAILED fires at
    16:30 and arms 1935.29 as a re-arm reference.

    From 22:15 price rolls back over and closes below 1935.29 for eleven
    consecutive candles (-3.5%, down to 1865). That is exactly what the re-arm
    exists for: the reclaim was the old trend's last gasp. But every candle of
    the drop makes a new low, so no swing pivot can form to run the pivot-gated
    confirmed CHoCH check -- and the provisional live-edge path used to consult
    only `validated_choch_low`, which the original CHoCH had reset to `None`.
    The chart went blank through the entire move.

    With the provisional CHoCH resolving its reference the way the confirmed
    check does (validated -> pending leg origin -> blind-spot origin -> re-arm),
    the re-fire shows as a dimmed `CHoCH? ↻ ▼` at the first sustained close
    below the level. The state-machine trend stays untouched (provisional marks
    are additive), and the mark carries the *failure's* timestamp as its
    reference so the frontend anchors its line at the `✕`.
    """
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "ethusdt_15m_2026_07_27_choch_refire.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    candles = [
        Candle(
            symbol="ETHUSDT",
            timeframe=TimeFrame.M15,
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=row[5],
            taker_buy_volume=row[6],
        )
        for row in rows
    ]
    provider = _FuturesLimitFakeProvider({TimeFrame.M15: candles})

    run = _run_internal_structure(provider, "ETHUSDT", TimeFrame.M15, 1200, True)

    # The original bearish CHoCH and its failure are unchanged.
    choch = next(
        e
        for e in run.events
        if e.timestamp == datetime(2026, 7, 27, 14, 30, tzinfo=UTC)
        and e.event is StructureEvent.CHANGE_OF_CHARACTER
    )
    assert choch.direction is MarketDirection.BEARISH
    assert choch.reference_price_level == pytest.approx(1935.29)
    assert not choch.provisional

    failure = next(
        e
        for e in run.events
        if e.timestamp == datetime(2026, 7, 27, 16, 30, tzinfo=UTC)
        and e.event is StructureEvent.CHOCH_FAILED
    )
    assert failure.direction is MarketDirection.BEARISH
    assert not failure.provisional

    # The re-fire: a provisional bearish CHoCH at the first sustained close back
    # below the re-armed level, referencing it and anchored at the failure.
    refire = next(
        (
            e
            for e in run.events
            if e.event is StructureEvent.CHANGE_OF_CHARACTER
            and e.provisional
            and e.direction is MarketDirection.BEARISH
            and e.timestamp > failure.timestamp
        ),
        None,
    )
    assert refire is not None, "re-armed CHoCH never re-fired at the live edge"
    assert refire.timestamp == datetime(2026, 7, 27, 22, 15, tzinfo=UTC)
    assert refire.reference_price_level == pytest.approx(1935.29)
    # The re-arm pivot carries the failure's timestamp, so the frontend renders
    # `CHoCH? ↻ ▼` and starts its line at the `✕`.
    assert refire.reference_timestamp == failure.timestamp
    assert refire.reference_structural is True

    # Additive only: the provisional mark never flips the state machine.
    assert run.trend is MarketDirection.BULLISH


def _load_eth_15m_refire_candles() -> list[Candle]:
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "ethusdt_15m_2026_07_27_refire.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    return [
        Candle(
            symbol="ETHUSDT",
            timeframe=TimeFrame.M15,
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for row in rows
    ]


def _eth_refire_intermediate_bos(
    run: dashboard_data.InternalStructureRun,
) -> list[MarketStructure]:
    leg_start = datetime(2026, 7, 27, 16, tzinfo=UTC)
    return [
        e
        for e in run.events
        if e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BEARISH
        and not e.provisional
        and e.timestamp >= leg_start
    ]


def test_run_internal_structure_stages_eth_15m_refire_intermediate_bos() -> None:
    """The re-fired CHoCH's leg gets the staircase step it closed through.

    ETHUSDT M15 2026-07-27: `CHoCH ▼` at 1917.9 (14:30) failed at 16:15, the
    level re-armed, and the resumed drop re-fired it at 22:15 with a confirming
    fundo of 1865.0 -- closing straight through the 1917.9 fundo on the way,
    which the state machine reads as part of the one reversal and never marks.
    """
    provider = _FuturesLimitFakeProvider({TimeFrame.M15: _load_eth_15m_refire_candles()})

    run = _run_internal_structure(provider, "ETHUSDT", TimeFrame.M15, 400, True)

    bos = _eth_refire_intermediate_bos(run)
    assert [e.reference_price_level for e in bos] == [pytest.approx(1917.9)]
    # Anchored at the close through the level, and its line starts at the ✕.
    assert bos[0].reference_timestamp == datetime(2026, 7, 27, 16, 30, tzinfo=UTC)
    assert bos[0].timestamp > datetime(2026, 7, 27, 16, 15, tzinfo=UTC)


def test_run_internal_structure_eth_15m_refire_bos_absent_without_staging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dashboard_data, "_STAGE_REFIRE_INTERMEDIATE_BOS", False)
    provider = _FuturesLimitFakeProvider({TimeFrame.M15: _load_eth_15m_refire_candles()})

    run = _run_internal_structure(provider, "ETHUSDT", TimeFrame.M15, 400, True)

    assert _eth_refire_intermediate_bos(run) == []


# --- Additive-staging invariance ---------------------------------------------
#
# A BOS stager is meant to *add a mark*. It must not move a CHoCH, a
# CHOCH_FAILED, a sweep or a pivot label -- but nothing in the pipeline enforces
# that, and the coupling is easy to miss: the composition passes read the BOS set
# to decide the fate of *other* events (`_drop_failed_refire_cycles`'s
# `refire_worked` guard, the leg-launch rescue's window bound). A stager wired in
# the wrong place therefore rewrites settled structure while every BOS-counting
# measurement looks clean -- exactly how `_stage_refire_intermediate_bos` was
# first built, silently growing four CHoCH/✕ pairs on NEARUSDT M15.
#
# These tests pin the contract per flag on real fixtures.

_ADDITIVE_STAGING_FLAGS = [
    "_STAGE_REFIRE_INTERMEDIATE_BOS",
    "_STAGE_SUPERSEDED_CONTINUATION_BOS",
]


def _non_bos_signature(run: dashboard_data.InternalStructureRun) -> list[tuple]:
    """Every field the chart draws, for every event that is not a BOS."""
    return [
        (
            e.timestamp,
            e.event,
            e.direction,
            e.price_level,
            e.reference_price_level,
            e.reference_timestamp,
            e.reference_structural,
            e.provisional,
        )
        for e in run.events
        if e.event is not StructureEvent.BREAK_OF_STRUCTURE
    ]


def _bos_count(run: dashboard_data.InternalStructureRun) -> int:
    return sum(1 for e in run.events if e.event is StructureEvent.BREAK_OF_STRUCTURE)


@pytest.mark.parametrize("flag", _ADDITIVE_STAGING_FLAGS)
@pytest.mark.parametrize(
    ("symbol", "fixture"),
    [
        ("ETHUSDT", "eth_refire"),
        ("NEARUSDT", "near_superseded"),
    ],
)
def test_additive_bos_stagers_leave_non_bos_structure_untouched(
    monkeypatch: pytest.MonkeyPatch, flag: str, symbol: str, fixture: str
) -> None:
    candles = (
        _load_eth_15m_refire_candles()
        if fixture == "eth_refire"
        else _load_near_15m_superseded_candles()
    )
    provider = _FuturesLimitFakeProvider({TimeFrame.M15: candles})

    on = _run_internal_structure(provider, symbol, TimeFrame.M15, 400, True)
    monkeypatch.setattr(dashboard_data, flag, False)
    off = _run_internal_structure(provider, symbol, TimeFrame.M15, 400, True)

    # Non-vacuous: the flag really does add a mark on this fixture.
    assert _bos_count(on) == _bos_count(off) + 1
    # ...and that is the *only* thing it does.
    assert _non_bos_signature(on) == _non_bos_signature(off)


def _load_ena_1h_refire_worked_candles() -> list[Candle]:
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "enausdt_1h_2026_07_12_refire_worked.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    return [
        Candle(
            symbol="ENAUSDT",
            timeframe=TimeFrame.H1,
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for row in rows
    ]


def _ena_refire_cycle_events(events: list[MarketStructure]) -> list[StructureEvent]:
    window_start = datetime(2026, 7, 12, tzinfo=UTC)
    window_end = datetime(2026, 7, 21, tzinfo=UTC)
    return [
        e.event
        for e in events
        if e.direction is MarketDirection.BEARISH
        and not e.provisional
        and e.event
        in (StructureEvent.CHANGE_OF_CHARACTER, StructureEvent.CHOCH_FAILED)
        and window_start <= e.timestamp <= window_end
    ]


def test_refire_worked_guard_counts_staged_bos_by_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`stage_reversal_eaten_bos` is the documented exception to the rule above.

    Its staged mark satisfies `_drop_failed_refire_cycles`'s `refire_worked`
    guard, so it *does* decide whether a `✕ → ↻ → ✕` cycle survives — the
    ENAUSDT H1 2026-07 lock. This pins that as a wired decision
    (`_REFIRE_WORKED_COUNTS_STAGED_BOS`) rather than an accident of which
    stagers happen to be on: restrict the guard to machine-emitted BOS and the
    same fixture's cycles collapse.

    `_CHOCH_FAIL_LEVEL_BUFFER_ATR` is off here: with it on, the fixture's
    reclaims no longer clear the level and the `X -> refire -> X` cycle this
    pins never forms in the first place.
    """
    monkeypatch.setattr(dashboard_data, "_CHOCH_FAIL_LEVEL_BUFFER_ATR", None)
    provider = _FuturesLimitFakeProvider({TimeFrame.H1: _load_ena_1h_refire_worked_candles()})

    def run() -> list[MarketStructure]:
        return _run_internal_structure(provider, "ENAUSDT", TimeFrame.H1, 400, True).events

    assert dashboard_data._REFIRE_WORKED_COUNTS_STAGED_BOS is True
    counted = _ena_refire_cycle_events(run())

    monkeypatch.setattr(dashboard_data, "_REFIRE_WORKED_COUNTS_STAGED_BOS", False)
    machine_only = _ena_refire_cycle_events(run())

    assert len(counted) > len(machine_only)


def _load_btc_15m_prov_continuation_candles() -> list[Candle]:
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "btcusdt_15m_2026_07_28_prov_continuation.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    return [
        Candle(
            symbol="BTCUSDT",
            timeframe=TimeFrame.M15,
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for row in rows
    ]


def _btc_live_edge_provisional_bos(
    run: dashboard_data.InternalStructureRun,
) -> list[MarketStructure]:
    return [
        e
        for e in run.events
        if e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.provisional
        and e.timestamp >= datetime(2026, 7, 28, tzinfo=UTC)
    ]


def test_run_internal_structure_marks_btc_15m_forming_continuation() -> None:
    """A leg that keeps making lows is marked while its pivot is still forming.

    BTCUSDT M15 2026-07-28: the 63021.0 fundo of 01:00 closed-broke at 13:30,
    but the confirming pullback pivot needs candles the series does not have
    yet. Between the two older provisional routes this fell in a hole -- the
    tail scan only fires *before* the state advance, the first-BOS route only on
    a leg's opening break -- so the chart's last mark stayed the 22:30 BOS,
    fifteen hours and -2% behind price.
    """
    provider = _FuturesLimitFakeProvider({TimeFrame.M15: _load_btc_15m_prov_continuation_candles()})

    run = _run_internal_structure(provider, "BTCUSDT", TimeFrame.M15, 400, True)

    forming = _btc_live_edge_provisional_bos(run)
    assert [e.reference_price_level for e in forming] == [pytest.approx(63021.0)]
    assert forming[0].direction is MarketDirection.BEARISH
    assert forming[0].timestamp == datetime(2026, 7, 28, 13, 30, tzinfo=UTC)


def test_run_internal_structure_btc_15m_continuation_unmarked_without_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dashboard_data, "_EMIT_PROVISIONAL_CONTINUATION_BOS", False)
    provider = _FuturesLimitFakeProvider({TimeFrame.M15: _load_btc_15m_prov_continuation_candles()})

    run = _run_internal_structure(provider, "BTCUSDT", TimeFrame.M15, 400, True)

    assert _btc_live_edge_provisional_bos(run) == []


def _load_btc_15m_keep_under_reversal_candles() -> list[Candle]:
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "btcusdt_15m_2026_07_28_keep_under_reversal.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    return [
        Candle(
            symbol="BTCUSDT",
            timeframe=TimeFrame.M15,
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for row in rows
    ]


def _btc_keep_under_reversal_marks(
    run: dashboard_data.InternalStructureRun,
) -> tuple[list[MarketStructure], list[MarketStructure]]:
    cutoff = datetime(2026, 7, 28, tzinfo=UTC)
    live = [e for e in run.events if e.provisional and e.timestamp >= cutoff]
    return (
        [e for e in live if e.event is StructureEvent.BREAK_OF_STRUCTURE],
        [e for e in live if e.event is StructureEvent.CHANGE_OF_CHARACTER],
    )


def test_run_internal_structure_keeps_btc_15m_bos_under_forming_reversal() -> None:
    """A forming reversal no longer erases the continuation it reversed.

    BTCUSDT M15 2026-07-28: the leg closed through its 63021.0 floor at 13:30
    (the `BOS?`) and only then turned up into a forming bullish CHoCH at 15:15.
    That order is the shape of the ordinary reversal -- the fundo is confirmed
    by a close before price leaves it -- so both marks belong on the chart.
    """
    candles = _load_btc_15m_keep_under_reversal_candles()
    provider = _FuturesLimitFakeProvider({TimeFrame.M15: candles})

    run = _run_internal_structure(provider, "BTCUSDT", TimeFrame.M15, 400, True)

    bos, choch = _btc_keep_under_reversal_marks(run)
    assert [e.reference_price_level for e in bos] == [pytest.approx(63021.0)]
    assert bos[0].direction is MarketDirection.BEARISH
    assert bos[0].timestamp == datetime(2026, 7, 28, 13, 30, tzinfo=UTC)
    # The reversal it sits under is still emitted, and still forming.
    assert [e.direction for e in choch] == [MarketDirection.BULLISH]
    assert choch[0].timestamp > bos[0].timestamp


def test_run_internal_structure_btc_15m_bos_erased_by_reversal_without_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dashboard_data, "_KEEP_PROVISIONAL_BOS_UNDER_REVERSAL", False)
    candles = _load_btc_15m_keep_under_reversal_candles()
    provider = _FuturesLimitFakeProvider({TimeFrame.M15: candles})

    run = _run_internal_structure(provider, "BTCUSDT", TimeFrame.M15, 400, True)

    bos, choch = _btc_keep_under_reversal_marks(run)
    assert bos == []
    assert [e.direction for e in choch] == [MarketDirection.BULLISH]


def _load_ethbtc_4h_dup_prov_bos_candles() -> list[Candle]:
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "ethbtc_4h_2026_07_26_dup_prov_bos.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    return [
        Candle(
            symbol="ETHBTC",
            timeframe=TimeFrame.H4,
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for row in rows
    ]


def _ethbtc_july_bos(run: dashboard_data.InternalStructureRun) -> list[MarketStructure]:
    return [
        e
        for e in run.events
        if e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.timestamp >= datetime(2026, 7, 26, tzinfo=UTC)
    ]


def test_run_internal_structure_drops_ethbtc_4h_duplicated_provisional_bos() -> None:
    """A `BOS?` is dropped when a real BOS already marks the same floor.

    ETHBTC H4 2026-07-26: the July range's breakout staged a real bullish BOS at
    the 0.029793 boundary -- from outside the detector, so its own dedup could
    not see it -- while the live-edge route marked the same boundary. The chart
    drew `BOS` and `BOS?` stacked on one line.
    """
    candles = _load_ethbtc_4h_dup_prov_bos_candles()
    provider = _FuturesLimitFakeProvider({TimeFrame.H4: candles})

    run = _run_internal_structure(provider, "ETHBTC", TimeFrame.H4, 400, True)

    july = _ethbtc_july_bos(run)
    assert [e.provisional for e in july] == [False]
    assert july[0].reference_price_level == pytest.approx(0.029793)


def test_run_internal_structure_ethbtc_4h_duplicate_kept_without_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dashboard_data, "_drop_duplicated_provisional_bos", lambda events: events
    )
    candles = _load_ethbtc_4h_dup_prov_bos_candles()
    provider = _FuturesLimitFakeProvider({TimeFrame.H4: candles})

    run = _run_internal_structure(provider, "ETHBTC", TimeFrame.H4, 400, True)

    july = _ethbtc_july_bos(run)
    # Both marks on the same floor -- the stacked pair the pass removes.
    assert sorted(e.provisional for e in july) == [False, True]
    assert {round(e.reference_price_level or 0, 6) for e in july} == {0.029793}


# --- CHoCH-failure noise band (_CHOCH_FAIL_LEVEL_BUFFER_ATR) -----------------
#
# BTCUSDT M15, the structurally anchored production slice. The bullish CHoCH of
# 2026-07-25 18:00 breaks the 64305.8 lower high formed 07-24 15:30 and holds
# above it for ten candles. Four closes then dip a maximum of 0.37 mean-TR
# (0.08%) below the level -- an ordinary retest of the counter-zone -- and the
# bare-level failure check killed the reversal at base persistence. The same
# level re-fired an hour later and the leg ran to 65555 (+1.9%), so the chart
# showed a `CHoCH ✕ ▲` followed immediately by a `CHoCH ↻ ▲` at one price.


def _load_btc_15m_fail_buffer_candles() -> list[Candle]:
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "btcusdt_15m_2026_07_25_choch_fail_buffer.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    return [
        Candle(
            symbol="BTCUSDT",
            timeframe=TimeFrame.M15,
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=row[5],
            taker_buy_volume=row[6],
        )
        for row in rows
    ]


def _btc_fail_buffer_choch_events(
    run: dashboard_data.InternalStructureRun,
) -> list[MarketStructure]:
    return [
        e
        for e in run.events
        if e.direction is MarketDirection.BULLISH
        and not e.provisional
        and e.event
        in (StructureEvent.CHANGE_OF_CHARACTER, StructureEvent.CHOCH_FAILED)
        and e.timestamp >= datetime(2026, 7, 25, 17, tzinfo=UTC)
        and e.reference_price_level == pytest.approx(64305.8)
    ]


def test_btc_15m_shallow_retest_does_not_negate_choch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 0.37-ATR dip back through the level is a retest, not an invalidation."""
    provider = _FuturesLimitFakeProvider({TimeFrame.M15: _load_btc_15m_fail_buffer_candles()})

    def run() -> dashboard_data.InternalStructureRun:
        return _run_internal_structure(provider, "BTCUSDT", TimeFrame.M15, 1200, False)

    # The pathology, with the noise band off: fire, die, re-fire at one level.
    monkeypatch.setattr(dashboard_data, "_CHOCH_FAIL_LEVEL_BUFFER_ATR", None)
    assert [
        (e.event, e.timestamp) for e in _btc_fail_buffer_choch_events(run())
    ] == [
        (StructureEvent.CHANGE_OF_CHARACTER, datetime(2026, 7, 25, 18, tzinfo=UTC)),
        (StructureEvent.CHOCH_FAILED, datetime(2026, 7, 25, 20, 30, tzinfo=UTC)),
        (StructureEvent.CHANGE_OF_CHARACTER, datetime(2026, 7, 25, 21, 30, tzinfo=UTC)),
    ]

    # Wired: the reclaim never clears the level by the band, so the single
    # CHoCH stands and its leg prints the staircase instead of a ✕.
    monkeypatch.undo()
    assert [
        (e.event, e.timestamp) for e in _btc_fail_buffer_choch_events(run())
    ] == [(StructureEvent.CHANGE_OF_CHARACTER, datetime(2026, 7, 25, 18, tzinfo=UTC))]
    assert any(
        e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BULLISH
        and not e.provisional
        and e.timestamp > datetime(2026, 7, 25, 21, tzinfo=UTC)
        for e in run().events
    )


def _load_sol_4h_choch_window_candles() -> list[Candle]:
    """SOLUSDT H4, the anchored detection slice through 2026-08-13.

    The 08-08 bullish CHoCH (75.28) and its confirming 08-09 BOS, followed by
    the 08-11 pullback that used to print two more CHoCHs in consecutive
    candles. 5-column rows: ts_ms/open/high/low/close (volume is irrelevant to
    structure detection).
    """
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "solusdt_4h_2026_08_11_choch_window.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    return [
        Candle(
            symbol="SOLUSDT",
            timeframe=TimeFrame.H4,
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for row in rows
    ]


def test_choch_scan_window_starts_after_the_reference_level_formed() -> None:
    """A CHoCH cannot be confirmed by closes that predate its own reference.

    SOLUSDT H4 2026-08-11: a bearish CHoCH against a *confirmed* bullish trend
    (barrier persistence 4) met that persistence on the 08-06/08-07 sell-off --
    closes from before the 75.33 level existed -- while having a single
    qualifying close of its own (08-11 12:00; the next candle closed back
    above). Its state reset then promoted the stale 74.82 high of 08-05, a
    level price had left behind three days earlier, as the opposite reference,
    and the trend flipped back one candle later: three CHoCHs in four candles,
    two of them artifacts of pre-flip evidence.
    """
    candles = _load_sol_4h_choch_window_candles()
    detector = _build_internal_detector(TimeFrame.H4, confluence_filter=False)

    events = detector.detect(candles)

    # The 08-08 reversal and its confirming BOS stand.
    assert any(
        e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BULLISH
        and not e.provisional
        and e.timestamp == datetime(2026, 8, 8, 8, tzinfo=UTC)
        for e in events
    )
    # The whipsaw pair does not.
    assert [
        e
        for e in events
        if e.event is StructureEvent.CHANGE_OF_CHARACTER
        and not e.provisional
        and datetime(2026, 8, 10, tzinfo=UTC)
        <= e.timestamp
        <= datetime(2026, 8, 12, tzinfo=UTC)
    ] == []
    assert detector.final_trend is MarketDirection.BULLISH


# --- Provisional-CHoCH staleness + noise guards (SOLUSDT H4, 2026-08) --------
#
# The chart showed a dimmed `CHoCH? v` at 75.33 broken on 2026-08-15 still
# drawn on 08-19 -- 25 candles and +6% later, sitting under the bullish leg's
# own `BOS?` at a new high, against a standing bullish `CHoCH` from 08-08. Two
# independent causes, one fix each:
#   * the emission scans the tail for the *first* sustained close-break and
#     never re-checks it, so a reclaimed forming reversal survives forever
#     (`_PROVISIONAL_CHOCH_REQUIRE_LIVE`);
#   * at the production `persistence_candles = 2` the break was two closes
#     0.17% under a bare reference, reclaimed on the very next candle
#     (`_PROVISIONAL_CHOCH_BREAK_BUFFER_ATR`).


def _load_sol_4h_prov_candles() -> list[Candle]:
    import json
    from pathlib import Path

    data_path = (
        Path(__file__).parent.parent
        / "liquidity"
        / "detectors"
        / "data"
        / "solusdt_4h_2026_07_20_08_19.json"
    )
    with data_path.open() as f:
        rows = json.load(f)
    return [
        Candle(
            symbol="SOLUSDT",
            timeframe=TimeFrame.H4,
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for row in rows
    ]


def _sol_4h_provisional(
    monkeypatch: pytest.MonkeyPatch, *, live: bool, buffer_atr: float | None
) -> list[MarketStructure]:
    monkeypatch.setattr(dashboard_data, "_PROVISIONAL_CHOCH_REQUIRE_LIVE", live)
    monkeypatch.setattr(
        dashboard_data, "_PROVISIONAL_CHOCH_BREAK_BUFFER_ATR", buffer_atr
    )
    events = _build_internal_detector(TimeFrame.H4, confluence_filter=False).detect(
        _load_sol_4h_prov_candles()
    )
    return [e for e in events if e.provisional]


def test_sol_4h_stale_provisional_choch_without_the_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reported bug, reproduced: with both guards off the reclaimed bearish
    break of 2026-08-15 is still emitted at the live edge, alongside the bullish
    `BOS?` of the leg that walked +6% away from it."""
    provisional = _sol_4h_provisional(monkeypatch, live=False, buffer_atr=None)
    stale = [
        e for e in provisional if e.event is StructureEvent.CHANGE_OF_CHARACTER
    ]
    assert len(stale) == 1
    assert stale[0].direction is MarketDirection.BEARISH
    assert stale[0].timestamp == datetime(2026, 8, 15, 4, tzinfo=UTC)
    assert stale[0].reference_price_level == 75.33
    # The leg it supposedly reversed kept going, and says so.
    assert any(e.event is StructureEvent.BREAK_OF_STRUCTURE for e in provisional)


@pytest.mark.parametrize(
    ("live", "buffer_atr"),
    [(True, None), (False, 0.5), (True, 0.5)],
)
def test_sol_4h_guards_retire_the_stale_provisional_choch(
    monkeypatch: pytest.MonkeyPatch, live: bool, buffer_atr: float | None
) -> None:
    """Either guard alone retires it (they attack independent causes of the same
    mark), and together they do too. The leg's own provisional BOS -- a live-edge
    mark that IS still standing -- is untouched, so this is a retirement of the
    stale mark, not of the provisional layer."""
    provisional = _sol_4h_provisional(monkeypatch, live=live, buffer_atr=buffer_atr)
    assert [e.event for e in provisional] == [StructureEvent.BREAK_OF_STRUCTURE]
    assert provisional[0].direction is MarketDirection.BULLISH


def test_provisional_choch_guards_require_the_provisional_choch_flag() -> None:
    with pytest.raises(
        ValueError, match="provisional_choch_require_live requires emit_provisional_choch"
    ):
        InternalStructureDetector(provisional_choch_require_live=True)
    with pytest.raises(
        ValueError,
        match="provisional_choch_break_buffer_atr requires emit_provisional_choch",
    ):
        InternalStructureDetector(provisional_choch_break_buffer_atr=0.5)
    with pytest.raises(
        ValueError, match="provisional_choch_break_buffer_atr must be non-negative"
    ):
        InternalStructureDetector(
            emit_provisional_choch=True, provisional_choch_break_buffer_atr=-0.1
        )
