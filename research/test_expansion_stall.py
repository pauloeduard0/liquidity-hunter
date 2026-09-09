"""Testes das funcoes puras de `research/expansion_stall.py`.

Fora de `liquidity_hunter/tests` de proposito (a suite de regressao nao muda):

    poetry run pytest research/test_expansion_stall.py
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    StructureScope,
    TimeFrame,
)
from research.expansion_stall import _runs, episodes_of_run

START = datetime(2026, 1, 1, tzinfo=UTC)
BULL, BEAR = MarketDirection.BULLISH, MarketDirection.BEARISH


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


def ev(
    i: int,
    kind: StructureEvent,
    direction: MarketDirection,
    price: float = 100.0,
    *,
    provisional: bool = False,
) -> MarketStructure:
    return MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=ts(i),
        event=kind,
        direction=direction,
        price_level=price,
        reference_price_level=price - 1,
        scope=StructureScope.INTERNAL,
        provisional=provisional,
    )


def bos(i: int, direction: MarketDirection, price: float, **kw: bool) -> MarketStructure:
    return ev(i, StructureEvent.BREAK_OF_STRUCTURE, direction, price, **kw)


def choch(i: int, direction: MarketDirection, price: float = 100.0) -> MarketStructure:
    return ev(i, StructureEvent.CHANGE_OF_CHARACTER, direction, price)


def failed(i: int, direction: MarketDirection, **kw: bool) -> MarketStructure:
    return ev(i, StructureEvent.CHOCH_FAILED, direction, 100.0, **kw)


# --- deteccao da corrida de BOS -------------------------------------------


def test_a_choch_ends_the_run() -> None:
    events = [bos(1, BULL, 100), bos(2, BULL, 110), choch(3, BEAR), bos(4, BULL, 120)]
    assert [len(r) for r in _runs(events)] == [2, 1]


def test_an_opposite_bos_starts_another_run() -> None:
    events = [bos(1, BULL, 100), bos(2, BEAR, 90), bos(3, BEAR, 80)]
    assert [len(r) for r in _runs(events)] == [1, 2]


def test_provisional_bos_do_not_count_as_the_run() -> None:
    events = [bos(1, BULL, 100), bos(2, BULL, 110, provisional=True), bos(3, BULL, 120)]
    assert [len(r) for r in _runs(events)] == [2]


# --- classificacao do episodio --------------------------------------------


def test_a_short_run_is_control_not_expansion() -> None:
    events = [bos(1, BULL, 100), bos(2, BULL, 130)]
    kinds = {e.kind for e in episodes_of_run(events, candles(200), min_bos=3, min_atr=1.0)}
    assert kinds == {"control"}


def test_a_run_below_the_displacement_floor_is_not_an_expansion() -> None:
    events = [bos(1, BULL, 100.0), bos(2, BULL, 100.1), bos(3, BULL, 100.2)]
    kinds = {e.kind for e in episodes_of_run(events, candles(200), min_bos=3, min_atr=50.0)}
    assert kinds == {"control"}


def _expansion(events: list[MarketStructure]) -> object:
    found = [
        e
        for e in episodes_of_run(events, candles(200), min_bos=3, min_atr=1.0)
        if e.kind == "expansion"
    ]
    assert len(found) == 1
    return found[0]


EXPANSION = [bos(1, BULL, 100), bos(2, BULL, 110), bos(3, BULL, 130)]


def test_the_users_shape_counter_choch_that_fails_and_the_leg_resumes() -> None:
    case = _expansion([*EXPANSION, choch(10, BEAR), failed(20, BEAR), bos(30, BULL, 140)])
    assert case.shape == "counter_failed_resumed"
    assert case.outcome == "resumed"
    assert case.first_counter_choch == str(ts(10))
    assert case.counter_choch_invalidated == "failed"
    assert case.counter_choch_confirmed is False
    assert case.last_bos_timestamp == str(ts(3))
    assert case.expansion_extreme == 130
    assert case.run_bos == 3


def test_a_fizzle_counts_as_invalidation_not_as_a_failure() -> None:
    case = _expansion(
        [*EXPANSION, choch(10, BEAR), failed(20, BEAR, provisional=True), bos(30, BULL, 140)]
    )
    assert case.counter_choch_invalidated == "fizzled"


def test_a_counter_choch_that_becomes_structure_is_a_legitimate_reversal() -> None:
    case = _expansion([*EXPANSION, choch(10, BEAR), bos(20, BEAR, 80)])
    assert case.shape == "counter_confirmed"
    assert case.outcome == "reversed"
    assert case.counter_choch_confirmed is True


def test_nothing_after_the_expansion_leaves_the_leg_open() -> None:
    case = _expansion(EXPANSION)
    assert case.shape == "quiet_open"
    assert case.outcome == "open"
    assert case.events == 0


def test_a_counter_choch_that_neither_confirms_nor_fails_stays_unresolved() -> None:
    case = _expansion([*EXPANSION, choch(10, BEAR), bos(40, BULL, 140)])
    assert case.shape == "counter_unresolved_resumed"
    assert case.counter_choch_confirmed is False
    assert case.counter_choch_invalidated is None


def test_a_provisional_bos_does_not_re_establish_structure() -> None:
    case = _expansion([*EXPANSION, bos(10, BULL, 140, provisional=True)])
    assert case.outcome == "open"


# --- densidade -------------------------------------------------------------


def test_density_counts_events_alternations_and_the_window() -> None:
    case = _expansion(
        [
            *EXPANSION,
            choch(10, BEAR),
            failed(20, BEAR),
            choch(25, BULL),
            choch(28, BEAR),
            bos(30, BULL, 140),
        ]
    )
    assert case.events == 5           # tudo depois do ultimo BOS da expansao
    assert case.choch == 3
    assert case.invalidated == 1
    assert case.candles_since_bos == 27
    # bear -> bull -> bear -> bull(retomada): tres trocas de direcao
    assert case.alternations == 3
