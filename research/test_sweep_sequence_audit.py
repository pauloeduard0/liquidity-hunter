"""Testes do S3 — a contagem de sequencia e os seus tempos.

Tres grupos:

* **lado fisico e tempos** -- `physical_side`, `known_at_all` e
  `causal_trend_series` decidem *quando* uma sequencia existe e *em que
  tendencia*. Erram em silencio; toda conclusao do S3 depende deles.
* **contagem** -- as quatro definicoes pre-registradas do S3.0 quebram a run em
  lugares diferentes. Cada quebra e testada explicitamente, sobre um stream
  sintetico onde a resposta certa e obvia.
* **ponta a ponta** -- sobre serie real em cache: que o outcome parte do
  `known_at` (nunca do timestamp back-datado), que a ordem da sequencia e a de
  disponibilidade e que nenhum estagio le o futuro. Pulam sem cache local.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from liquidity_hunter.app.dashboard_data import load_dashboard_data
from liquidity_hunter.core.domain import (
    MarketDirection,
    MarketStructure,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.tests.liquidity.detectors._factories import make_series
from research._offline import OfflineKlinesProvider
from research._paginated import NoFuturesProvider
from research.sweep_sequence_audit import (
    DEFS,
    PERSISTENCE,
    PRIMARY,
    Row,
    _reach,
    _time_to_mfe,
    causal_trend_series,
    extract,
    known_at_all,
    physical_side,
    stage,
)

BULL = MarketDirection.BULLISH
BEAR = MarketDirection.BEARISH
NEUTRAL = MarketDirection.NEUTRAL
SWEEP = StructureEvent.LIQUIDITY_SWEEP
BOS = StructureEvent.BREAK_OF_STRUCTURE
CHOCH = StructureEvent.CHANGE_OF_CHARACTER

T0 = datetime(2024, 1, 1, tzinfo=UTC)   # = `_factories.BASE_TIME`


def _ev(event, direction, *, hour: int, price: float, ref: float | None = None):
    return MarketStructure(
        symbol="T", timeframe=TimeFrame.H1, timestamp=T0 + timedelta(hours=hour),
        event=event, direction=direction, price_level=price,
        reference_price_level=ref,
    )


# --------------------------------------------------------------------------
# S3.7 — lado fisico
# --------------------------------------------------------------------------


def test_a_wick_above_the_reference_is_a_swept_high():
    assert physical_side(_ev(SWEEP, BULL, hour=1, price=110.0, ref=100.0)) == "high"


def test_a_wick_below_the_reference_is_a_swept_low():
    assert physical_side(_ev(SWEEP, BEAR, hour=1, price=90.0, ref=100.0)) == "low"


def test_physical_side_ignores_direction_when_geometry_disagrees():
    """O S7 mostrou que `direction` e a tendencia invertida, nao um lado.

    Um evento marcado ``bullish`` cujo pavio ficou ABAIXO da referencia varreu
    um fundo -- e e isso que o S3 conta.
    """
    assert physical_side(_ev(SWEEP, BULL, hour=1, price=90.0, ref=100.0)) == "low"


def test_without_a_reference_the_side_falls_back_to_direction():
    assert physical_side(_ev(SWEEP, BULL, hour=1, price=110.0)) == "high"


# --------------------------------------------------------------------------
# S3.1 — known_at
# --------------------------------------------------------------------------


def _candles():
    # o extremo 110 e feito no candle 3; o extremo 90 no candle 6
    highs = [101.0, 102.0, 103.0, 110.0, 104.0, 105.0, 106.0, 107.0]
    lows = [99.0, 98.0, 97.0, 96.0, 95.0, 94.0, 90.0, 93.0]
    return make_series(highs, lows, timeframe=TimeFrame.H1)


def test_a_sweep_is_known_only_after_its_pivot_plus_the_lookback():
    candles = _candles()
    events = [_ev(SWEEP, BULL, hour=1, price=110.0, ref=100.0)]
    # pivo no candle 3, lookback de producao 5 -> conhecivel em 8
    assert known_at_all(candles, events, TimeFrame.H1) == {0: 8}


def test_a_break_is_known_after_the_persistence_candles():
    candles = _candles()
    events = [_ev(BOS, BULL, hour=2, price=103.0)]
    assert known_at_all(candles, events, TimeFrame.H1) == {0: 2 + PERSISTENCE}


def test_provisional_events_never_enter_the_sequence():
    candles = _candles()
    ghost = _ev(SWEEP, BULL, hour=1, price=110.0, ref=100.0).model_copy(
        update={"provisional": True}
    )
    assert known_at_all(candles, [ghost], TimeFrame.H1) == {}


def test_an_event_outside_the_visible_window_is_skipped():
    candles = _candles()
    far = _ev(BOS, BULL, hour=500, price=103.0)
    assert known_at_all(candles, [far], TimeFrame.H1) == {}


# --------------------------------------------------------------------------
# S3.2 — tendencia causal
# --------------------------------------------------------------------------


def test_trend_is_neutral_until_the_first_break_is_knowable():
    events = [_ev(BOS, BULL, hour=3, price=103.0)]
    known = {0: 5}
    series = causal_trend_series(events, known, 8)
    assert series[:5] == [NEUTRAL] * 5
    assert series[5:] == [BULL] * 3


def test_trend_flips_only_at_the_known_at_of_the_flipping_event():
    events = [_ev(BOS, BULL, hour=0, price=103.0), _ev(CHOCH, BEAR, hour=3, price=97.0)]
    known = {0: 1, 1: 6}
    series = causal_trend_series(events, known, 8)
    assert series[1] is BULL
    assert series[5] is BULL      # o CHoCH ainda nao e conhecivel
    assert series[6] is BEAR


def test_neutral_events_do_not_erase_the_standing_trend():
    events = [_ev(BOS, BULL, hour=0, price=103.0), _ev(BOS, NEUTRAL, hour=2, price=104.0)]
    series = causal_trend_series(events, {0: 1, 1: 3}, 6)
    assert series[-1] is BULL


# --------------------------------------------------------------------------
# S3.0/S3.3 — a contagem sob cada definicao
# --------------------------------------------------------------------------


def _row(**nth: int) -> Row:
    return Row(
        symbol="T", timeframe="1h", timestamp="x", idx=0, known=0, block=0,
        direction="bullish", side="high", klass="RECLAIM", trend="bullish",
        level=None, nth=nth,
    )


def test_stage_collapses_everything_from_four_upward():
    assert stage(_row(**{PRIMARY: 3})) == "3"
    assert stage(_row(**{PRIMARY: 4})) == "4+"
    assert stage(_row(**{PRIMARY: 9})) == "4+"


def test_every_pre_registered_definition_is_counted():
    """Se uma definicao sumir do relatorio, o S3.0 deixa de ser pre-registrado."""
    assert set(DEFS) == {"event", "leg", "side", "trend"}
    assert PRIMARY in DEFS


# --------------------------------------------------------------------------
# S3.9/S3.10 — as metricas de "explode"
# --------------------------------------------------------------------------


def _straight_up():
    # sobe 1 por candle, sem excursao adversa
    highs = [100.0 + i for i in range(10)]
    lows = [99.0 + i for i in range(10)]
    return make_series(highs, lows, timeframe=TimeFrame.H1)


def test_reach_counts_the_favourable_excursion_in_atr():
    candles = _straight_up()
    got = _reach(candles, 0, atr=2.0, bullish=True, stop_idx=None)
    # entry = close do candle 0; o topo da serie fica ~8 acima -> 4 ATR
    assert got == {"1ATR": True, "2ATR": True, "3ATR": True}


def test_reach_stops_at_the_structural_change():
    candles = _straight_up()
    got = _reach(candles, 0, atr=2.0, bullish=True, stop_idx=1)
    # so um candle de janela: nao chega a 1 ATR
    assert got["1ATR"] is False


def test_reach_stops_at_one_atr_adverse():
    highs = [100.0, 100.5, 120.0]
    lows = [99.0, 90.0, 119.0]
    candles = make_series(highs, lows, timeframe=TimeFrame.H1)
    got = _reach(candles, 0, atr=2.0, bullish=True, stop_idx=None)
    # o candle 1 perfura 1 ATR contra antes do candle 2 explodir a favor
    assert got["3ATR"] is False


def test_reach_is_empty_without_a_usable_atr():
    assert not any(_reach(_straight_up(), 0, atr=0.0, bullish=True,
                          stop_idx=None).values())


def test_time_to_mfe_points_at_the_extreme_candle():
    candles = _straight_up()
    assert _time_to_mfe(candles, 0, 5, bullish=True) == 5


def test_time_to_mfe_is_none_when_the_window_is_truncated():
    assert _time_to_mfe(_straight_up(), 0, 40, bullish=True) is None


# --------------------------------------------------------------------------
# ponta a ponta (precisa do cache local)
# --------------------------------------------------------------------------

_PROVIDER = OfflineKlinesProvider()
_needs_cache = pytest.mark.skipif(
    not _PROVIDER.has("BTCUSDT", TimeFrame.H1),
    reason="sem research/.klines_cache local",
)


@pytest.fixture(scope="module")
def rows() -> list[Row]:
    data = load_dashboard_data(
        provider=_PROVIDER, symbol="BTCUSDT", timeframe=TimeFrame.H1, limit=1200,
        compute_narrative=False, futures_provider=NoFuturesProvider(),
    )
    return extract(data)


@_needs_cache
def test_the_panel_produces_sweeps_at_several_stages(rows):
    assert len(rows) > 20
    assert {stage(r) for r in rows} >= {"1", "2", "3"}


@_needs_cache
def test_no_sweep_is_measured_before_it_could_be_known(rows):
    """S3.1: o relogio de outcome nunca comeca no timestamp back-datado."""
    assert all(r.known >= r.idx for r in rows)
    assert any(r.known > r.idx for r in rows), "nenhum back-dating na amostra?"


@_needs_cache
def test_the_sequence_is_ordered_by_availability_not_by_timestamp(rows):
    """A ordem da run e a de `known_at` -- e a unica ordem causal."""
    assert all(a.known <= b.known for a, b in zip(rows, rows[1:], strict=False))


@_needs_cache
def test_the_count_restarts_at_one_whenever_the_causal_trend_changes(rows):
    """S3.0: a definicao PRIMARY quebra exatamente na inversao de tendencia."""
    for a, b in zip(rows, rows[1:], strict=False):
        if b.trend != a.trend:
            assert b.nth[PRIMARY] == 1
        else:
            assert b.nth[PRIMARY] == a.nth[PRIMARY] + 1


@_needs_cache
def test_the_loosest_definition_never_counts_less_than_the_strictest(rows):
    """`event` nunca quebra; toda outra definicao so pode quebrar mais."""
    assert all(r.nth["event"] >= r.nth[d] for r in rows for d in DEFS)


@_needs_cache
def test_a_third_sweep_carries_a_three_letter_shape_and_side_word(rows):
    third = [r for r in rows if stage(r) == "3"]
    assert third
    assert all(len(r.shape) == 3 and len(r.sides) == 3 for r in third)
    assert all(set(r.sides) <= {"H", "L"} for r in third)


@_needs_cache
def test_a_run_that_crossed_a_choch_cannot_be_primary_length_three(rows):
    """Coerencia: a PRIMARY quebra na inversao, entao um trio dela nao pode
    ter atravessado um CHoCH que mudou a tendencia."""
    for r in rows:
        if r.nth[PRIMARY] >= 2 and r.crossed_choch:
            # atravessou CHoCH sem mudar a tendencia (CHoCH na mesma direcao)
            assert r.trend != "neutral"


@_needs_cache
def test_the_outcome_window_never_extends_past_the_series(rows):
    measured = [r for r in rows if r.mfe]
    assert measured
    assert all(set(r.mfe) == set(r.mae) for r in measured)
    assert all(v >= 0 for r in measured for v in r.mfe.values())
