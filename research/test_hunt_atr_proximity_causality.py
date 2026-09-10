"""Testes de `research.hunt_atr_proximity_causality`.

A afirmacao central desta etapa nao e estatistica, e estrutural: a proximidade
so alimenta o estado vivo, e o historico nao a consulta. Um teste que apenas
comparasse numeros nunca pegaria uma regressao ali -- por isso o primeiro bloco
prende a **cadeia**: se um dia `build_history` passar a montar targets, o teste
quebra e a pesquisa toda tem de ser refeita.

O segundo bloco e sobre a propriedade que da nome a etapa. O causal tem de ser
invariante a candles futuros; o legacy **nao e**, e isso e afirmado por um teste
proprio -- um defeito com testemunha, como em H1.

Rodar:
    poetry run pytest research/test_hunt_atr_proximity_causality.py
"""

from __future__ import annotations

import inspect
from datetime import timedelta

import pytest
from liquidity_hunter.app.liquidity_hunt import LiquidityHuntEngine
from liquidity_hunter.core.domain import (
    Candle,
    LiquiditySide,
    LiquidityZone,
    LiquidityZoneType,
    TimeFrame,
)
from research.hunt_atr_proximity_causality import (
    PROXIMITY_PROBES,
    causal_proximity,
    eligible_targets,
    history_invariance,
    legacy_proximity,
)
from research.test_hunt_score_redundancy import BASE, _data, _hunt_snapshot

ATR = 2.0


def _c(
    i: int, high: float, low: float, close: float = 100.0, open_: float = 100.0
) -> Candle:
    return Candle(
        symbol="TESTUSDT",
        timeframe=TimeFrame.M15,
        timestamp=BASE + timedelta(minutes=15 * i),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        taker_buy_volume=50.0,
    )


def _calm(i: int) -> Candle:
    """Candle de amplitude pequena: 1% de true range."""
    return _c(i, 100.5, 99.5)


def _wild(i: int) -> Candle:
    """Candle de amplitude grande: ~10% de true range."""
    return _c(i, 105.0, 95.0)


def _zone(low: float, high: float, formed: int = 0) -> LiquidityZone:
    return LiquidityZone(
        symbol="TESTUSDT",
        zone_type=LiquidityZoneType.EQUAL_HIGHS,
        price_low=low,
        price_high=high,
        timeframe=TimeFrame.M15,
        side=LiquiditySide.BUY_SIDE,
        formed_at=BASE + timedelta(minutes=15 * formed),
    )


# ---------------------------------------------------------------------------
# 1. A cadeia: quem usa proximidade
# ---------------------------------------------------------------------------


def test_apenas_o_build_vivo_usa_proximidade() -> None:
    """A afirmacao estrutural da etapa, presa por teste.

    Se `build_history` passar a consultar proximidade um dia, todo o resultado
    da H4 deixa de valer -- e este teste quebra antes de alguem reusar a
    conclusao.
    """
    vivo = inspect.getsource(LiquidityHuntEngine.build)
    assert "_effective_proximity" in vivo
    for metodo in ("build_history", "build_continuation_history"):
        corpo = inspect.getsource(getattr(LiquidityHuntEngine, metodo))
        assert "_effective_proximity" not in corpo
        assert "_zone_targets" not in corpo
        assert "_band_targets" not in corpo


def test_targets_so_existem_no_estado_vivo() -> None:
    data = _hunt_snapshot()
    engine = LiquidityHuntEngine(proximity_atr=ATR)
    assert hasattr(engine.build(data), "targets")
    for episodio in engine.build_history(data):
        assert not hasattr(episodio, "targets")


def test_historico_e_invariante_a_proximidade() -> None:
    """A sonda do painel, em miniatura: 4 valores de `proximity_atr`, um stream.

    A sonda inclui 100.0, que tornaria todo pool do universo "proximo". Se o
    historico reagisse a proximidade em qualquer grau, esse valor separaria os
    streams.
    """
    resultados = history_invariance(_hunt_snapshot())
    assert set(resultados) == {str(p) for p in PROXIMITY_PROBES}
    assert all(r["identico"] for r in resultados.values())


