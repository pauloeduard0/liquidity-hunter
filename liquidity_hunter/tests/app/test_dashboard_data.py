"""Tests for `liquidity_hunter.app.dashboard_data`."""

from datetime import UTC, datetime

from liquidity_hunter.app.dashboard_data import (
    _STRUCTURAL_ANCHOR_REGION,
    _drop_pre_break_reference_bos,
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


def test_load_dashboard_data_derives_trend_from_market_structure() -> None:
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
    assert data.higher_timeframe_direction is MarketDirection.BEARISH


def test_load_dashboard_data_internal_structure_events_use_internal_scope() -> None:
    # Extended series: bearish BOS at L130 confirmed by LH pullback at H180.
    int_highs = [150.0] * 20
    int_highs[2] = 200.0
    int_highs[17] = 180.0
    int_lows = [145.0] * 20
    int_lows[7] = 140.0
    int_lows[12] = 130.0
    candles = make_series(int_highs, int_lows, symbol="BTCUSDT")
    candles[12] = make_candle(
        12,
        int_highs[12],
        int_lows[12],
        symbol="BTCUSDT",
        close=135.0,
        taker_buy_volume=0.3,
    )
    # The confirming LH pullback closes near its high (a real bounce, not a
    # midpoint doji) so it passes the BOS pullback wick filter.
    candles[17] = make_candle(17, int_highs[17], int_lows[17], symbol="BTCUSDT", close=178.0)

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
    # visible window is its tail. Then the HTF series (100) is fetched.
    assert provider.requested_timeframes == [TimeFrame.H1, TimeFrame.H4]
    assert provider.requested_limits == [800, 100]


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

    assert provider.requested_limits == [1000, 100]


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

    # limit=40 + 300 buffer = 340 (fetched once), plus HTF fetch (100).
    assert provider.requested_limits == [340, 100]

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
