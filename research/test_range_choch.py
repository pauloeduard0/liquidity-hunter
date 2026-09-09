"""Testes das funcoes puras de `research/range_choch.py`.

Fora de `liquidity_hunter/tests` de proposito: `testpaths` aponta so para o
pacote, entao a suite de regressao nao muda. Rode explicitamente:

    poetry run pytest research/test_range_choch.py
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from liquidity_hunter.core.domain import (
    Candle,
    ConsolidationRange,
    ConsolidationStatus,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    StructureScope,
    TimeFrame,
)
from research.range_choch import EDGE_CANDLES, cases_of_run, summarize

START = datetime(2026, 1, 1, tzinfo=UTC)


def ts(i: int) -> datetime:
    return START + timedelta(hours=i)


def candles(n: int) -> list[Candle]:
    return [
        Candle(
            symbol="BTCUSDT",
            timeframe=TimeFrame.H1,
            timestamp=ts(i),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for i in range(n)
    ]


def choch(i: int, direction: MarketDirection, *, provisional: bool = False) -> MarketStructure:
    return MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=ts(i),
        event=StructureEvent.CHANGE_OF_CHARACTER,
        direction=direction,
        price_level=100.0,
        reference_price_level=99.0,
        scope=StructureScope.INTERNAL,
        provisional=provisional,
    )


def event(
    i: int, kind: StructureEvent, direction: MarketDirection, *, provisional: bool = False
) -> MarketStructure:
    return MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=ts(i),
        event=kind,
        direction=direction,
        price_level=100.0,
        reference_price_level=99.0,
        scope=StructureScope.INTERNAL,
        provisional=provisional,
    )


def box(start: int, end: int | None) -> ConsolidationRange:
    resolved = end is not None
    return ConsolidationRange(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        start_timestamp=ts(start),
        end_timestamp=ts(end) if resolved else None,
        price_low=90.0,
        price_high=110.0,
        status=ConsolidationStatus.RESOLVED if resolved else ConsolidationStatus.ACTIVE,
        resolved_direction=MarketDirection.BULLISH if resolved else None,
        candle_count=(end - start) if resolved else 60,
    )


def test_choch_inside_an_active_box_is_in_range() -> None:
    (case,) = cases_of_run([choch(50, MarketDirection.BULLISH)], [box(10, None)], candles(200))
    assert case.zone == "in_range"
    assert case.range_status == "active"
    assert case.range_end is None


def test_choch_just_after_a_resolved_box_is_range_edge() -> None:
    (case,) = cases_of_run(
        [choch(60 + EDGE_CANDLES - 1, MarketDirection.BULLISH)], [box(10, 60)], candles(200)
    )
    assert case.zone == "range_edge"
    assert case.range_end == str(ts(60))


def test_choch_far_past_the_box_is_the_control() -> None:
    (case,) = cases_of_run(
        [choch(60 + EDGE_CANDLES + 5, MarketDirection.BULLISH)], [box(10, 60)], candles(200)
    )
    assert case.zone == "outside"
    assert case.range_start is None


def test_a_real_choch_failed_of_the_same_direction_is_a_failure() -> None:
    events = [
        choch(50, MarketDirection.BULLISH),
        event(70, StructureEvent.CHOCH_FAILED, MarketDirection.BULLISH),
    ]
    (case,) = cases_of_run(events, [], candles(200))
    assert case.outcome == "failed"
    assert case.outcome_timestamp == str(ts(70))


def test_a_provisional_choch_failed_is_a_fizzle_not_a_failure() -> None:
    events = [
        choch(50, MarketDirection.BULLISH),
        event(70, StructureEvent.CHOCH_FAILED, MarketDirection.BULLISH, provisional=True),
    ]
    (case,) = cases_of_run(events, [], candles(200))
    assert case.outcome == "fizzled"


def test_a_same_direction_bos_confirms_the_choch() -> None:
    events = [
        choch(50, MarketDirection.BULLISH),
        event(60, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH),
        event(90, StructureEvent.CHOCH_FAILED, MarketDirection.BULLISH),
    ]
    (case,) = cases_of_run(events, [], candles(200))
    # O BOS veio primeiro: a reversao virou estrutura, e o desfecho e dela.
    assert case.outcome == "confirmed"


def test_a_provisional_bos_does_not_confirm() -> None:
    events = [
        choch(50, MarketDirection.BULLISH),
        event(60, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, provisional=True),
    ]
    (case,) = cases_of_run(events, [], candles(200))
    assert case.outcome == "open"


def test_an_opposite_choch_soon_after_reverses_it() -> None:
    events = [choch(50, MarketDirection.BULLISH), choch(80, MarketDirection.BEARISH)]
    cases = cases_of_run(events, [], candles(200))
    assert cases[0].outcome == "reversed"


def test_an_opposite_choch_long_after_is_not_a_reversal() -> None:
    events = [choch(50, MarketDirection.BULLISH), choch(150, MarketDirection.BEARISH)]
    cases = cases_of_run(events, [], candles(200))
    assert cases[0].outcome == "open"


def test_nothing_after_the_choch_leaves_it_open() -> None:
    (case,) = cases_of_run([choch(50, MarketDirection.BULLISH)], [], candles(200))
    assert case.outcome == "open"
    assert case.outcome_timestamp is None


def test_only_change_of_character_events_become_cases() -> None:
    events = [
        event(50, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH),
        event(60, StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH),
    ]
    assert cases_of_run(events, [], candles(200)) == []


def test_summarize_excludes_open_from_the_fail_rate_denominator() -> None:
    events = [
        choch(10, MarketDirection.BULLISH),
        event(20, StructureEvent.CHOCH_FAILED, MarketDirection.BULLISH),
        choch(40, MarketDirection.BULLISH),
        event(50, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH),
        choch(180, MarketDirection.BEARISH),  # sem desfecho: open
    ]
    stats = summarize(cases_of_run(events, [], candles(200)))["outside"]
    assert stats["total"] == 3
    assert stats["open"] == 1
    assert stats["settled"] == 2
    assert stats["fail_rate"] == 0.5
