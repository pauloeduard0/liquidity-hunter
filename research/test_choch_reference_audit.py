"""Testes da Etapa 4.4.

O risco central desta etapa e diferente das anteriores: ela LE o estado
interno do detector por `sys.settrace`. Dois modos de falha silenciosa
existem e cada um tem teste aqui -- o trace alterar o resultado da producao
(nao pode), e o trace apontar para a linha errada e colher retrato nenhum
(tem de quebrar, nao devolver `None`).
"""

from __future__ import annotations

import sys

import pytest
from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import MarketDirection, StructureEvent, TimeFrame
from research.choch_reference_audit import (
    CLASSIFY_SPAN,
    OUTCOME_WINDOW,
    ZEC_BEARISH,
    ZEC_BULLISH,
    assert_trace_line,
    classify_reference,
    collect,
    first_close_beyond,
    persistence_of,
    reference_slots,
    spearman,
    split_holdout,
    traced_run,
    zec_timeline,
)
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series

PANEL = [
    ("BTCUSDT", TimeFrame.H1),
    ("ETHUSDT", TimeFrame.H4),
    ("SOLUSDT", TimeFrame.M15),
    ("ZECUSDT", TimeFrame.D1),
]


def available():
    return [
        (symbol, timeframe)
        for symbol, timeframe in PANEL
        if (CACHE_DIR / f"{symbol}_{timeframe.value}.json").exists()
    ]


# --------------------------------------------------------------------------
# o trace nao pode mudar nada
# --------------------------------------------------------------------------


def test_a_linha_tracada_e_a_cabeca_do_laco():
    """Se o detector for editado, a medicao tem de PARAR, nao esvaziar."""
    assert_trace_line()


def test_o_trace_nao_altera_o_resultado_da_producao():
    """`traced_run` tem de devolver o mesmo stream que `_run_internal_structure`.

    Este e o teste que autoriza a etapa inteira: se observar mudasse o
    observado, todo numero medido aqui seria sobre outro detector.
    """
    combos = available()
    if not combos:
        pytest.skip("sem cache")
    compared = 0
    for symbol, timeframe in combos:
        series = load_series(symbol, timeframe)
        end = len(series)
        clean = dd._run_internal_structure(
            provider=SliceProvider(list(series[end - LIMIT - BUFFER : end])),
            symbol=symbol,
            timeframe=timeframe,
            limit=LIMIT,
            confluence_filter=True,
        )
        run, snapshots = traced_run(symbol, timeframe, series, end)
        assert len(run.candles) == len(clean.candles)
        assert len(run.events) == len(clean.events)
        for mine, theirs in zip(run.events, clean.events, strict=True):
            assert mine.timestamp == theirs.timestamp
            assert mine.event is theirs.event
            assert mine.direction is theirs.direction
            assert mine.price_level == theirs.price_level
            assert mine.reference_price_level == theirs.reference_price_level
            assert mine.provisional == theirs.provisional
        assert snapshots, "o trace nao colheu retrato nenhum"
        assert run.trend == clean.trend
        compared += 1
    assert compared >= 2


def test_o_trace_e_desinstalado_no_fim():
    """Um `settrace` esquecido deixaria o resto da suite 40x mais lento."""
    combos = available()
    if not combos:
        pytest.skip("sem cache")
    symbol, timeframe = combos[0]
    series = load_series(symbol, timeframe)
    before = sys.gettrace()
    traced_run(symbol, timeframe, series, len(series))
    assert sys.gettrace() is before


def test_o_retrato_tem_os_slots_reais_do_detector():
    """Se um slot deixar de existir, ele vira `<ausente>` e some da tabela --
    silenciosamente. Este teste exige que os slots principais estejam la."""
    combos = available()
    if not combos:
        pytest.skip("sem cache")
    symbol, timeframe = combos[0]
    series = load_series(symbol, timeframe)
    _run, snapshots = traced_run(symbol, timeframe, series, len(series))
    for key in (
        "validated_choch_high",
        "validated_choch_low",
        "active_high",
        "active_low",
        "candidate_choch_high",
        "candidate_choch_low",
        "choch_origin_high",
        "choch_origin_low",
        "trend",
        "pending_bos",
    ):
        assert any(
            snapshot.state.get(key) != "<ausente>" for snapshot in snapshots
        ), f"o slot {key} nunca apareceu no retrato"


# --------------------------------------------------------------------------
# a nomeacao da origem
# --------------------------------------------------------------------------


def test_a_origem_da_referencia_e_reconhecida_na_maioria_dos_casos():
    """`desconhecida` e uma resposta honesta, mas se for a maioria a leitura
    do estado interno nao esta funcionando e a secao 9 nao vale nada."""
    combos = available()
    if not combos:
        pytest.skip("sem cache")
    total = unknown = 0
    for symbol, timeframe in combos:
        series = load_series(symbol, timeframe)
        run, snapshots = traced_run(symbol, timeframe, series, len(series))
        for event in run.events:
            if event.provisional or event.event is not StructureEvent.CHANGE_OF_CHARACTER:
                continue
            source, offset = classify_reference(snapshots, event)
            assert abs(offset) <= CLASSIFY_SPAN
            total += 1
            if source in ("desconhecida", "sem_retrato"):
                unknown += 1
    assert total >= 10, f"so {total} CHoCH -- amostra insuficiente para o teste"
    assert unknown / total <= 0.25, f"{unknown}/{total} referencias nao reconhecidas"


