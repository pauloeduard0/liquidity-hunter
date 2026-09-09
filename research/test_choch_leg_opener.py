"""Fecha a variante generalizada contra a producao.

`choch_leg_opener.detect_stall` e copia verbatim de
`liquidity.structural_stall.detect_structural_stall` a menos do guard do
opener. Toda a medicao da Etapa 4.1 depende disso ser verdade, entao aqui a
copia e prendida contra o original em series reais -- e o invariante da secao 4
(pernas de BOS nao podem mudar) e verificado tanto no objeto quanto na
populacao inteira.
"""

from __future__ import annotations

import datetime as dt

import pytest
from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import MarketDirection, StructureEvent, TimeFrame
from liquidity_hunter.liquidity.structural_stall import detect_structural_stall
from research.choch_leg_opener import (
    BOS_ONLY,
    BOS_OR_CHOCH,
    INVARIANT_FIELDS,
    ZEC_CASE,
    collect,
    compare,
    detect_stall,
    legs_of_run,
    trend_at,
)
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series

PANEL = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ZECUSDT"]
TFS = [TimeFrame.M15, TimeFrame.H1, TimeFrame.H4, TimeFrame.D1]
COMBOS = [
    (symbol, tf)
    for symbol in PANEL
    for tf in TFS
    if (CACHE_DIR / f"{symbol}_{tf.value}.json").exists()
]


def run_for(symbol: str, timeframe: TimeFrame, window: int = 0):
    series = load_series(symbol, timeframe)
    end = len(series) - window * LIMIT
    return dd._run_internal_structure(
        provider=SliceProvider(series[end - LIMIT - BUFFER : end]),
        symbol=symbol,
        timeframe=timeframe,
        limit=LIMIT,
        confluence_filter=True,
    )


@pytest.mark.parametrize(("symbol", "timeframe"), COMBOS)
def test_bos_only_e_indistinguivel_da_producao(symbol: str, timeframe: TimeFrame) -> None:
    """Com `openers=BOS_ONLY` a copia devolve exatamente o objeto da producao.

    Avaliado sobre PREFIXOS: uma janela so tem um estado final, e comparar so
    ele seria quase sempre `None == None`. Cada prefixo e uma pergunta
    independente, e as duas funcoes tem de concordar em todas.
    """
    run = run_for(symbol, timeframe)
    compared = 0
    for cut in range(len(run.candles) - 600, len(run.candles), 11):
        candles = run.candles[: cut + 1]
        events = [e for e in run.events if e.timestamp <= candles[-1].timestamp]
        mine = detect_stall(candles, events, openers=BOS_ONLY)
        theirs = detect_structural_stall(candles, events)
        assert mine == theirs, f"{symbol} {timeframe.value} em {candles[-1].timestamp}"
        compared += 1
        if theirs is not None:
            compared += 100
    assert compared > 0


def test_a_copia_compara_stalls_de_verdade() -> None:
    """Guarda contra o teste acima virar `None == None` em todos os prefixos."""
    hits = 0
    for symbol, timeframe in COMBOS:
        run = run_for(symbol, timeframe)
        for cut in range(len(run.candles) - 600, len(run.candles), 11):
            candles = run.candles[: cut + 1]
            events = [e for e in run.events if e.timestamp <= candles[-1].timestamp]
            hits += detect_structural_stall(candles, events) is not None
    assert hits >= 20, f"apenas {hits} prefixos com stall -- teste sem poder"


@pytest.mark.parametrize(("symbol", "timeframe"), COMBOS)
def test_abrir_o_guard_nao_mexe_em_perna_de_bos(symbol: str, timeframe: TimeFrame) -> None:
    """O invariante da secao 4, perna a perna, nos campos exigidos."""
    run = run_for(symbol, timeframe)
    current = {
        leg.opener_timestamp: leg
        for leg in legs_of_run(run, symbol, timeframe, 0, BOS_ONLY)
    }
    for leg in legs_of_run(run, symbol, timeframe, 0, BOS_OR_CHOCH):
        if leg.opener_event != StructureEvent.BREAK_OF_STRUCTURE.value:
            continue
        old = current.get(leg.opener_timestamp)
        assert old is not None, f"perna de BOS {leg.opener_timestamp} sumiu"
        for name in INVARIANT_FIELDS:
            assert getattr(old, name) == getattr(leg, name), name


def test_invariante_na_populacao_do_painel() -> None:
    """O mesmo invariante na coleta inteira, que e o numero que o relatorio cita."""
    current, proposed = collect(PANEL, TFS, windows=2)
    result = compare(current, proposed)
    assert result["bos_legs_changed"] == []
    assert result["bos_legs_identical"] == result["legs_current"]
    assert result["legs_proposed"] > result["legs_current"]


