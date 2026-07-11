"""Tests for `liquidity_hunter.app.overview`."""

from liquidity_hunter.app.dashboard_data import load_dashboard_data
from liquidity_hunter.app.overview import (
    OVERVIEW_TIMEFRAMES,
    build_overview,
    load_overview,
    load_timeframe_structure,
)
from liquidity_hunter.core.domain import (
    Candle,
    FundingRate,
    LiquidityHuntPhase,
    LongShortRatio,
    MarketDirection,
    OpenInterestPoint,
    RetailPositioning,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.data.providers.base import FuturesDataProvider, OHLCVProvider
from liquidity_hunter.tests.liquidity.detectors._factories import make_series


class _PerTimeframeProvider(OHLCVProvider):
    """Returns the trailing `limit` candles of a per-timeframe series."""

    def __init__(self, series_by_timeframe: dict[TimeFrame, list[Candle]]) -> None:
        self._series_by_timeframe = series_by_timeframe
        self.requested_timeframes: list[TimeFrame] = []

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        self.requested_timeframes.append(timeframe)
        return self._series_by_timeframe[timeframe][-limit:]


class _EmptyFuturesProvider(FuturesDataProvider):
    """Empty futures state, so `load_dashboard_data` runs without network."""

    def get_open_interest_history(
        self, symbol: str, timeframe: TimeFrame, limit: int = 500
    ) -> list[OpenInterestPoint]:
        return []

    def get_funding_rate_history(self, symbol: str, limit: int = 500) -> list[FundingRate]:
        return []

    def get_long_short_ratio(
        self, symbol: str, timeframe: TimeFrame, limit: int = 500
    ) -> list[LongShortRatio]:
        return []


def _zigzag(num_candles: int, drift: float) -> list[Candle]:
    """A drifting zigzag: repeated same-direction structure breaks.

    Rising (`drift > 0`) yields a BULLISH internal trend, falling a BEARISH
    one (the first bootstrap break while NEUTRAL sets the trend, and the
    monotonic drift keeps extending it).
    """
    amplitude, period = 15.0, 20
    highs, lows = [], []
    for i in range(num_candles):
        phase = i % period
        half = period // 2
        wave = amplitude * (phase / half if phase < half else 1 - (phase - half) / half)
        level = 500.0 + drift * i + wave
        highs.append(level + 2.0)
        lows.append(level - 2.0)
    return make_series(highs, lows)


def test_load_overview_builds_ladder_in_default_order() -> None:
    rising = _zigzag(200, drift=1.0)
    provider = _PerTimeframeProvider(dict.fromkeys(OVERVIEW_TIMEFRAMES, rising))

    overview = load_overview(provider=provider, symbol="BTCUSDT")

    assert overview.symbol == "BTCUSDT"
    assert [entry.timeframe for entry in overview.entries] == list(OVERVIEW_TIMEFRAMES)
    # One fetch per timeframe -- no duplicate HTF fetches (the HTF trend is
    # read from the same snapshot batch).
    assert sorted(provider.requested_timeframes, key=list(TimeFrame).index) == sorted(
        OVERVIEW_TIMEFRAMES, key=list(TimeFrame).index
    )
    for entry in overview.entries:
        assert entry.trend is MarketDirection.BULLISH
        assert entry.current_price == rising[-1].close
        assert entry.candle_timestamp == rising[-1].timestamp
        # Every timeframe rises, so structure is aligned everywhere.
        assert entry.hunt_phase is LiquidityHuntPhase.NONE
        assert entry.hunted_side is RetailPositioning.NEUTRAL


def test_overview_higher_timeframe_anchors_follow_the_map() -> None:
    rising = _zigzag(200, drift=1.0)
    provider = _PerTimeframeProvider(dict.fromkeys(OVERVIEW_TIMEFRAMES, rising))

    overview = load_overview(provider=provider)

    by_timeframe = {entry.timeframe: entry for entry in overview.entries}
    assert by_timeframe[TimeFrame.M5].higher_timeframe is TimeFrame.H1
    assert by_timeframe[TimeFrame.H1].higher_timeframe is TimeFrame.H4
    assert by_timeframe[TimeFrame.D1].higher_timeframe is TimeFrame.W1
    # W1 is the top of the ladder: no anchor, own trend, reads "aligned".
    assert by_timeframe[TimeFrame.W1].higher_timeframe is None
    assert by_timeframe[TimeFrame.W1].higher_timeframe_direction is None
    assert by_timeframe[TimeFrame.M5].higher_timeframe_direction is MarketDirection.BULLISH


def test_overview_counter_trend_timeframe_reports_hunt() -> None:
    falling = _zigzag(200, drift=-1.0)
    rising = _zigzag(200, drift=1.0)
    provider = _PerTimeframeProvider({TimeFrame.M15: falling, TimeFrame.H1: rising})

    overview = load_overview(
        provider=provider, timeframes=[TimeFrame.M15, TimeFrame.H1]
    )

    m15 = overview.entries[0]
    assert m15.trend is MarketDirection.BEARISH
    assert m15.higher_timeframe is TimeFrame.H1
    assert m15.higher_timeframe_direction is MarketDirection.BULLISH
    # A bearish correction inside a bullish HTF: its sellers are the fuel.
    assert m15.hunted_side is RetailPositioning.SHORT
    assert m15.hunt_phase is not LiquidityHuntPhase.NONE


def test_overview_last_event_reflects_visible_structure() -> None:
    rising = _zigzag(200, drift=1.0)
    snapshot = load_timeframe_structure(
        provider=_PerTimeframeProvider({TimeFrame.H1: rising}), timeframe=TimeFrame.H1
    )

    overview = build_overview("BTCUSDT", [snapshot])

    entry = overview.entries[0]
    assert entry.last_event in (
        StructureEvent.BREAK_OF_STRUCTURE,
        StructureEvent.CHANGE_OF_CHARACTER,
        StructureEvent.CHOCH_FAILED,
    )
    assert entry.last_event_direction is MarketDirection.BULLISH
    assert entry.last_event_timestamp is not None
    assert entry.last_event_candles_ago is not None
    assert entry.last_event_candles_ago == sum(
        1 for c in snapshot.candles if c.timestamp > entry.last_event_timestamp
    )


def test_load_timeframe_structure_matches_dashboard_internal_run() -> None:
    """The ladder must read exactly the structure the chart renders."""
    rising = _zigzag(200, drift=1.0)
    series = {TimeFrame.H1: rising, TimeFrame.H4: rising}

    snapshot = load_timeframe_structure(
        provider=_PerTimeframeProvider(series), timeframe=TimeFrame.H1
    )
    data = load_dashboard_data(
        provider=_PerTimeframeProvider(series),
        timeframe=TimeFrame.H1,
        futures_provider=_EmptyFuturesProvider(),
    )

    assert snapshot.candles == data.candles
    assert snapshot.events == data.internal_structure_events


def test_build_overview_handles_partial_batch_without_anchor() -> None:
    falling = _zigzag(200, drift=-1.0)
    snapshot = load_timeframe_structure(
        provider=_PerTimeframeProvider({TimeFrame.M15: falling}), timeframe=TimeFrame.M15
    )

    overview = build_overview("BTCUSDT", [snapshot])

    entry = overview.entries[0]
    # M15's anchor (H1) is not in the batch: degrade to the own trend, which
    # reads "aligned" -- the same fallback `load_dashboard_data` uses for the
    # top timeframe.
    assert entry.higher_timeframe is None
    assert entry.higher_timeframe_direction is None
    assert entry.hunt_phase is LiquidityHuntPhase.NONE
