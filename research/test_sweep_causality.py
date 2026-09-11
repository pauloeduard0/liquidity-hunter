"""Testes diagnosticos do S1 — os tres tempos de um sweep.

Dois grupos:

* **helpers do estudo** -- localizacao do pivo, decomposicao do atraso,
  "o nivel ja era conhecivel?". Erram em silencio se nao forem testados, e
  toda a conclusao do S1 depende deles.
* **disponibilidade real** -- roda a pipeline de producao em prefixos de uma
  serie em cache e verifica que o evento (a) nasce depois do candle que o
  data e (b) fica estavel depois de nascer. Sao testes de *caracterizacao*:
  descrevem o comportamento atual, nao afirmam que ele esta certo. Pulam
  quando nao ha cache local.
"""

from __future__ import annotations

import pytest
from liquidity_hunter.core.domain import (
    MarketDirection,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.liquidity.detectors import SwingStructureDetector
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle, make_series
from research._offline import OfflineKlinesProvider
from research.sweep_audit import Run
from research.sweep_causality import (
    Times,
    _find_extreme_index,
    lookback_of,
    measure_times,
    replay,
)

BULL = MarketDirection.BULLISH
BEAR = MarketDirection.BEARISH


# --------------------------------------------------------------------------
# _find_extreme_index
# --------------------------------------------------------------------------


def _series():
    return make_series([10.0, 12.0, 11.0, 15.0, 11.0], [8.0, 9.0, 5.0, 12.0, 9.0])


def test_finds_the_candle_that_made_the_high_scanning_forward():
    assert _find_extreme_index(_series(), 15.0, 0, high=True, forward=True) == 3


def test_finds_the_candle_that_made_the_low_scanning_backward():
    assert _find_extreme_index(_series(), 5.0, 4, high=False, forward=False) == 2


def test_scanning_backward_stops_at_the_start_index():
    """Um nivel formado DEPOIS do ponto de partida nao e achado para tras."""
    assert _find_extreme_index(_series(), 12.0, 1, high=False, forward=False) is None


def test_a_price_no_candle_ever_printed_has_no_index():
    assert _find_extreme_index(_series(), 99.0, 0, high=True, forward=True) is None


# --------------------------------------------------------------------------
# decomposicao do atraso
# --------------------------------------------------------------------------


def _times(sweep_idx: int, pivot_idx: int | None, level_idx: int | None, lb: int = 5):
    return Times(
        symbol="T", timeframe="1h", direction="bullish",
        sweep_idx=sweep_idx, pivot_idx=pivot_idx, level_idx=level_idx, lookback=lb,
    )


def test_backdating_is_the_gap_between_the_timestamp_and_the_pivot():
    t = _times(sweep_idx=10, pivot_idx=14, level_idx=2)
    assert t.backdate == 4
    assert t.pivot_confirm_idx == 19          # 14 + lookback 5
    assert t.analytic_known_idx - t.sweep_idx == 9


def test_a_sweep_dated_on_its_own_pivot_still_waits_the_lookback():
    """back-dating zero (39,4% dos casos) nao significa atraso zero."""
    t = _times(sweep_idx=10, pivot_idx=10, level_idx=2)
    assert t.backdate == 0
    assert t.analytic_known_idx - t.sweep_idx == 5


def test_level_already_known_when_its_pivot_confirmed_before_the_sweep():
    # nivel formado no candle 2, conhecivel em 2+5=7, sweep em 10 -> ja conhecido
    assert _times(sweep_idx=10, pivot_idx=10, level_idx=2).level_known_before_sweep is True


def test_level_still_in_the_future_when_its_pivot_confirms_after_the_sweep():
    # nivel formado no candle 8, conhecivel em 13, sweep em 10 -> ainda nao
    assert _times(sweep_idx=10, pivot_idx=12, level_idx=8).level_known_before_sweep is False


def test_an_unlocated_level_answers_none_not_false():
    """Nao achar o nivel e ignorancia, nao uma resposta negativa."""
    assert _times(sweep_idx=10, pivot_idx=10, level_idx=None).level_known_before_sweep is None


@pytest.mark.parametrize("tf", [TimeFrame.M15, TimeFrame.H1, TimeFrame.H4])
def test_production_lookback_is_five_on_every_audited_timeframe(tf):
    """Se isto quebrar, toda a aritmetica do S1 muda -- e deliberadamente."""
    assert lookback_of(tf) == 5


# --------------------------------------------------------------------------
# measure_times sobre a saida real do detector
# --------------------------------------------------------------------------


def _sweep_run(timeframe: TimeFrame = TimeFrame.H1) -> Run:
    """A serie da rota de persistencia, passada pelo detector de producao."""
    highs = [150.0, 200.0, 150.0, 210.0, 150.0, 220.0, 150.0]
    lows = [145.0, 145.0, 140.0, 145.0, 130.0, 145.0, 120.0]
    candles = make_series(highs, lows, timeframe=timeframe)
    candles[3] = make_candle(3, highs[3], lows[3], close=205.0, timeframe=timeframe)
    candles[5] = make_candle(5, highs[5], lows[5], close=215.0, timeframe=timeframe)
    events = SwingStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)
    return Run(
        symbol="BTCUSDT", timeframe=timeframe, candles=candles, events=events,
        zones=[], poi=[], grabs=[], contexts=[], atr=10.0,
    )


