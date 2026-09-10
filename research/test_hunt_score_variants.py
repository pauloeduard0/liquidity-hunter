"""Testes de `research.hunt_score_variants`.

O primeiro teste do arquivo e o unico que precisa passar para os outros
significarem alguma coisa: `V0` -- a variante que nao muda regra nenhuma --
tem de reproduzir a producao episodio a episodio. Se ela divergir, a
maquinaria de variantes esta medindo um motor que nao existe, e o efeito de V1,
V2 e V3 fica indistinguivel do efeito de te-las escrito.

Depois disso, cada variante e testada por aquilo que ela promete **e** pelo que
ela promete nao fazer: V2 nao pode tocar no hunt, V3 nao pode tocar na
continuation, e V1 nao pode mexer na lista de fontes.

Rodar:
    poetry run pytest research/test_hunt_score_variants.py
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from liquidity_hunter.app.liquidity_hunt import (
    _CAPTURE_THRESHOLD,
    _CONTINUATION_CAPTURE_THRESHOLD,
    LiquidityHuntEngine,
)
from liquidity_hunter.core.domain import Candle, StructureEvent, TimeFrame
from research.hunt_score_variants import (
    BLOCKS,
    DISCOVERY_SHARE,
    V0,
    V1,
    V1V3,
    V2,
    V3,
    VARIANTS,
    _position,
    annotate,
    engine_episodes,
    outcome_of,
    series_span,
)
from research.test_hunt_score_redundancy import (
    BASE,
    _candles,
    _continuation_snapshot,
    _hunt_snapshot,
)

SNAPSHOTS = (_hunt_snapshot, _continuation_snapshot)


# ---------------------------------------------------------------------------
# 1. V0: o controle da maquinaria
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("snapshot", SNAPSHOTS)
def test_v0_reproduz_a_producao(snapshot: object) -> None:
    """Sem regra alterada, a variante e a producao -- inclusive nos scores.

    Comparado com `==` sobre os proprios episodios (dataclasses de dominio), e
    nao por contagem: dois streams do mesmo tamanho podem descrever pernas
    diferentes.
    """
    data = snapshot()  # type: ignore[operator]
    producao = LiquidityHuntEngine()
    variante = V0()
    assert variante.build_history(data) == producao.build_history(data)
    assert (
        variante.build_continuation_history(data)
        == producao.build_continuation_history(data)
    )


def test_v0_preserva_os_gates_de_producao() -> None:
    """Os gates nao sao reimplementados pela variante; eles continuam decidindo.

    O fixture do hunt tem clusters que a assinatura de piso reprova; se a
    maquinaria os deixasse passar, `V0` teria mais episodios que a producao --
    e o teste acima ja falharia. Aqui a checagem e direta sobre a contagem, para
    que a causa apareca nomeada quando quebrar.
    """
    data = _hunt_snapshot()
    assert len(V0().build_history(data)) == len(LiquidityHuntEngine().build_history(data))


# ---------------------------------------------------------------------------
# 2. V1 -- cap de familia raid+zone
# ---------------------------------------------------------------------------


def test_v1_desconta_o_menor_peso_quando_raid_e_zone_coexistem() -> None:
    assert V1().effective_score({"raid": 4.0, "zone": 2.0, "delta": 1.0}) == 5.0


def test_v1_nao_mexe_em_cluster_sem_o_par() -> None:
    for sources in (
        {"raid": 4.0, "sweep": 3.0},
        {"zone": 2.0, "vsa": 4.0, "delta": 1.0},
        {"vsa": 3.0, "sweep": 3.0, "delta": 1.0},
    ):
        assert V1().effective_score(sources) == sum(sources.values())


def test_v1_nao_altera_a_lista_de_fontes() -> None:
    """O item 3 pede o score efetivo, com a evidencia observada preservada."""
    data = _hunt_snapshot()
    base = {e.end_timestamp: sorted(e.capture_sources) for e in V0().build_history(data)}
    for e in V1().build_history(data):
        assert sorted(e.capture_sources) == base[e.end_timestamp]


def test_v1_so_pode_remover_episodios_nunca_criar() -> None:
    """Um cap de score e monotonico: nada que reprovava passa a aprovar."""
    for snapshot in SNAPSHOTS:
        data = snapshot()
        for method in ("build_history", "build_continuation_history"):
            base = {e.end_timestamp for e in getattr(V0(), method)(data)}
            var = {e.end_timestamp for e in getattr(V1(), method)(data)}
            assert var <= base


# ---------------------------------------------------------------------------
# 3. V2 -- continuation com >= 2 fontes
# ---------------------------------------------------------------------------


def test_v2_recusa_fonte_unica_na_continuation() -> None:
    engine = V2()
    engine._stream = "continuation"
    assert not engine.extra_gate({"vsa": 4.0})
    assert engine.extra_gate({"vsa": 4.0, "delta": 1.0})


def test_v2_nao_toca_no_hunt() -> None:
    """O gate e por hipotese exclusivo da continuation."""
    engine = V2()
    engine._stream = "hunt"
    assert engine.extra_gate({"vsa": 4.0})
    data = _hunt_snapshot()
    assert V2().build_history(data) == V0().build_history(data)


def _solo_vsa_snapshot() -> Any:
    """Continuation cujo grab e um VSA forte **sozinho** -- o alvo exato de V2.

    Derivado do fixture compartilhado removendo o `LIQUIDITY_SWEEP` co-locado:
    sobra o thrust de confianca 80 (peso 4), que atinge o limiar 4 da
    continuation sem companhia. A serie do fixture tem delta zerado
    (`taker_buy_volume` = metade do volume), entao nem o modificador de delta
    entra -- que e o que torna o cluster genuinamente de fonte unica.
    """
    data = _continuation_snapshot()
    return replace(
        data,
        internal_structure_events=[
            e for e in data.internal_structure_events
            if e.event is not StructureEvent.LIQUIDITY_SWEEP
        ],
    )


def test_v2_remove_a_continuation_de_uma_fonte_so() -> None:
    data = _solo_vsa_snapshot()
    base = V0().build_continuation_history(data)
    assert base, "o fixture precisa ter um grab de continuation"
    solo = [e for e in base if len(e.capture_sources) == 1]
    assert solo, "o fixture precisa ter um grab de fonte unica"
    depois = V2().build_continuation_history(data)
    assert len(depois) == len(base) - len(solo)


def test_v2_preserva_a_continuation_com_duas_fontes() -> None:
    """O controle de V2: o gate remove a fonte unica, nao a continuation toda."""
    data = _continuation_snapshot()
    base = V0().build_continuation_history(data)
    assert base and all(len(e.capture_sources) >= 2 for e in base)
    assert V2().build_continuation_history(data) == base


# ---------------------------------------------------------------------------
# 4. V3 -- limiar do hunt em 10
# ---------------------------------------------------------------------------


def test_v3_eleva_apenas_o_limiar_do_hunt() -> None:
    engine = V3()
    engine._stream = "hunt"
    assert engine.threshold_for(_CAPTURE_THRESHOLD) == 10.0
    engine._stream = "continuation"
    assert engine.threshold_for(_CONTINUATION_CAPTURE_THRESHOLD) == (
        _CONTINUATION_CAPTURE_THRESHOLD
    )


def test_v3_nao_toca_na_continuation() -> None:
    data = _continuation_snapshot()
    assert (
        V3().build_continuation_history(data) == V0().build_continuation_history(data)
    )


def test_v3_remove_o_hunt_abaixo_de_dez() -> None:
    data = _hunt_snapshot()
    base = V0().build_history(data)
    assert base and all(e.capture_score < 10 for e in base)
    assert V3().build_history(data) == []


def test_v1v3_aplica_as_duas_regras() -> None:
    engine = V1V3()
    engine._stream = "hunt"
    assert engine.effective_score({"raid": 4.0, "zone": 2.0}) == 4.0
    assert engine.threshold_for(_CAPTURE_THRESHOLD) == 10.0


def test_todas_as_variantes_estao_registradas() -> None:
    assert set(VARIANTS) == {"V0", "V1", "V2", "V3", "V1V3"}


# ---------------------------------------------------------------------------
# 5. Cortes temporais e deduplicacao
# ---------------------------------------------------------------------------


def test_span_sai_da_serie_e_nao_dos_episodios() -> None:
    """A fronteira tem de ser identica em todos os bracos (ver `series_span`)."""
    candles = _candles(1000)
    lo, hi = series_span(candles)
    assert hi == candles[-1].timestamp
    assert lo == candles[-800].timestamp


def test_span_de_serie_curta_nao_estoura() -> None:
    candles = _candles(50)
    lo, hi = series_span(candles)
    assert lo == candles[0].timestamp
    assert hi == candles[-1].timestamp


def test_posicao_temporal_e_fatia() -> None:
    spans = {
        "BTCUSDT:M15": [
            BASE.isoformat(),
            (BASE + timedelta(days=100)).isoformat(),
        ]
    }
    inicio = _position(spans, "BTCUSDT", "M15", BASE.isoformat())
    meio = _position(spans, "BTCUSDT", "M15", (BASE + timedelta(days=50)).isoformat())
    fim = _position(spans, "BTCUSDT", "M15", (BASE + timedelta(days=90)).isoformat())
    assert inicio == pytest.approx(0.0)
    assert meio == pytest.approx(0.5)
    assert fim == pytest.approx(0.9)
    assert meio is not None and meio < DISCOVERY_SHARE <= fim  # type: ignore[operator]


def test_posicao_sem_span_conhecido_e_none() -> None:
    assert _position({}, "XXXUSDT", "M15", BASE.isoformat()) is None


def test_annotate_classifica_split_e_bloco() -> None:
    payload = {
        "spans": {"BTCUSDT:M15": [BASE.isoformat(),
                                  (BASE + timedelta(days=100)).isoformat()]},
        "outcomes": {
            "a": {"symbol": "BTCUSDT", "tf": "M15",
                  "anchor": (BASE + timedelta(days=10)).isoformat()},
            "b": {"symbol": "BTCUSDT", "tf": "M15",
                  "anchor": (BASE + timedelta(days=95)).isoformat()},
            "c": {"symbol": "ZZZUSDT", "tf": "M15", "anchor": BASE.isoformat()},
        },
    }
    annotate(payload)
    assert payload["outcomes"]["a"]["split"] == "discovery"
    assert payload["outcomes"]["a"]["block"] == 0
    assert payload["outcomes"]["b"]["split"] == "holdout"
    assert payload["outcomes"]["b"]["block"] == BLOCKS - 1
    assert payload["outcomes"]["c"]["split"] is None


def test_episodios_sao_chaveados_por_captura() -> None:
    """A identidade e a captura: o inicio se desloca quando o vizinho muda."""
    data = _hunt_snapshot()
    eps = engine_episodes(V0(), data, "TESTUSDT", "M15")
    assert eps
    for key, ep in eps.items():
        assert key.symbol == "TESTUSDT"
        assert key.stream in ("hunt", "continuation")
        assert datetime.fromisoformat(key.anchor)
        assert datetime.fromisoformat(ep["start"]) <= datetime.fromisoformat(key.anchor)


# ---------------------------------------------------------------------------
# 6. Outcome
# ---------------------------------------------------------------------------


def _trending(n: int, step: float) -> list[Candle]:
    return [
        Candle(
            symbol="TESTUSDT",
            timeframe=TimeFrame.M15,
            timestamp=BASE + timedelta(minutes=15 * i),
            open=100.0 + step * i,
            high=100.5 + step * i,
            low=99.5 + step * i,
            close=100.0 + step * i,
            volume=100.0,
            taker_buy_volume=50.0,
        )
        for i in range(n)
    ]


def test_outcome_mede_o_movimento_liquido_com_sinal_da_direcao() -> None:
    """Numa serie que sobe, um grab de alta tem net positivo e o de baixa nao."""
    candles = _trending(200, 0.1)
    anchor = candles[100].timestamp
    alta = outcome_of(candles, anchor, True, random.Random(1))
    baixa = outcome_of(candles, anchor, False, random.Random(1))
    assert alta is not None and baixa is not None
    assert alta["net_20"] > 0
    assert baixa["net_20"] == pytest.approx(-alta["net_20"])
    assert alta["win_20"] is True


def test_outcome_fora_da_serie_e_none() -> None:
    candles = _trending(200, 0.1)
    assert outcome_of(candles, BASE - timedelta(days=5), True, random.Random(1)) is None


def test_outcome_de_serie_curta_e_none() -> None:
    candles = _trending(10, 0.1)
    assert outcome_of(candles, candles[2].timestamp, True, random.Random(1)) is None


def test_datas_do_teste_sao_utc() -> None:
    """Um span comparado entre fusos diferentes daria uma fatia silenciosamente
    errada; o projeto inteiro e tz-aware em UTC e o fixture tem de seguir."""
    assert BASE.tzinfo is UTC
