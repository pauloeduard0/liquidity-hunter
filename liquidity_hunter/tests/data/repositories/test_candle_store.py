"""Tests for the persistent candle store."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.repositories import SQLiteCandleStore

_START = datetime(2026, 8, 1, tzinfo=UTC)


def _candle(index: int, *, close: float = 100.0, symbol: str = "TEST") -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=TimeFrame.H1,
        timestamp=_START + timedelta(hours=index),
        open=100.0,
        high=max(100.0, close) + 1.0,
        low=min(100.0, close) - 1.0,
        close=close,
        volume=10.0,
        taker_buy_volume=5.0,
    )


@pytest.fixture
def store(tmp_path: Path) -> SQLiteCandleStore:
    return SQLiteCandleStore(tmp_path / "candles.sqlite3")


def test_empty_series_reports_nothing(store: SQLiteCandleStore) -> None:
    assert store.last_timestamp("s", TimeFrame.H1) is None
    assert store.count("s", TimeFrame.H1) == 0
    assert store.load("s", TimeFrame.H1, "TEST", 10) == []


def test_roundtrip_is_chronological(store: SQLiteCandleStore) -> None:
    store.save("s", [_candle(i) for i in range(5)])

    loaded = store.load("s", TimeFrame.H1, "TEST", 10)

    assert [c.timestamp for c in loaded] == [_START + timedelta(hours=i) for i in range(5)]
    assert store.last_timestamp("s", TimeFrame.H1) == _START + timedelta(hours=4)


def test_load_returns_the_newest_when_limited(store: SQLiteCandleStore) -> None:
    store.save("s", [_candle(i) for i in range(10)])

    loaded = store.load("s", TimeFrame.H1, "TEST", 3)

    assert [c.timestamp for c in loaded] == [_START + timedelta(hours=i) for i in (7, 8, 9)]


def test_before_excludes_the_boundary(store: SQLiteCandleStore) -> None:
    store.save("s", [_candle(i) for i in range(10)])

    loaded = store.load("s", TimeFrame.H1, "TEST", 10, before=_START + timedelta(hours=4))

    assert [c.timestamp for c in loaded] == [_START + timedelta(hours=i) for i in range(4)]


def test_resaving_a_candle_overwrites_it(store: SQLiteCandleStore) -> None:
    """A re-fetched bar is the authority: the live edge finishes forming."""
    store.save("s", [_candle(0, close=100.0)])
    store.save("s", [_candle(0, close=250.0)])

    loaded = store.load("s", TimeFrame.H1, "TEST", 10)

    assert len(loaded) == 1
    assert loaded[0].close == 250.0


def test_series_and_timeframe_are_separate_namespaces(store: SQLiteCandleStore) -> None:
    store.save("a", [_candle(0)])
    store.save("b", [_candle(1)])

    assert store.count("a", TimeFrame.H1) == 1
    assert store.count("b", TimeFrame.H1) == 1
    assert store.count("a", TimeFrame.H4) == 0


def test_stored_rows_serve_any_alias_of_the_series(store: SQLiteCandleStore) -> None:
    """The store is keyed by series, so the caller stamps the symbol."""
    store.save("s", [_candle(0, symbol="POOL_A")])

    loaded = store.load("s", TimeFrame.H1, "solana:abc", 10)

    assert loaded[0].symbol == "solana:abc"


def test_survives_a_reopen(tmp_path: Path) -> None:
    path = tmp_path / "candles.sqlite3"
    SQLiteCandleStore(path).save("s", [_candle(i) for i in range(4)])

    assert SQLiteCandleStore(path).count("s", TimeFrame.H1) == 4