def test_a_precedencia_dos_slots_e_a_do_codigo():
    """`validated` > `pending_leg_origin` > `choch_origin` > `rearm` > `active`."""

    class Pivot:
        def __init__(self, price):
            self.price = price
            self.timestamp = "t"

    class Pending:
        direction = MarketDirection.BEARISH
        pullback_ref = Pivot(2.0)

    state = {
        "validated_choch_high": Pivot(1.0),
        "pending_bos": Pending(),
        "choch_origin_high": Pivot(3.0),
        "bull_choch_rearm": Pivot(4.0),
        "active_high": Pivot(5.0),
    }
    names = [name for name, _price, _ts in reference_slots(state, bullish_choch=True)]
    assert names == [
        "validated",
        "pending_leg_origin",
        "choch_origin",
        "rearm",
        "active_fallback",
    ]


def test_o_pending_so_conta_do_lado_oposto():
    """Um `pending_bos` bullish nao arma referencia para um CHoCH bullish."""

    class Pivot:
        def __init__(self, price):
            self.price = price
            self.timestamp = "t"

    class Pending:
        direction = MarketDirection.BULLISH
        pullback_ref = Pivot(2.0)

    state = {"pending_bos": Pending(), "active_high": Pivot(5.0)}
    names = [name for name, _p, _t in reference_slots(state, bullish_choch=True)]
    assert "pending_leg_origin" not in names


# --------------------------------------------------------------------------
# a quebra contrafactual
# --------------------------------------------------------------------------


class FakeCandle:
    def __init__(self, close):
        self.close = close
        self.high = close
        self.low = close
        self.timestamp = close


def test_a_quebra_exige_persistencia():
    """Um unico fechamento alem do nivel nao e quebra -- a producao cobra
    `persistence_candles`, e a alternativa tem de pagar o mesmo preco."""
    candles = [FakeCandle(c) for c in (10, 10, 12, 9, 9, 13, 14, 15)]
    # Com persistencia 2, o fechamento isolado em 12 (indice 2) nao serve.
    assert first_close_beyond(candles, 0, len(candles), 11.0, True, 2) == 5
    # Sem persistencia, o primeiro fechamento alem ja vale.
    assert first_close_beyond(candles, 0, len(candles), 11.0, True, 0) == 2
    # Nada alem do nivel.
    assert first_close_beyond(candles, 0, len(candles), 99.0, True, 0) is None


def test_a_quebra_bearish_e_o_espelho():
    candles = [FakeCandle(c) for c in (10, 10, 8, 7, 6)]
    assert first_close_beyond(candles, 0, len(candles), 9.0, False, 1) == 2
    assert first_close_beyond(candles, 0, len(candles), 1.0, False, 1) is None


def test_a_persistencia_e_a_da_producao():
    for timeframe in (TimeFrame.M15, TimeFrame.H1, TimeFrame.H4, TimeFrame.D1):
        assert persistence_of(timeframe) == dd._INTERNAL_STRUCTURE_PARAMS[timeframe][1]


# --------------------------------------------------------------------------
# estatistica e particao
# --------------------------------------------------------------------------


def test_spearman_conhece_os_extremos():
    crescente = [(float(i), float(i)) for i in range(20)]
    assert spearman(crescente) == pytest.approx(1.0)
    decrescente = [(float(i), float(-i)) for i in range(20)]
    assert spearman(decrescente) == pytest.approx(-1.0)
    assert spearman([(1.0, 1.0)] * 3) is None
    assert spearman([(1.0, 2.0)] * 20) is None


def test_holdout_e_por_timeframe_e_nao_vaza():
    cases = collect(["BTCUSDT", "ZECUSDT"], windows=1)
    if len(cases) < 20:
        pytest.skip("amostra pequena demais")
    discovery, holdout = split_holdout(cases)
    assert len(discovery) + len(holdout) == len(cases)
    for timeframe in {case.timeframe for case in cases}:
        d = [c.timestamp for c in discovery if c.timeframe == timeframe]
        h = [c.timestamp for c in holdout if c.timeframe == timeframe]
        if d and h:
            assert max(d) <= min(h)


def test_a_janela_de_desfecho_e_finita():
    assert 0 < OUTCOME_WINDOW <= 200


# --------------------------------------------------------------------------
# o caso obrigatorio
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case_key", [ZEC_BEARISH, ZEC_BULLISH])
def test_as_duas_timelines_do_zec_existem(case_key):
    if not (CACHE_DIR / "ZECUSDT_1d.json").exists():
        pytest.skip("sem cache do ZEC")
    timeline = zec_timeline(case_key)
    if "erro" in timeline:
        pytest.skip(timeline["erro"])
    case = timeline["case"]
    assert case is not None
    assert case["reference_price"] is not None
    assert case["reference_source"] not in ("desconhecida", "sem_retrato")
    assert case["choch_lag_bars"] is not None
    assert timeline["evolucao"], "a evolucao da referencia saiu vazia"


def test_o_zec_bearish_e_o_que_a_auditoria_afirma():
    """A referencia do CHoCH de 04/06 e um nivel estrutural distante do topo."""
    if not (CACHE_DIR / "ZECUSDT_1d.json").exists():
        pytest.skip("sem cache do ZEC")
    timeline = zec_timeline(ZEC_BEARISH)
    if "erro" in timeline:
        pytest.skip(timeline["erro"])
    case = timeline["case"]
    assert case["direction"] == MarketDirection.BEARISH.value
    # A auditoria original mediu a referencia 486.00 bem abaixo do topo.
    assert case["reference_to_extreme_atr"] > 5.0
    assert case["move_completed_at_choch_pct"] > 40.0
