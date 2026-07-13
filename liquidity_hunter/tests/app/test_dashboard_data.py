"""Tests for `liquidity_hunter.app.dashboard_data`."""

from datetime import UTC, datetime

import pytest

from liquidity_hunter.app import dashboard_data
from liquidity_hunter.app.dashboard_data import (
    _STRUCTURAL_ANCHOR_REGION,
    _build_internal_detector,
    _drop_pre_break_reference_bos,
    _drop_resumed_fizzle_markers,
    _reanchor_bos_close_break,
    _run_internal_structure,
    _structural_anchor_index,
    load_dashboard_data,
)
from liquidity_hunter.core.domain import (
    Candle,
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
)
from liquidity_hunter.data.exceptions import DataProviderConnectionError
from liquidity_hunter.data.providers.base import FuturesDataProvider, OHLCVProvider
from liquidity_hunter.liquidity import InternalStructureDetector
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle, make_series

HIGHS = [
    100.0, 101.0, 102.0, 110.0, 103.0, 102.0, 101.0,
    100.0, 101.0, 102.0, 110.0, 103.0, 102.0, 101.0, 100.0,
]
LOWS = [h - 5 for h in HIGHS]

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
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")
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


def _structure_event(
    minute: int,
    event: StructureEvent,
    direction: MarketDirection,
    *,
    reference_minute: int | None = None,
    provisional: bool = False,
) -> MarketStructure:
    return MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.M15,
        timestamp=datetime(2026, 7, 2, 0, minute, tzinfo=UTC),
        event=event,
        direction=direction,
        price_level=100.0,
        reference_price_level=90.0,
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

    assert _drop_resumed_fizzle_markers([choch, fizzle, bos]) == [choch, bos]


def test_drop_resumed_fizzle_markers_keeps_marker_with_no_later_bos() -> None:
    # No same-direction BOS after the reclaim: the reversal genuinely fizzled
    # (price ranges beyond the reclaimed level), the marker stands.
    choch = _structure_event(10, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH)
    fizzle = _structure_event(
        20, StructureEvent.CHOCH_FAILED, MarketDirection.BEARISH, provisional=True
    )

    assert _drop_resumed_fizzle_markers([choch, fizzle]) == [choch, fizzle]


def test_drop_resumed_fizzle_markers_ignores_opposite_direction_bos() -> None:
    # An opposite-direction BOS is not the marked reversal resuming.
    fizzle = _structure_event(
        20, StructureEvent.CHOCH_FAILED, MarketDirection.BEARISH, provisional=True
    )
    bos = _structure_event(30, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH)

    assert _drop_resumed_fizzle_markers([fizzle, bos]) == [fizzle, bos]


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
    assert _drop_resumed_fizzle_markers(events) == events


def test_drop_resumed_fizzle_markers_keeps_genuine_choch_failed() -> None:
    # A non-provisional CHOCH_FAILED is a real state-machine failure (the trend
    # flipped back); the resumed trend's BOS after it is expected, not a cancel.
    failed = _structure_event(20, StructureEvent.CHOCH_FAILED, MarketDirection.BULLISH)
    bos = _structure_event(30, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH)

    assert _drop_resumed_fizzle_markers([failed, bos]) == [failed, bos]


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


def test_run_internal_structure_drops_eth_1h_resumed_fizzle() -> None:
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
        and e.timestamp == datetime(2026, 6, 29, 16, tzinfo=UTC)
        for e in run.events
    )