def test_a_sonda_de_proximidade_realmente_mexe_em_alguma_coisa() -> None:
    """Controle negativo: uma sonda que nao muda nada nao prova nada.

    Sem isto, `test_historico_e_invariante_a_proximidade` passaria mesmo se
    `proximity_atr` fosse ignorado em todo lugar -- inclusive no vivo -- e a
    conclusao da etapa seria um artefato.
    """
    candles = [_calm(i) for i in range(50)]
    estreito = LiquidityHuntEngine(proximity_atr=0.5)._effective_proximity(candles)
    largo = LiquidityHuntEngine(proximity_atr=100.0)._effective_proximity(candles)
    assert largo > estreito > 0


# ---------------------------------------------------------------------------
# 2. Causalidade da formula
# ---------------------------------------------------------------------------


def test_causal_ignora_candles_posteriores() -> None:
    """A propriedade que define a variante causal."""
    calmos = [_calm(i) for i in range(30)]
    depois = calmos + [_wild(i) for i in range(30, 40)]
    at = calmos[-1].timestamp
    assert causal_proximity(calmos, at, ATR) == pytest.approx(
        causal_proximity(depois, at, ATR)
    )


def test_causal_e_invariante_ao_truncamento() -> None:
    """Teste de truncamento (item 4), sobre a serie inteira e um prefixo dela."""
    serie = [_calm(i) for i in range(30)] + [_wild(i) for i in range(30, 45)]
    for corte in (20, 25, 29):
        at = serie[corte].timestamp
        completo = causal_proximity(serie, at, ATR)
        truncado = causal_proximity(serie[: corte + 1], at, ATR)
        assert completo == pytest.approx(truncado)


def test_legacy_FALHA_o_truncamento() -> None:
    """Este teste passa afirmando um defeito, e existe para dar testemunha a ele.

    O `legacy` e a media sobre a janela inteira: acrescentar volatilidade
    **depois** de T muda o valor que um replay atribuiria a T. E precisamente a
    nao-causalidade que a etapa foi medir -- e a razao de ela nao afetar
    producao e que ninguem replaya `build()` sobre o passado, nao que a
    propriedade se sustente.
    """
    calmos = [_calm(i) for i in range(30)]
    depois = calmos + [_wild(i) for i in range(30, 40)]
    assert legacy_proximity(depois, ATR) > legacy_proximity(calmos, ATR) * 1.5


def test_causal_iguala_legacy_no_ultimo_candle() -> None:
    """No instante vivo as duas leituras sao a mesma coisa -- e por isso o
    `build()` ao vivo e causal."""
    serie = [_calm(i) for i in range(20)] + [_wild(i) for i in range(20, 30)]
    at = serie[-1].timestamp
    assert causal_proximity(serie, at, ATR) == pytest.approx(legacy_proximity(serie, ATR))


# ---------------------------------------------------------------------------
# 3. Bordas da formula
# ---------------------------------------------------------------------------


def test_serie_curta_cai_no_percentual_fixo() -> None:
    engine = LiquidityHuntEngine(proximity_atr=ATR, proximity_pct=0.02)
    assert engine._effective_proximity([]) == 0.02
    assert engine._effective_proximity([_calm(0)]) == 0.02


def test_prefixo_curto_demais_cai_no_percentual_fixo() -> None:
    """No comeco da serie o causal nao tem dois candles: tem de degradar igual."""
    serie = [_calm(i) for i in range(10)]
    assert causal_proximity(serie, serie[0].timestamp, ATR) == pytest.approx(0.02)


def test_amplitude_zero_da_proximidade_zero() -> None:
    """Feed morto pos-deslistagem: high == low == close (ver `strip_dead_tail`)."""
    mortos = [_c(i, 100.0, 100.0) for i in range(10)]
    assert legacy_proximity(mortos, ATR) == pytest.approx(0.0)


