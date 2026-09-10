"""Testes da regra causal do join HTF/LTF (`research.hunt_htf_causality`).

Ficam em `research/` e nao em `liquidity_hunter/tests/` de proposito: nada
disto e producao ainda. O `pyproject` aponta a descoberta do pytest para
`liquidity_hunter/tests`, entao rodar exige o caminho explicito:

    poetry run pytest research/test_hunt_htf_causality.py
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from liquidity_hunter.app.liquidity_hunt import LiquidityHuntEngine
from liquidity_hunter.core.domain import (
    MarketDirection,
    MarketStructure,
    StructureEvent,
    TimeFrame,
)
from research.hunt_htf_causality import (
    HTF_PERIOD,
    CausalHuntEngine,
    is_knowable,
)


def _event(
    ts: datetime,
    event: StructureEvent = StructureEvent.BREAK_OF_STRUCTURE,
    direction: MarketDirection = MarketDirection.BULLISH,
    *,
    provisional: bool = False,
) -> MarketStructure:
    return MarketStructure(
        symbol="TESTUSDT",
        timeframe=TimeFrame.H4,
        event=event,
        direction=direction,
        price_level=100.0,
        timestamp=ts,
        provisional=provisional,
    )


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 5, hour, minute, tzinfo=UTC)


H4 = timedelta(hours=4)


# ---------------------------------------------------------------------------
# 3. O join exato
# ---------------------------------------------------------------------------


def test_evento_htf_em_formacao_nao_e_conhecivel() -> None:
    """Um candle H4 aberto as 12:00 nao existe as 13:00."""
    assert not is_knowable(_at(12), H4, _at(13))


def test_evento_htf_e_conhecivel_exatamente_no_fechamento() -> None:
    """A fronteira e inclusiva: as 16:00 o candio das 12:00 fechou.

    Um candle fecha no instante em que o proximo abre, e a abertura do proximo
    e a evidencia de que o anterior terminou. Escolher `<` aqui atrasaria toda
    leitura HTF em um tick sem nenhuma razao de causalidade.
    """
    assert is_knowable(_at(12), H4, _at(16))
    assert not is_knowable(_at(12), H4, _at(15, 59))


def test_periodo_por_timeframe_nao_e_generico() -> None:
    """Cada TF carrega sua duracao real -- um W1 nao e "sete vezes alguma coisa"."""
    assert HTF_PERIOD[TimeFrame.M30] == timedelta(minutes=30)
    assert HTF_PERIOD[TimeFrame.H1] == timedelta(hours=1)
    assert HTF_PERIOD[TimeFrame.H4] == timedelta(hours=4)
    assert HTF_PERIOD[TimeFrame.D1] == timedelta(days=1)
    assert HTF_PERIOD[TimeFrame.W1] == timedelta(weeks=1)


@pytest.mark.parametrize(
    ("ltf", "htf"),
    [
        (TimeFrame.M15, TimeFrame.M30),
        (TimeFrame.H1, TimeFrame.H4),
        (TimeFrame.H4, TimeFrame.D1),
        (TimeFrame.D1, TimeFrame.W1),
    ],
)
def test_cada_par_da_escada_tem_periodo_declarado(ltf: TimeFrame, htf: TimeFrame) -> None:
    from liquidity_hunter.app.dashboard_data import _HIGHER_TIMEFRAME_MAP

    assert _HIGHER_TIMEFRAME_MAP[ltf] is htf
    assert HTF_PERIOD[htf] > timedelta(0)


# ---------------------------------------------------------------------------
# 4. A variante causal contra a legacy
# ---------------------------------------------------------------------------


def test_legacy_le_o_candle_aberto_e_causal_nao() -> None:
    """O caso que a auditoria encontrou, reduzido ao minimo.

    Um BOS bearish num candle H4 aberto as 12:00 (fecha 16:00). As 13:00 o
    LEGACY ja o le e responde `bearish`; o CAUSAL ainda ve so o BOS bullish
    anterior.
    """
    events = [
        _event(_at(4), direction=MarketDirection.BULLISH),
        _event(_at(12), direction=MarketDirection.BEARISH),
    ]
    legacy = LiquidityHuntEngine()
    causal = CausalHuntEngine(htf_period=H4)
    assert legacy._htf_trend_at(events, _at(13), MarketDirection.BULLISH) is (
        MarketDirection.BEARISH
    )
    assert causal._htf_trend_at(events, _at(13), MarketDirection.BULLISH) is (
        MarketDirection.BULLISH
    )
    # ...e as 16:00 os dois concordam de novo.
    assert causal._htf_trend_at(events, _at(16), MarketDirection.BULLISH) is (
        MarketDirection.BEARISH
    )


def test_causal_replica_multiplos_eventos_htf() -> None:
    events = [
        _event(_at(0), direction=MarketDirection.BULLISH),
        _event(_at(4), direction=MarketDirection.BEARISH),
        _event(_at(8), direction=MarketDirection.BULLISH),
    ]
    causal = CausalHuntEngine(htf_period=H4)
    assert causal._htf_trend_at(events, _at(8), MarketDirection.BEARISH) is (
        MarketDirection.BEARISH
    )
    assert causal._htf_trend_at(events, _at(12), MarketDirection.BEARISH) is (
        MarketDirection.BULLISH
    )


def test_causal_preserva_a_reversao_do_choch_failed() -> None:
    """`CHOCH_FAILED` continua revertendo o trend -- a correcao nao toca a semantica."""
    events = [
        _event(_at(0), direction=MarketDirection.BULLISH),
        _event(_at(4), StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH),
        _event(_at(8), StructureEvent.CHOCH_FAILED, MarketDirection.BEARISH),
    ]
    causal = CausalHuntEngine(htf_period=H4)
    # as 12:00 o CHOCH_FAILED (aberto 08:00, fechado 12:00) ja conta: reverte
    # o CHoCH bearish e devolve bullish.
    assert causal._htf_trend_at(events, _at(12), MarketDirection.BEARISH) is (
        MarketDirection.BULLISH
    )
    # as 11:00 ele ainda nao existe: o trend e o do CHoCH bearish.
    assert causal._htf_trend_at(events, _at(11), MarketDirection.BULLISH) is (
        MarketDirection.BEARISH
    )


def test_causal_ignora_provisional_como_a_producao() -> None:
    events = [
        _event(_at(0), direction=MarketDirection.BULLISH),
        _event(_at(4), direction=MarketDirection.BEARISH, provisional=True),
    ]
    causal = CausalHuntEngine(htf_period=H4)
    assert causal._htf_trend_at(events, _at(20), MarketDirection.BEARISH) is (
        MarketDirection.BULLISH
    )


def test_causal_cai_no_fallback_quando_nada_fechou_ainda() -> None:
    """Sem evento conhecivel, vale o escalar -- identico ao comportamento atual."""
    events = [_event(_at(12), direction=MarketDirection.BEARISH)]
    causal = CausalHuntEngine(htf_period=H4)
    assert causal._htf_trend_at(events, _at(13), MarketDirection.BULLISH) is (
        MarketDirection.BULLISH
    )


def test_gap_de_candle_htf_nao_muda_a_regra() -> None:
    """Um buraco na serie HTF nao inventa nem apaga conheciveis.

    A regra olha o evento, e um evento so existe sobre um candle que existiu.
    Faltando o candle das 08:00, o das 04:00 e o das 12:00 continuam valendo
    exatamente nos seus proprios fechamentos.
    """
    events = [
        _event(_at(4), direction=MarketDirection.BULLISH),
        _event(_at(12), direction=MarketDirection.BEARISH),
    ]
    causal = CausalHuntEngine(htf_period=H4)
    assert causal._htf_trend_at(events, _at(9), MarketDirection.BEARISH) is (
        MarketDirection.BULLISH
    )
    assert causal._htf_trend_at(events, _at(16), MarketDirection.BULLISH) is (
        MarketDirection.BEARISH
    )


# ---------------------------------------------------------------------------
# 5. Truncamento: nenhum evento futuro pode mudar classificacao passada
# ---------------------------------------------------------------------------


def _classify(engine: LiquidityHuntEngine, events: list[MarketStructure], at: datetime):
    return engine._htf_trend_at(events, at, MarketDirection.BULLISH)


def test_truncamento_causal_e_estavel() -> None:
    """A propriedade que define causalidade.

    Classificar uma perna em `at` com a serie HTF inteira tem que dar o mesmo
    resultado que classifica-la com a serie truncada em `at`. Se der diferente,
    a classificacao esta lendo o futuro.
    """
    at = _at(13)
    completo = [
        _event(_at(4), direction=MarketDirection.BULLISH),
        _event(_at(12), direction=MarketDirection.BEARISH),
        _event(_at(20), direction=MarketDirection.BULLISH),
    ]
    truncado = [e for e in completo if e.timestamp <= at]
    causal = CausalHuntEngine(htf_period=H4)
    assert _classify(causal, completo, at) is _classify(causal, truncado, at)


def test_truncamento_legacy_FALHA_no_mesmo_caso() -> None:
    """O contraponto obrigatorio: o LEGACY nao tem essa propriedade.

    Este teste passa afirmando um defeito. Ele existe para que a correcao
    tenha uma testemunha: no dia em que producao adotar a regra causal, este
    teste passa a falhar, e essa falha e a prova de que a correcao pegou.
    """
    at = _at(13)
    completo = [
        _event(_at(4), direction=MarketDirection.BULLISH),
        _event(_at(12), direction=MarketDirection.BEARISH),
    ]
    # Truncar em `at` remove o evento das 12:00? Nao -- ele abriu antes de `at`.
    # E esse o ponto: o LEGACY o considera conhecivel embora o candle so feche
    # as 16:00, tres horas depois do instante que ele esta classificando.
    legacy = LiquidityHuntEngine()
    assert _classify(legacy, completo, at) is MarketDirection.BEARISH
    # O observador honesto em `at` -- que so tem candles HTF FECHADOS -- leria:
    fechados = [e for e in completo if is_knowable(e.timestamp, H4, at)]
    assert _classify(legacy, fechados, at) is MarketDirection.BULLISH
    # As duas leituras discordam: o LEGACY antecipa a virada em 3h.
    assert _classify(legacy, completo, at) is not _classify(legacy, fechados, at)
