"""Tests for the persistent-history caching provider."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.providers.base import OHLCVProvider
from liquidity_hunter.data.providers.caching import _OVERLAP_CANDLES, CachingOHLCVProvider
from liquidity_hunter.data.repositories import SQLiteCandleStore

_PERIOD = timedelta(hours=1)


def _series(count: int, *, end: datetime, close: float = 100.0) -> list[Candle]:
    """`count` H1 candles ending at `end` (inclusive), oldest first."""
    return [
        Candle(
            symbol="TEST",
            timeframe=TimeFrame.H1,
            timestamp=end - _PERIOD * (count - 1 - i),
            open=100.0,
            high=max(100.0, close) + 1.0,
            low=min(100.0, close) - 1.0,
            close=close,
            volume=10.0,
            taker_buy_volume=5.0,
        )
        for i in range(count)
    ]


class FakeProvider(OHLCVProvider):
    """Answers 'the last N candles' from a fixed history, like the real sources."""

    max_fetch_limit = 1000

    def __init__(self, history: list[Candle], key: str = "fake:TEST") -> None:
        self.history = history
        self.calls: list[int] = []
        self._key = key

    def series_key(self, symbol: str) -> str:
        return self._key

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        self.calls.append(limit)
        return self.history[-limit:] if limit else []


@pytest.fixture
def store(tmp_path: Path) -> SQLiteCandleStore:
    return SQLiteCandleStore(tmp_path / "candles.sqlite3")


def _now_aligned() -> datetime:
    return datetime.now(UTC).replace(minute=0, second=0, microsecond=0)


def test_cold_fetch_asks_for_the_whole_window(store: SQLiteCandleStore) -> None:
    inner = FakeProvider(_series(50, end=_now_aligned()))
    provider = CachingOHLCVProvider(inner, store)

    result = provider.get_ohlcv("TEST", TimeFrame.H1, 20)

    assert inner.calls == [20]
    assert len(result) == 20


def test_live_edge_is_not_stored(store: SQLiteCandleStore) -> None:
    """The newest bar is still forming; persisting it would freeze a partial print."""
    end = _now_aligned()
    inner = FakeProvider(_series(10, end=end))
    provider = CachingOHLCVProvider(inner, store)

    provider.get_ohlcv("TEST", TimeFrame.H1, 10)

    assert store.last_timestamp("fake:TEST", TimeFrame.H1) == end - _PERIOD


def test_warm_refresh_asks_only_for_the_tail(store: SQLiteCandleStore) -> None:
    """Time passes by revealing more of a fixed history -- the source only sees 'now'."""
    now = _now_aligned()
    full = _series(200, end=now)
    inner = FakeProvider(full[:-2])
    provider = CachingOHLCVProvider(inner, store)
    provider.get_ohlcv("TEST", TimeFrame.H1, 100)
    inner.calls.clear()

    inner.history = full
    result = provider.get_ohlcv("TEST", TimeFrame.H1, 100)

    # Stored history ends 3 bars back (2 new bars, plus the unstored live edge),
    # and the request adds the overlap on top -- not the 100 bars asked for.
    assert inner.calls == [3 + 1 + 3]
    assert len(result) == 100
    assert result[-1].timestamp == now
    stamps = [c.timestamp for c in result]
    assert all(b - a == _PERIOD for a, b in zip(stamps, stamps[1:], strict=False))


def test_refresh_rereads_the_live_edge_when_no_bar_closed(store: SQLiteCandleStore) -> None:
    inner = FakeProvider(_series(50, end=_now_aligned()))
    provider = CachingOHLCVProvider(inner, store)
    provider.get_ohlcv("TEST", TimeFrame.H1, 20)
    inner.calls.clear()

    provider.get_ohlcv("TEST", TimeFrame.H1, 20)

    # Nothing new closed, but the live edge is unstored, so it is re-read.
    assert inner.calls == [1 + 1 + 3]


def test_a_forming_bar_is_replaced_by_its_final_print(store: SQLiteCandleStore) -> None:
    end = _now_aligned()
    inner = FakeProvider(_series(20, end=end, close=100.0))
    provider = CachingOHLCVProvider(inner, store)
    provider.get_ohlcv("TEST", TimeFrame.H1, 20)

    # The same bar closes higher, and one more prints after it.
    inner.history = _series(20, end=end, close=100.0) + _series(1, end=end + _PERIOD)
    inner.history[-2] = inner.history[-2].model_copy(update={"close": 175.0})
    result = provider.get_ohlcv("TEST", TimeFrame.H1, 20)

    assert next(c for c in result if c.timestamp == end).close == 175.0


def test_history_grows_past_the_sources_per_request_cap(store: SQLiteCandleStore) -> None:
    """The window slides forward and the old bars stay -- depth the source cannot serve."""
    now = _now_aligned()
    full = _series(300, end=now)
    inner = FakeProvider(full[:-50])
    inner.max_fetch_limit = 100
    provider = CachingOHLCVProvider(inner, store, max_fetch_limit=300)
    provider.get_ohlcv("TEST", TimeFrame.H1, 100)

    inner.history = full
    result = provider.get_ohlcv("TEST", TimeFrame.H1, 300)

    assert store.count("fake:TEST", TimeFrame.H1) > inner.max_fetch_limit
    assert len(result) > inner.max_fetch_limit
    stamps = [c.timestamp for c in result]
    assert all(b - a == _PERIOD for a, b in zip(stamps, stamps[1:], strict=False))


def test_a_gap_discards_stored_history_rather_than_splicing(
    store: SQLiteCandleStore, caplog: pytest.LogCaptureFixture
) -> None:
    """A discontinuity the detectors cannot see is worse than a shorter window."""
    now = _now_aligned()
    full = _series(100, end=now)
    inner = FakeProvider(full[:-40])
    inner.max_fetch_limit = 10
    provider = CachingOHLCVProvider(inner, store, max_fetch_limit=100)
    provider.get_ohlcv("TEST", TimeFrame.H1, 10)

    # A long outage: what the source can still serve does not reach back to
    # stored history.
    inner.history = full
    result = provider.get_ohlcv("TEST", TimeFrame.H1, 100)

    assert len(result) == 10
    assert result[0].timestamp == now - 9 * _PERIOD
    assert "Gap between stored history" in caplog.text


def test_different_series_do_not_mix(store: SQLiteCandleStore) -> None:
    end = _now_aligned()
    spot = FakeProvider(_series(30, end=end, close=100.0), key="binance-spot:BTCUSDT")
    perp = FakeProvider(_series(30, end=end, close=900.0), key="binance-futures:BTCUSDT")
    CachingOHLCVProvider(spot, store).get_ohlcv("BTCUSDT", TimeFrame.H1, 30)

    result = CachingOHLCVProvider(perp, store).get_ohlcv("BTCUSDT", TimeFrame.H1, 30)

    assert {c.close for c in result} == {900.0}


def test_a_shallow_store_is_deepened_rather_than_pinning_the_window(
    store: SQLiteCandleStore,
) -> None:
    """A series first seeded by a small caller must not cap every later request.

    The overview ladder asks for a narrower window than the dashboard. Asking
    only for the tail after that would serve ~300 candles forever.
    """
    inner = FakeProvider(_series(2000, end=_now_aligned()))
    inner.max_fetch_limit = 1500
    provider = CachingOHLCVProvider(inner, store)
    provider.get_ohlcv("TEST", TimeFrame.H1, 300)

    result = provider.get_ohlcv("TEST", TimeFrame.H1, 1500)

    assert len(result) == 1500
    stamps = [c.timestamp for c in result]
    assert all(b - a == _PERIOD for a, b in zip(stamps, stamps[1:], strict=False))


def test_a_source_with_no_more_history_is_not_asked_again(store: SQLiteCandleStore) -> None:
    """A young series is shorter than the request; re-asking for it every time is waste."""
    inner = FakeProvider(_series(120, end=_now_aligned()))
    provider = CachingOHLCVProvider(inner, store)
    provider.get_ohlcv("TEST", TimeFrame.H1, 1000)
    provider.get_ohlcv("TEST", TimeFrame.H1, 1000)
    inner.calls.clear()

    result = provider.get_ohlcv("TEST", TimeFrame.H1, 1000)

    assert inner.calls == [1 + 1 + 3]
    assert len(result) == 120


def test_small_caller_does_not_pin_the_window_of_a_larger_one(
    store: SQLiteCandleStore,
) -> None:
    """A shallow caller must not retire the series for a deeper one.

    The overview ladder asks for a narrow window; the dashboard asks for the
    full one. Once the narrow caller had settled -- its second request is a
    tail, which by construction starts after the oldest stored bar -- the
    series was marked exhausted, and every later dashboard request served the
    narrow store plus a tail instead of the window it asked for. On ETH 30m
    that showed as a chart starting a week back rather than 25 days.
    """
    now = _now_aligned()
    provider = FakeProvider(_series(1000, end=now))
    caching = CachingOHLCVProvider(provider, store)

    caching.get_ohlcv("TEST", TimeFrame.H1, 300)
    caching.get_ohlcv("TEST", TimeFrame.H1, 300)

    deep = caching.get_ohlcv("TEST", TimeFrame.H1, 900)
    assert len(deep) == 900
    assert deep[0].timestamp == provider.history[-900].timestamp


def test_exhaustion_still_stops_a_repeated_full_window_request(
    store: SQLiteCandleStore,
) -> None:
    """A full-window attempt that reaches no further back is not repeated."""
    now = _now_aligned()
    provider = FakeProvider(_series(200, end=now))
    caching = CachingOHLCVProvider(provider, store)

    caching.get_ohlcv("TEST", TimeFrame.H1, 500)
    provider.calls.clear()
    caching.get_ohlcv("TEST", TimeFrame.H1, 500)

    assert provider.calls and provider.calls[0] <= _OVERLAP_CANDLES + 2
