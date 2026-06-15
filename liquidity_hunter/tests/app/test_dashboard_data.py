"""Tests for `liquidity_hunter.app.dashboard_data`."""

from datetime import timedelta

from liquidity_hunter.app.dashboard_data import load_dashboard_data
from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    RetailPositioning,
    StructureEvent,
    StructureScope,
    TimeFrame,
)
from liquidity_hunter.data.providers.base import OHLCVProvider
from liquidity_hunter.liquidity import InternalStructureDetector
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle, make_series

HIGHS = [
    100.0, 101.0, 102.0, 110.0, 103.0, 102.0, 101.0,
    100.0, 101.0, 102.0, 110.0, 103.0, 102.0, 101.0, 100.0,
]
LOWS = [h - 5 for h in HIGHS]

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


def test_load_dashboard_data_assembles_research_snapshot() -> None:
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")

    data = load_dashboard_data(provider=_FakeProvider(candles), symbol="BTCUSDT")

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
    # 140 and a strong bearish volume delta so the break is confirmed as a
    # BOS rather than a liquidity sweep.
    candles[12] = make_candle(
        12,
        STRUCTURE_HIGHS[12],
        STRUCTURE_LOWS[12],
        symbol="BTCUSDT",
        close=135.0,
        taker_buy_volume=0.3,
    )

    data = load_dashboard_data(provider=_FakeProvider(candles), symbol="BTCUSDT", swing_lookback=2)

    assert len(data.market_structure_events) == 1
    event = data.market_structure_events[0]
    assert event.event is StructureEvent.BREAK_OF_STRUCTURE
    assert event.direction is MarketDirection.BEARISH
    assert data.higher_timeframe_direction is MarketDirection.BEARISH


def test_load_dashboard_data_internal_structure_events_use_internal_scope() -> None:
    candles = make_series(STRUCTURE_HIGHS, STRUCTURE_LOWS, symbol="BTCUSDT")
    candles[12] = make_candle(
        12,
        STRUCTURE_HIGHS[12],
        STRUCTURE_LOWS[12],
        symbol="BTCUSDT",
        close=135.0,
        taker_buy_volume=0.3,
    )

    data = load_dashboard_data(
        provider=_FakeProvider(candles), symbol="BTCUSDT", internal_swing_lookback=2
    )

    assert data.internal_structure_events
    assert all(
        event.scope is StructureScope.INTERNAL for event in data.internal_structure_events
    )
    # `higher_timeframe_direction` still reflects only `market_structure_events`
    # (empty here, since `swing_lookback` keeps its default of 50).
    assert data.market_structure_events == []
    assert data.higher_timeframe_direction is MarketDirection.NEUTRAL


def test_load_dashboard_data_neutral_trend_with_no_structure_events() -> None:
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")

    data = load_dashboard_data(provider=_FakeProvider(candles), symbol="BTCUSDT")

    assert data.market_structure_events == []
    assert data.higher_timeframe_direction is MarketDirection.NEUTRAL


def test_load_dashboard_data_fetches_finer_timeframe_for_internal_structure() -> None:
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")
    provider = _FakeProvider(candles)

    load_dashboard_data(provider=provider, symbol="BTCUSDT", timeframe=TimeFrame.H1)

    assert provider.requested_timeframes == [TimeFrame.H1, TimeFrame.M30]


def test_load_dashboard_data_finer_fetch_covers_same_calendar_range() -> None:
    # M30 candles are half as long as H1 candles, so the finer fetch needs
    # twice the limit to cover the same calendar range as `candles`.
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")
    provider = _FakeProvider(candles)

    load_dashboard_data(provider=provider, symbol="BTCUSDT", timeframe=TimeFrame.H1, limit=500)

    assert provider.requested_limits == [500, 1000]


def test_load_dashboard_data_finer_fetch_limit_capped_at_klines_max() -> None:
    # H4 -> H1 is a 4x ratio; at limit=500 that would be 2000, capped to 1000.
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")
    provider = _FakeProvider(candles)

    load_dashboard_data(provider=provider, symbol="BTCUSDT", timeframe=TimeFrame.H4, limit=500)

    assert provider.requested_limits == [500, 1000]


def test_load_dashboard_data_skips_finer_fetch_for_finest_timeframe() -> None:
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")
    provider = _FakeProvider(candles)

    load_dashboard_data(provider=provider, symbol="BTCUSDT", timeframe=TimeFrame.M1)

    assert provider.requested_timeframes == [TimeFrame.M1]


def test_load_dashboard_data_internal_structure_filters_to_visible_window() -> None:
    # A long M30 zigzag produces internal structure events spanning over a
    # week, but only the first ~19 hours fall inside the H1 visible window
    # (limit=20 -> 20 H1 candles starting at the same `BASE_TIME`).
    m30_highs, m30_lows = _trending_zigzag(340)
    m30_candles = make_series(
        m30_highs,
        m30_lows,
        symbol="BTCUSDT",
        timeframe=TimeFrame.M30,
        interval=timedelta(minutes=30),
    )
    h1_highs, h1_lows = _trending_zigzag(20)
    h1_candles = make_series(h1_highs, h1_lows, symbol="BTCUSDT", timeframe=TimeFrame.H1)

    provider = _PerTimeframeFakeProvider({TimeFrame.H1: h1_candles, TimeFrame.M30: m30_candles})

    data = load_dashboard_data(
        provider=provider,
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        limit=20,
        internal_swing_lookback=10,
    )

    # H1 limit=20 -> coverage_ratio=2 -> finer_limit=40, +300 buffer = 340.
    assert provider.requested_limits == [20, 340]

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
    unfiltered_events = InternalStructureDetector(swing_lookback=10).detect(m30_candles)
    assert len(unfiltered_events) > len(data.internal_structure_events)
