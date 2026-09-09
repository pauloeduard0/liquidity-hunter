"""Fecha a auditoria de latencia contra a producao.

O modulo copia duas aritmeticas da producao (a do stall e a leitura de
tendencia do Tide) e data fases a mao. Se qualquer uma dessas copias divergir
do original, todo numero do relatorio vira ficcao -- entao cada uma e presa
aqui contra a fonte, em series reais.
"""

from __future__ import annotations

import datetime as dt

import pytest
from liquidity_hunter.core.domain import MarketDirection, StructureEvent, TimeFrame
from liquidity_hunter.liquidity.structural_stall import detect_structural_stall
from research.range_choch import CACHE_DIR, load_series
from research.zec_d1_structure_lag import (
    ADVANCES,
    CASE_SYMBOL,
    CASE_TIMEFRAME,
    CRASH_CANDLE,
    D1_SYMBOLS,
    SWING_LOOKBACK,
    atr_at,
    counterfactual_stall,
    decompose,
    first_shock_against,
    index_by_timestamp,
    production_run,
    snapshots,
    trend_after,
)

AVAILABLE = [
    s for s in D1_SYMBOLS if (CACHE_DIR / f"{s}_{TimeFrame.D1.value}.json").exists()
]


@pytest.fixture(scope="module")
def case():
    return production_run(CASE_SYMBOL, CASE_TIMEFRAME)


@pytest.mark.parametrize("symbol", AVAILABLE)
def test_counterfactual_stall_reproduz_a_producao(symbol: str) -> None:
    """Com o opener que a producao escolheria, a copia da aritmetica coincide.

    `detect_structural_stall` decide o opener sozinho (ultimo advance, e so
    BOS) e so responde sobre o AGORA. Para comparar as duas com poder de
    discriminacao, a producao e chamada sobre PREFIXOS da janela: cada prefixo
    em que ela devolve um stall e um caso, e a copia tem de acertar o mesmo
    candle, as mesmas barras e a mesma retracao.

    Sem os prefixos o teste seria vazio -- em 2026-09-09 nenhum dos seis
    simbolos D1 tem stall vigente.
    """
    run = production_run(symbol, TimeFrame.D1)
    by_ts = index_by_timestamp(run.candles)
    compared = 0
    for cut in range(len(run.candles) - 900, len(run.candles), 5):
        candles = run.candles[: cut + 1]
        events = [e for e in run.events if e.timestamp <= candles[-1].timestamp]
        stall = detect_structural_stall(candles, events)
        if stall is None:
            continue
        opener = next(
            e
            for e in events
            if e.timestamp == stall.last_advance_timestamp
            and e.event is StructureEvent.BREAK_OF_STRUCTURE
        )
        trigger = counterfactual_stall(candles, opener, by_ts[opener.timestamp])
        assert trigger is not None
        index, bars, retracement = trigger
        assert candles[index].timestamp == stall.stale_since
        assert bars == stall.bars_since_advance
        assert retracement == pytest.approx(stall.retracement_atr)
        compared += 1
    if compared == 0:
        # Nem todo simbolo passa por um stall na janela; o que nao pode
        # acontecer e divergir quando passa. `test_..._compara_alguma_coisa`
        # garante que a suite nao vira toda skip.
        pytest.skip(f"{symbol}: nenhum prefixo da janela produziu stall")


def test_o_pinning_do_stall_compara_alguma_coisa() -> None:
    """Guarda contra a suite inteira virar skip: o caso principal tem stalls."""
    run = production_run(CASE_SYMBOL, CASE_TIMEFRAME)
    hits = sum(
        detect_structural_stall(
            run.candles[: cut + 1],
            [e for e in run.events if e.timestamp <= run.candles[cut].timestamp],
        )
        is not None
        for cut in range(len(run.candles) - 900, len(run.candles), 5)
    )
    assert hits >= 5


def test_trend_after_reproduz_o_final_trend_da_producao() -> None:
    """A leitura de tendencia do Tide == a do detector, em prefixos reais.

    Os snapshots da queda de 2026-06 atravessam bullish -> bearish, entao o
    teste discrimina em vez de so confirmar um viés unico.
    """
    series = load_series(CASE_SYMBOL, CASE_TIMEFRAME)
    seen = set()
    for snapshot in snapshots(CASE_SYMBOL, CASE_TIMEFRAME, series, CRASH_CANDLE):
        seen.add(snapshot.trend)
    assert seen == {"bullish", "bearish"}, "os snapshots precisam cruzar a virada"

    run = production_run(CASE_SYMBOL, CASE_TIMEFRAME)
    assert trend_after(run.events) is run.trend