def test_the_three_times_are_resolved_from_a_real_detector_event():
    run = _sweep_run()
    rows = measure_times(run)
    assert len(rows) == 1
    t = rows[0]
    sweep = next(
        e for e in run.events if e.event is StructureEvent.LIQUIDITY_SWEEP
    )
    # o timestamp e o candle que atravessou; o pivo e o extremo `price_level`
    assert run.candles[t.sweep_idx].timestamp == sweep.timestamp
    assert run.candles[t.pivot_idx].low == sweep.price_level
    assert run.candles[t.level_idx].low == sweep.reference_price_level
    # e os tres tempos sao distintos do known_at analitico
    assert t.analytic_known_idx > t.sweep_idx


@pytest.mark.parametrize("tf", [TimeFrame.M15, TimeFrame.H1, TimeFrame.H4])
def test_measure_times_reads_the_lookback_of_the_series_timeframe(tf):
    rows = measure_times(_sweep_run(tf))
    assert rows and rows[0].lookback == lookback_of(tf)


def test_provisional_events_are_excluded_from_the_times_panel():
    run = _sweep_run()
    ghost = next(
        e for e in run.events if e.event is StructureEvent.LIQUIDITY_SWEEP
    ).model_copy(update={"provisional": True})
    run.events = [ghost]
    assert measure_times(run) == []


# --------------------------------------------------------------------------
# disponibilidade real (precisa do cache local)
# --------------------------------------------------------------------------

_PROVIDER = OfflineKlinesProvider()
_needs_cache = pytest.mark.skipif(
    not _PROVIDER.has("BTCUSDT", TimeFrame.H1),
    reason="sem research/.klines_cache local",
)


@_needs_cache
def test_a_sweep_is_born_after_the_candle_it_is_dated_on():
    """S1.5, caracterizado: nenhum sweep existe no candle que o data."""
    full, lives, _opened = replay(_PROVIDER, "BTCUSDT", TimeFrame.H1, 400, step=2)
    index_of = {c.timestamp: i for i, c in enumerate(full.candles)}
    final = {
        (e.direction, e.price_level)
        for e in full.events
        if e.event is StructureEvent.LIQUIDITY_SWEEP and not e.provisional
    }
    checked = 0
    for ident, life in lives.items():
        if ident not in final:
            continue
        i = index_of.get(min(life.stamps))
        # so os que nasceram dentro da janela de replay tem nascimento medido
        if i is None or life.first_cut <= 400 // 2:
            continue
        assert life.first_cut > i, f"{ident} nasceu no proprio candle datado"
        checked += 1
    assert checked > 0, "a janela de replay nao cobriu nenhum nascimento"


@_needs_cache
def test_a_sweep_that_survives_keeps_one_timestamp_and_one_reference():
    """S1.3: depois de nascer, o evento sobrevivente nao e reescrito.

    Caracterizacao. Se quebrar, o sweep passou a repintar metadata e o
    modelo B/C do S1.7 muda de peso.
    """
    full, lives, _opened = replay(_PROVIDER, "BTCUSDT", TimeFrame.H1, 400, step=2)
    final = {
        (e.direction, e.price_level)
        for e in full.events
        if e.event is StructureEvent.LIQUIDITY_SWEEP and not e.provisional
    }
    survivors = [life for ident, life in lives.items() if ident in final]
    assert survivors
    assert all(len(life.stamps) == 1 for life in survivors)
    assert all(len(life.events) == 1 for life in survivors)


@_needs_cache
def test_repaint_is_counted_as_an_identity_that_never_reaches_the_final_series():
    """Um sweep visto ao vivo e ausente no fim e repaint -- e nao vem marcado.

    Nao afirma uma taxa (ela varia por serie); afirma que o estudo *conta*
    esse caso, que e o que o S0 mediu em 2,91%.
    """
    full, lives, _opened = replay(_PROVIDER, "BTCUSDT", TimeFrame.H1, 400, step=2)
    final = {
        (e.direction, e.price_level)
        for e in full.events
        if e.event is StructureEvent.LIQUIDITY_SWEEP and not e.provisional
    }
    assert set(lives) >= final, "o replay tem que ver tudo o que a serie final tem"
    # e nenhum evento final carrega marca de provisional: o repaint e invisivel
    assert all(
        not e.provisional
        for e in full.events
        if e.event is StructureEvent.LIQUIDITY_SWEEP
    )
