"""The anchor store: hysteresis state, held outside the pure pipeline."""

from datetime import UTC, datetime, timedelta

from liquidity_hunter.api.anchors import AnchorStore
from liquidity_hunter.core.domain import TimeFrame

TS = datetime(2026, 7, 26, 20, tzinfo=UTC)


def test_unknown_pair_hints_nothing() -> None:
    # No memory means no hint, which is exactly today's stateless behaviour.
    assert AnchorStore().get("BTCUSDT", TimeFrame.H1) is None


def test_remembers_per_symbol_and_timeframe() -> None:
    store = AnchorStore()
    store.remember("BTCUSDT", TimeFrame.H1, TS)
    store.remember("BTCUSDT", TimeFrame.H4, TS + timedelta(hours=4))

    assert store.get("BTCUSDT", TimeFrame.H1) == TS
    assert store.get("BTCUSDT", TimeFrame.H4) == TS + timedelta(hours=4)
    assert store.get("ETHUSDT", TimeFrame.H1) is None


def test_none_anchor_forgets() -> None:
    # A run with no pre-visible buffer anchors at 0. Hinting a candle the next
    # run may not even reach is worse than re-deriving, so it forgets instead.
    store = AnchorStore()
    store.remember("BTCUSDT", TimeFrame.H1, TS)
    store.remember("BTCUSDT", TimeFrame.H1, None)

    assert store.get("BTCUSDT", TimeFrame.H1) is None


def test_entry_expires() -> None:
    store = AnchorStore(ttl_seconds=0.0)
    store.remember("BTCUSDT", TimeFrame.H1, TS)

    assert store.get("BTCUSDT", TimeFrame.H1) is None


def test_evicts_least_recently_used_past_the_cap() -> None:
    store = AnchorStore(max_entries=2)
    for i, symbol in enumerate(("A", "B", "C")):
        store.remember(symbol, TimeFrame.H1, TS + timedelta(hours=i))

    live = [s for s in ("A", "B", "C") if store.get(s, TimeFrame.H1) is not None]
    assert live == ["B", "C"]