def _load_btc_1d_weak_fail_candles() -> list[Candle]:
    """BTCUSDT 1d 2022-06-03..2026-07-11, the full 1500-candle production slice.

    Real-data regression for ``choch_weak_ref_fail_at_broken_level``: the
    2026-04-30 bullish CHoCH fired against a *weak* re-anchor reference
    (75998.9) and collapsed within days, but its 59800 leg origin was never
    sustained-broken -- with the flag off the trend sits bullish through the
    entire 82.8k -> 57.7k crash, every new low prints as a counter-trend
    sweep, and the chart shows no bearish BOS at the bottom (unlike ETH D1,
    whose rally never fired a CHoCH and whose June break of 1736 printed the
    continuation BOS). With the flag on the CHoCH fails for real at its
    broken level (2026-05-26), the trend resumes bearish, and the June close
    below the restored 59800 floor prints the BOS at the bottom. 5-column
    rows: ts/open/high/low/close.
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


def test_btc_1d_weak_choch_fails_at_broken_level_and_prints_bottom_bos() -> None:
    candles = _load_btc_1d_weak_fail_candles()
    provider = _FuturesLimitFakeProvider({TimeFrame.D1: candles})

    run = _run_internal_structure(provider, "BTCUSDT", TimeFrame.D1, 1200, False)

    # The weak 2026-04-30 bullish CHoCH fails for real (not a fizzle marker)
    # at the level it broke, once price sustains closes back below it.
    assert any(
        e.event is StructureEvent.CHOCH_FAILED
        and not e.provisional
        and e.direction is MarketDirection.BULLISH
        and e.timestamp == datetime(2026, 5, 26, tzinfo=UTC)
        and e.reference_price_level == pytest.approx(75998.9)
        for e in run.events
    )
    # The resumed bearish trend prints the continuation BOS at the bottom,
    # referencing the restored 59800 floor (the January BOS low).
    assert any(
        e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BEARISH
        and e.timestamp == datetime(2026, 6, 25, tzinfo=UTC)
        and e.reference_price_level == pytest.approx(59800.0)
        for e in run.events
    )
    assert run.trend is MarketDirection.BEARISH


def test_btc_1d_weak_fail_off_trend_stays_bullish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dashboard_data, "_CHOCH_WEAK_REF_FAIL_AT_BROKEN_LEVEL", False)
    candles = _load_btc_1d_weak_fail_candles()
    provider = _FuturesLimitFakeProvider({TimeFrame.D1: candles})

    run = _run_internal_structure(provider, "BTCUSDT", TimeFrame.D1, 1200, False)

    # Off: only the additive fizzle marker fires (trend never flips), so the
    # crash prints no bearish BOS after the January one.
    assert not any(
        e.event is StructureEvent.CHOCH_FAILED and not e.provisional
        for e in run.events
        if e.timestamp >= datetime(2026, 1, 1, tzinfo=UTC)
    )
    assert not any(
        e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BEARISH
        and e.timestamp > datetime(2026, 2, 1, tzinfo=UTC)
        for e in run.events
    )
    assert run.trend is MarketDirection.BULLISH


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

    # Off: both displaced bullish CHoCHs are marked failed on their pullbacks,
    # at the levels they launched from (2.045 and 2.173).
    fail_refs = {
        round(e.reference_price_level, 3)
        for e in run.events
        if e.event is StructureEvent.CHOCH_FAILED
        and e.direction is MarketDirection.BULLISH
        and datetime(2026, 6, 6, tzinfo=UTC) <= e.timestamp <= datetime(2026, 6, 22, tzinfo=UTC)
    }
    assert fail_refs == {2.045, 2.173}


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

    # No bearish failure survives in the rally/top window (06-15..06-16 12:00);
    # the 06-16 14:00 bearish CHoCH stands and leads a clean bearish BOS
    # staircase instead of the old phantom-driven whipsaw.
    rally_top_window = (datetime(2026, 6, 15, tzinfo=UTC), datetime(2026, 6, 16, 12, tzinfo=UTC))
    assert not any(
        e.event is StructureEvent.CHOCH_FAILED
        and e.direction is MarketDirection.BEARISH
        and rally_top_window[0] <= e.timestamp <= rally_top_window[1]
        for e in run.events
    )
    assert any(
        e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BEARISH
        and e.timestamp == datetime(2026, 6, 17, 19, tzinfo=UTC)
        for e in run.events
    )