def test_gap_na_serie_nao_quebra_a_formula() -> None:
    """Um buraco de tempo nao e tratado: a formula e por par consecutivo.

    Registrado como comportamento, nao como defeito -- o true range entre dois
    candles separados por horas continua sendo calculado como se fossem
    vizinhos, e o resultado e um TR grande, nao um erro.
    """
    a = [_calm(i) for i in range(5)]
    b = [_c(i, 120.0, 118.0, 119.0, open_=119.0) for i in range(100, 105)]
    assert legacy_proximity(a + b, ATR) > legacy_proximity(a, ATR)


@pytest.mark.parametrize(
    "tf,minutos", [(TimeFrame.M15, 15), (TimeFrame.H1, 60), (TimeFrame.H4, 240)]
)
def test_a_formula_nao_depende_do_timeframe(tf: TimeFrame, minutos: int) -> None:
    """O `mean_tr_pct` e adimensional: mesma geometria, mesmo valor em qualquer TF.

    Importa porque o painel separa por timeframe, e uma diferenca observada la
    tem de vir do mercado, nao da aritmetica.
    """
    serie = [
        Candle(
            symbol="TESTUSDT", timeframe=tf,
            timestamp=BASE + timedelta(minutes=minutos * i),
            open=100.0, high=100.5, low=99.5, close=100.0,
            volume=100.0, taker_buy_volume=50.0,
        )
        for i in range(30)
    ]
    assert legacy_proximity(serie, ATR) == pytest.approx(0.02, rel=1e-6)


# ---------------------------------------------------------------------------
# 4. Elegibilidade de target
# ---------------------------------------------------------------------------


def test_target_dentro_e_fora_da_borda() -> None:
    zonas = [_zone(100.9, 101.1), _zone(109.9, 110.1)]
    at = BASE + timedelta(hours=5)
    perto = eligible_targets(zonas, 100.0, 0.02, True, at)
    longe = eligible_targets(zonas, 100.0, 0.20, True, at)
    assert [round(m, 2) for m, _ in perto] == [101.0]
    assert len(longe) == 2


def test_zona_ainda_nao_formada_nao_e_elegivel() -> None:
    """Nao havia nivel ali: incluir seria o mesmo lookahead por outra porta."""
    zonas = [_zone(100.9, 101.1, formed=10)]
    assert eligible_targets(zonas, 100.0, 0.20, True, BASE) == []
    assert eligible_targets(
        zonas, 100.0, 0.20, True, BASE + timedelta(minutes=150)
    ) != []


def test_volatilidade_futura_muda_a_decisao_do_legacy() -> None:
    """O caso obrigatorio do item 18: o futuro decidindo o passado.

    Um pool a 5% de distancia esta FORA da proximidade causal em T (mercado
    calmo, ~2%) e DENTRO da proximidade legacy, porque a volatilidade que entra
    na media chegou depois de T. Mesmo pool, mesmo instante, dois vereditos.
    """
    calmos = [_calm(i) for i in range(30)]
    serie = calmos + [_wild(i) for i in range(30, 60)]
    at = calmos[-1].timestamp
    zonas = [_zone(104.9, 105.1)]

    prox_causal = causal_proximity(serie, at, ATR)
    prox_legacy = legacy_proximity(serie, ATR)
    assert prox_causal < 0.05 < prox_legacy

    assert eligible_targets(zonas, 100.0, prox_causal, True, at) == []
    assert eligible_targets(zonas, 100.0, prox_legacy, True, at) != []


def test_eligible_targets_segue_o_lado_cacado() -> None:
    zonas = [_zone(100.9, 101.1)]
    at = BASE + timedelta(hours=5)
    assert eligible_targets(zonas, 100.0, 0.20, True, at) != []
    assert eligible_targets(zonas, 100.0, 0.20, False, at) == []


def test_preco_invalido_nao_estoura() -> None:
    assert eligible_targets([_zone(100.9, 101.1)], 0.0, 0.02, True, BASE) == []


def test_snapshot_sem_zonas_nao_tem_target() -> None:
    data = _data()
    assert eligible_targets(data.liquidity_zones, 100.0, 0.02, True, BASE) == []