def test_choch_failed_inverte_na_leitura_de_tendencia() -> None:
    """A direcao de um `✕` e a do CHoCH que falhou -- a tendencia e a oposta."""
    run = production_run(CASE_SYMBOL, CASE_TIMEFRAME)
    template = next(e for e in run.events if e.event in ADVANCES)
    failed = template.model_copy(
        update={
            "event": StructureEvent.CHOCH_FAILED,
            "direction": MarketDirection.BULLISH,
            "provisional": False,
        }
    )
    assert trend_after([failed]) is MarketDirection.BEARISH


def test_fases_da_decomposicao_sao_ordenadas_e_causais(case) -> None:
    """Nenhuma fase pode acontecer depois da seguinte, nem a emissao antes do evento."""
    by_ts = index_by_timestamp(case.candles)
    checked = 0
    for event in case.events:
        if event.provisional or event.event not in ADVANCES:
            continue
        d = decompose(event, case.candles, by_ts, None)
        if d.ref_formed_index is None:
            continue
        checked += 1
        assert d.ref_confirmed_index == min(
            d.ref_formed_index + SWING_LOOKBACK, len(case.candles) - 1
        )
        if d.close_break_index is not None:
            assert d.close_break_index > d.ref_formed_index
            assert d.event_index >= d.close_break_index, (
                f"{event.timestamp}: evento datado ANTES do close-break"
            )
        if d.first_touch_index is not None and d.close_break_index is not None:
            assert d.first_touch_index <= d.close_break_index
    assert checked >= 5


def test_atr_e_shock_sao_causais(case) -> None:
    """Truncar o futuro nao muda nem o ATR nem o primeiro shock ja detectado."""
    candles = case.candles
    cut = len(candles) - 40
    assert atr_at(candles, cut) == atr_at(candles[: cut + 1], cut)
    full = first_shock_against(candles, cut - 60, cut, bearish_move=True)
    partial = first_shock_against(candles[: cut + 1], cut - 60, cut, bearish_move=True)
    assert full == partial


def test_o_caso_zec_e_o_que_o_relatorio_afirma(case) -> None:
    """Os fatos citados nas conclusoes, presos ao stream real."""
    by_ts = index_by_timestamp(case.candles)
    advances = {
        (e.timestamp.date().isoformat(), e.event.value, e.direction.value): e
        for e in case.events
        if not e.provisional and e.event in ADVANCES
    }
    bear = advances[("2026-06-04", "change_of_character", "bearish")]
    bull = advances[("2026-08-19", "change_of_character", "bullish")]
    # A referencia bearish e o `higher_low` de 2026-05-16, ~29% abaixo do topo.
    assert bear.reference_price_level == pytest.approx(486.0)
    assert bear.reference_timestamp.date() == dt.date(2026, 5, 16)
    # Entre as duas viradas o stream nao tem UM advance -- a perna bearish
    # nunca avancou depois de nascer.
    between = [
        e
        for e in case.events
        if not e.provisional
        and e.event in ADVANCES
        and bear.timestamp < e.timestamp < bull.timestamp
    ]
    assert between == []
    # E por isso o stall nao pode ve-la: o ultimo advance e um CHoCH, e o guard
    # do opener so aceita BOS.
    dead_leg = case.candles[by_ts[bull.timestamp]].timestamp - bear.timestamp
    assert dead_leg.days >= 70
    trigger = counterfactual_stall(case.candles, bear, by_ts[bear.timestamp])
    assert trigger is not None
    assert case.candles[trigger[0]].timestamp.date() == dt.date(2026, 7, 26)


def test_a_maquina_ainda_lia_bullish_no_dia_seguinte_ao_crash() -> None:
    """Snapshot +1 (2026-06-05, close 388 vindo de 621): trend ainda bullish."""
    series = load_series(CASE_SYMBOL, CASE_TIMEFRAME)
    by_offset = {
        s.offset: s
        for s in snapshots(CASE_SYMBOL, CASE_TIMEFRAME, series, CRASH_CANDLE)
    }
    assert by_offset[0].trend == "bullish"
    assert by_offset[1].trend == "bullish"
    assert by_offset[1].close < 400
    assert by_offset[10].trend == "bearish"
    # E o stall nao ajuda em nenhum deles.
    assert all(s.stall is None for s in by_offset.values())