@pytest.mark.parametrize(("symbol", "timeframe"), COMBOS)
def test_causalidade_por_truncamento(symbol: str, timeframe: TimeFrame) -> None:
    """O resultado em T nao muda quando a serie e truncada em T.

    Vale especialmente para pernas abertas por CHoCH: nenhum conhecimento do
    proximo BOS/CHoCH pode participar do gatilho.
    """
    run = run_for(symbol, timeframe)
    checked = 0
    for cut in range(len(run.candles) - 400, len(run.candles), 37):
        candles = run.candles[: cut + 1]
        events = [e for e in run.events if e.timestamp <= candles[-1].timestamp]
        stall = detect_stall(candles, events, openers=BOS_OR_CHOCH)
        if stall is None:
            continue
        checked += 1
        # O gatilho foi datado em `stale_since`. Recalcular a resposta com a
        # serie cortada NAQUELE candle -- sem nada do que veio depois, nem
        # candle nem evento -- tem de dar o mesmo objeto. Se qualquer coisa
        # posterior participasse do gatilho, aqui divergiria.
        by_ts = {c.timestamp: i for i, c in enumerate(run.candles)}
        at = by_ts[stall.stale_since]
        earlier = run.candles[: at + 1]
        earlier_events = [
            e for e in run.events if e.timestamp <= earlier[-1].timestamp
        ]
        replayed = detect_stall(earlier, earlier_events, openers=BOS_OR_CHOCH)
        assert replayed is not None, f"o stall de {stall.stale_since} nao existia ali"
        assert replayed.stale_since == stall.stale_since
        assert replayed.last_advance_timestamp == stall.last_advance_timestamp
        assert replayed.bars_since_advance == stall.bars_since_advance
        assert replayed.retracement_atr == stall.retracement_atr
        assert replayed.frozen_atr_pct == stall.frozen_atr_pct
        assert replayed.leg_extreme_price == stall.leg_extreme_price
    if checked == 0:
        pytest.skip(f"{symbol} {timeframe.value}: nenhum prefixo com stall")


def test_a_causalidade_foi_testada_em_algum_lugar() -> None:
    """Guarda: pelo menos um combo do painel tem stall para truncar."""
    total = 0
    for symbol, timeframe in COMBOS:
        run = run_for(symbol, timeframe)
        for cut in range(len(run.candles) - 400, len(run.candles), 37):
            candles = run.candles[: cut + 1]
            events = [e for e in run.events if e.timestamp <= candles[-1].timestamp]
            total += detect_stall(candles, events, openers=BOS_OR_CHOCH) is not None
    assert total >= 5


def test_choch_failed_nunca_e_opener() -> None:
    """A secao 8: o `✕` nao abre perna, mas fecha a que estava aberta."""
    assert StructureEvent.CHOCH_FAILED not in BOS_OR_CHOCH
    run = run_for(*ZEC_CASE[:2])
    template = next(
        e for e in run.events if e.event is StructureEvent.BREAK_OF_STRUCTURE
    )
    failed = template.model_copy(
        update={"event": StructureEvent.CHOCH_FAILED, "provisional": False}
    )
    # Como ultimo advance, um `✕` nao produz stall nenhum sob o guard proposto.
    assert (
        detect_stall(run.candles, [failed], openers=BOS_OR_CHOCH) is None
    )
    # E ele conta como advance na leitura de tendencia (direcao invertida).
    bullish_failed = failed.model_copy(update={"direction": MarketDirection.BULLISH})
    assert (
        trend_at([bullish_failed], bullish_failed.timestamp) is MarketDirection.BEARISH
    )


def test_direcao_da_perna_e_a_direcao_do_choch() -> None:
    """Secao 9: `direction` da perna == tendencia que o opener deixou."""
    for symbol, timeframe in COMBOS:
        run = run_for(symbol, timeframe)
        for leg in legs_of_run(run, symbol, timeframe, 0, BOS_OR_CHOCH):
            if leg.trend_after is None:
                continue
            assert leg.trend_after == leg.direction, (
                f"{symbol} {timeframe.value} {leg.opener_timestamp}"
            )


def test_o_caso_zec_d1() -> None:
    """Secao 7: o CHoCH bearish de 2026-06-04 fica STALE em 2026-07-26."""
    symbol, timeframe, opener_ts = ZEC_CASE
    run = run_for(symbol, timeframe)
    # Hoje: invisivel.
    assert detect_stall(run.candles, run.events, openers=BOS_ONLY) is None
    leg = next(
        leg
        for leg in legs_of_run(run, symbol, timeframe, 0, BOS_OR_CHOCH)
        if leg.opener_timestamp == opener_ts
    )
    assert leg.opener_event == StructureEvent.CHANGE_OF_CHARACTER.value
    assert leg.direction == MarketDirection.BEARISH.value
    assert leg.stale_since is not None
    assert dt.datetime.fromisoformat(leg.stale_since).date() == dt.date(2026, 7, 26)
    assert leg.bars_since_advance == 52
    assert leg.retracement_atr == pytest.approx(6.8, abs=0.1)
    # E o STALE nao foi desmentido: nenhum BOS bearish veio depois.
    assert leg.outcome != "resumed"
