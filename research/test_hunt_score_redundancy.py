"""Testes de `research.hunt_score_redundancy`.

A pesquisa toda repousa sobre uma afirmacao: os clusters que ela conta sao os
clusters que a producao contou. O primeiro bloco de testes e sobre isso -- se a
decomposicao divergir de `_capture_grabs`, todo numero do painel passa a
descrever um motor que nao existe. Os demais cobrem as estatisticas, onde o
erro tipico nao e um crash e sim um numero plausivel e errado (um phi que sobe
com a frequencia, um ganho incremental que e so o nivel do estrato).

Rodar:
    poetry run pytest research/test_hunt_score_redundancy.py
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest
from liquidity_hunter.app.dashboard_data import DashboardData
from liquidity_hunter.app.liquidity_hunt import (
    _CAPTURE_THRESHOLD,
    _CONTINUATION_CAPTURE_THRESHOLD,
    LiquidityHuntEngine,
)
from liquidity_hunter.core.domain import (
    Candle,
    LiquiditySide,
    LiquidityZone,
    LiquidityZoneType,
    MarketDirection,
    MarketStructure,
    RetailPositioning,
    StructureEvent,
    TimeFrame,
    VolumeSpreadSignal,
    VSAPattern,
)
from liquidity_hunter.psychology import RetailBiasEstimate
from research.hunt_score_redundancy import (
    LAGS,
    SOURCES,
    AuditHuntEngine,
    ClusterRow,
    _bucket_of,
    ablate,
    ablation_effect,
    collect_clusters,
    cooccurrence,
    derive_families,
    effective_sources,
    incremental_value,
    outcome_rows,
    temporal_proximity,
)

BASE = datetime(2026, 7, 1, tzinfo=UTC)


def _candles(n: int, tf_minutes: int = 15, start: float = 100.0) -> list[Candle]:
    """Uma serie plana e limpa: nada aqui deve produzir sinal por acidente."""
    out = []
    for i in range(n):
        price = start + (i % 7) * 0.1
        out.append(
            Candle(
                symbol="TESTUSDT",
                timeframe=TimeFrame.M15,
                timestamp=BASE + timedelta(minutes=tf_minutes * i),
                open=price,
                high=price + 0.5,
                low=price - 0.5,
                close=price,
                volume=100.0,
                taker_buy_volume=50.0,
            )
        )
    return out


def _bias() -> RetailBiasEstimate:
    return RetailBiasEstimate(
        symbol="TESTUSDT",
        generated_at=BASE,
        dominant_side="neutral",
        confidence=50.0,
        explanation="Neutro.",
    )


def _data(**kwargs: object) -> DashboardData:
    candles = kwargs.pop("candles", None) or _candles(120)
    base: dict[str, object] = {
        "symbol": "TESTUSDT",
        "timeframe": TimeFrame.M15,
        "candles": candles,
        "current_price": candles[-1].close,
        "higher_timeframe_direction": MarketDirection.NEUTRAL,
        "liquidity_zones": [],
        "ranked_zones": [],
        "market_structure_events": [],
        "internal_structure_events": [],
        "retail_bias": _bias(),
        "poi_zones": [],
        "behavior_divergences": [],
        "volume_spread_signals": [],
    }
    base.update(kwargs)
    return DashboardData(**base)  # type: ignore[arg-type]


def _row(sources: dict[str, float], **kwargs: object) -> ClusterRow:
    defaults: dict[str, object] = {
        "symbol": "TESTUSDT",
        "tf": "M15",
        "window": "w0",
        "stream": "hunt",
        "leg_start": BASE.isoformat(),
        "leg_end": (BASE + timedelta(hours=5)).isoformat(),
        "anchor": BASE.isoformat(),
        "first_ts": BASE.isoformat(),
        "last_ts": BASE.isoformat(),
        "hunted_side": RetailPositioning.SHORT.value,
        "capture_direction": MarketDirection.BULLISH.value,
        "threshold": _CAPTURE_THRESHOLD,
        "require_vsa": True,
        "allow_raid": True,
        "by_source": dict(sources),
        "raw": [(BASE.isoformat(), w, s) for s, w in sources.items()],
        "score": sum(sources.values()),
        "passed": True,
        "reason": "",
        "oi_available": False,
    }
    defaults.update(kwargs)
    return ClusterRow(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. Fidelidade a producao -- o que sustenta todo o resto
# ---------------------------------------------------------------------------


def _hunt_snapshot() -> DashboardData:
    """Um snapshot com uma perna contra-tendencia que fecha num grab real.

    Montado a mao e nao baixado: o painel mede o mercado, o teste tem de medir
    o *motor*, e para isso precisa de um caso cujo resultado seja conhecido
    antes de rodar.
    """
    candles = _candles(120)
    # HTF bullish; a perna LTF vira bearish (contra-tendencia) e depois volta.
    htf_events = [
        MarketStructure(
            symbol="TESTUSDT",
            timeframe=TimeFrame.H1,
            timestamp=BASE - timedelta(hours=8),
            event=StructureEvent.BREAK_OF_STRUCTURE,
            direction=MarketDirection.BULLISH,
            price_level=100.0,
        )
    ]
    flip = candles[20].timestamp
    back = candles[60].timestamp
    events = [
        MarketStructure(
            symbol="TESTUSDT",
            timeframe=TimeFrame.M15,
            timestamp=flip,
            event=StructureEvent.CHANGE_OF_CHARACTER,
            direction=MarketDirection.BEARISH,
            price_level=100.0,
        ),
        MarketStructure(
            symbol="TESTUSDT",
            timeframe=TimeFrame.M15,
            timestamp=candles[40].timestamp,
            event=StructureEvent.LIQUIDITY_SWEEP,
            direction=MarketDirection.BULLISH,
            price_level=101.0,
        ),
        MarketStructure(
            symbol="TESTUSDT",
            timeframe=TimeFrame.M15,
            timestamp=back,
            event=StructureEvent.BREAK_OF_STRUCTURE,
            direction=MarketDirection.BULLISH,
            price_level=101.0,
        ),
    ]
    vsa = [
        VolumeSpreadSignal(
            symbol="TESTUSDT",
            timeframe=TimeFrame.M15,
            timestamp=candles[40].timestamp,
            pattern=VSAPattern.UP_THRUST,
            direction=MarketDirection.BEARISH,
            price_level=101.0,
            spread_ratio=2.0,
            close_position=0.2,
            volume_ratio=2.5,
            volume_delta=50.0,
            confidence=80.0,
            description="teste",
        )
    ]
    zones = [
        LiquidityZone(
            symbol="TESTUSDT",
            zone_type=LiquidityZoneType.EQUAL_HIGHS,
            price_low=100.9,
            price_high=101.1,
            timeframe=TimeFrame.M15,
            side=LiquiditySide.BUY_SIDE,
            formed_at=candles[5].timestamp,
            is_mitigated=True,
            invalidated_at=candles[40].timestamp,
        )
    ]
    return _data(
        candles=candles,
        internal_structure_events=events,
        higher_timeframe_events=htf_events,
        higher_timeframe=TimeFrame.H1,
        higher_timeframe_direction=MarketDirection.BULLISH,
        volume_spread_signals=vsa,
        liquidity_zones=zones,
    )


def test_o_snapshot_de_teste_realmente_produz_um_grab() -> None:
    """Sem isto, os testes de equivalencia passariam sobre listas vazias."""
    episodes = LiquidityHuntEngine().build_history(_hunt_snapshot())
    assert episodes, "o fixture precisa gerar pelo menos um episodio de hunt"


def test_decomposicao_reproduz_os_grabs_de_producao() -> None:
    """Os clusters aprovados sao exatamente os grabs que a producao emitiu.

    Comparado por (stream, ancora, score, fontes) e nao por contagem: dois
    conjuntos do mesmo tamanho podem descrever grabs diferentes, que e o erro
    que esta medicao nao pode cometer.
    """
    data = _hunt_snapshot()
    engine = LiquidityHuntEngine()
    producao = {
        (stream, e.end_timestamp.isoformat(), e.capture_score, tuple(e.capture_sources))
        for stream, method in (
            ("hunt", "build_history"),
            ("continuation", "build_continuation_history"),
        )
        for e in getattr(engine, method)(data)
    }
    auditoria = {
        (r.stream, r.anchor, r.score, tuple(sorted(r.by_source)))
        for r in collect_clusters(data, "TESTUSDT", "M15", "w0")
        if r.passed
    }
    assert auditoria == producao


def test_motor_auditado_nao_muda_o_stream() -> None:
    """`AuditHuntEngine` observa; ele nao pode decidir."""
    data = _hunt_snapshot()
    assert AuditHuntEngine().build_history(data) == LiquidityHuntEngine().build_history(data)
    assert (
        AuditHuntEngine().build_continuation_history(data)
        == LiquidityHuntEngine().build_continuation_history(data)
    )


def test_clusters_reprovados_sao_guardados_com_motivo() -> None:
    """Um gate so pode ser avaliado contra o que ele removeu."""
    rows = collect_clusters(_hunt_snapshot(), "TESTUSDT", "M15", "w0")
    reprovados = [r for r in rows if not r.passed]
    assert reprovados, "o fixture precisa ter clusters abaixo do limiar"
    assert all(r.reason for r in reprovados)
    assert {r.reason for r in reprovados} <= {
        "floor_signature",
        "raid_only",
        "below_threshold",
    }
    assert all(r.reason == "" for r in rows if r.passed)


def test_o_limiar_registrado_e_o_da_producao_por_stream() -> None:
    """Os dois streams usam limiares diferentes, e o rotulo tem de segui-los."""
    rows = collect_clusters(_hunt_snapshot(), "TESTUSDT", "M15", "w0")
    for r in rows:
        esperado = (
            _CAPTURE_THRESHOLD if r.stream == "hunt" else _CONTINUATION_CAPTURE_THRESHOLD
        )
        assert r.threshold == esperado


def test_score_registrado_e_a_soma_das_fontes() -> None:
    for r in collect_clusters(_hunt_snapshot(), "TESTUSDT", "M15", "w0"):
        assert r.score == pytest.approx(sum(r.by_source.values()))


# ---------------------------------------------------------------------------
# 2. Ablation
# ---------------------------------------------------------------------------


def test_ablation_de_fonte_ausente_nao_muda_nada() -> None:
    """Tirar o que nao estava la e o controle negativo da ablation."""
    data = _hunt_snapshot()
    base = LiquidityHuntEngine().build_history(data)
    assert ablate("oi_flush")().build_history(data) == base


def test_ablation_de_fonte_presente_derruba_o_grab() -> None:
    """O fixture chega ao limiar com sweep+vsa+zone; sem o vsa ele nao chega."""
    data = _hunt_snapshot()
    antes = LiquidityHuntEngine().build_history(data)
    depois = ablate("vsa")().build_history(data)
    assert len(depois) < len(antes)


def test_ablation_reporta_somem_e_sobrevivem_de_forma_consistente() -> None:
    efeitos = ablation_effect(_hunt_snapshot())
    assert set(efeitos) == set(SOURCES)
    for eff in efeitos.values():
        assert eff["somem"] + eff["sobrevivem"] == eff["base"]


def test_ablacao_de_zone_preserva_a_geometria_dos_pools() -> None:
    """`zone` sai como evidencia; os niveis continuam formando pools do raid.

    Se a ablacao apagasse as zonas do snapshot, ela removeria duas fontes de
    uma vez e o resultado do item 7 seria atribuido a fonte errada.
    """
    data = _hunt_snapshot()
    engine = ablate("zone")()
    signals = engine._collect_capture_signals(
        data, True, MarketDirection.BULLISH, data.candles[0].timestamp,
        data.candles[-1].timestamp,
    )
    assert not [s for s in signals if s[2] == "zone"]
    assert data.liquidity_zones, "as zonas continuam no snapshot"


# ---------------------------------------------------------------------------
# 3. Co-ocorrencia
# ---------------------------------------------------------------------------


def test_phi_um_para_fontes_sempre_juntas() -> None:
    rows = [_row({"raid": 4.0, "zone": 2.0}) for _ in range(10)]
    rows += [_row({"sweep": 3.0, "vsa": 4.0}) for _ in range(10)]
    m = cooccurrence(rows)
    assert m["raid|zone"]["phi"] == pytest.approx(1.0)
    assert m["raid|zone"]["jaccard"] == pytest.approx(1.0)


def test_phi_nao_premia_uma_fonte_apenas_frequente() -> None:
    """Uma fonte presente em todo cluster co-ocorre com tudo e nao explica nada.

    E a armadilha central desta matriz: `P(B|A)` seria 1.0 e diria "sempre
    juntos", enquanto phi corretamente fica em zero -- nao ha informacao em
    algo que nunca varia.
    """
    rows = [_row({"delta": 1.0, "raid": 4.0}) for _ in range(10)]
    rows += [_row({"delta": 1.0, "sweep": 3.0}) for _ in range(10)]
    m = cooccurrence(rows)
    assert m["raid|delta"]["p_a_given_b"] == pytest.approx(0.5)
    assert m["raid|delta"]["p_b_given_a"] == pytest.approx(1.0)
    assert m["raid|delta"]["phi"] == pytest.approx(0.0)


def test_phi_negativo_para_fontes_mutuamente_exclusivas() -> None:
    rows = [_row({"raid": 4.0}) for _ in range(10)]
    rows += [_row({"sweep": 3.0}) for _ in range(10)]
    assert cooccurrence(rows)["raid|sweep"]["phi"] < 0


def test_cooccurrence_vazia_nao_estoura() -> None:
    assert cooccurrence([]) == {}


def test_proximidade_temporal_conta_em_candles_do_grafico() -> None:
    """Duas fontes a 30 minutos sao 2 candles no M15 e meio candle no H1."""
    raw = [
        (BASE.isoformat(), 3.0, "sweep"),
        ((BASE + timedelta(minutes=30)).isoformat(), 4.0, "vsa"),
    ]
    row = _row({"sweep": 3.0, "vsa": 4.0}, raw=raw)
    prox = temporal_proximity([row], {"M15": 900.0})
    assert prox["vsa|sweep"]["media_candles"] == pytest.approx(2.0)
    assert prox["vsa|sweep"]["pct_lag_1"] == 0.0
    assert prox["vsa|sweep"]["pct_lag_3"] == 1.0
    prox_h1 = temporal_proximity([_row({"sweep": 3.0, "vsa": 4.0}, tf="H1", raw=raw)],
                                 {"H1": 3600.0})
    assert prox_h1["vsa|sweep"]["media_candles"] == pytest.approx(0.5)
    assert prox_h1["vsa|sweep"][f"pct_lag_{LAGS[0]}"] == 0.0


def test_proximidade_usa_o_par_mais_proximo() -> None:
    """Com varias marcas da mesma fonte, o que importa e a menor distancia."""
    raw = [
        (BASE.isoformat(), 3.0, "sweep"),
        ((BASE + timedelta(minutes=45)).isoformat(), 3.0, "sweep"),
        ((BASE + timedelta(minutes=15)).isoformat(), 4.0, "vsa"),
    ]
    prox = temporal_proximity([_row({"sweep": 3.0, "vsa": 4.0}, raw=raw)], {"M15": 900.0})
    assert prox["vsa|sweep"]["media_candles"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 4. Familias e dupla contagem
# ---------------------------------------------------------------------------


def test_familias_agrupam_fontes_correlacionadas_e_separam_o_resto() -> None:
    rows = [_row({"raid": 4.0, "zone": 2.0}) for _ in range(10)]
    rows += [_row({"sweep": 3.0}) for _ in range(10)]
    familias = derive_families(cooccurrence(rows))
    juntas = next(f for f in familias if "raid" in f)
    assert set(juntas) == {"raid", "zone"}
    assert ["sweep"] in familias


def test_familias_sao_transitivas() -> None:
    """Se A e B sao o mesmo evento e B e C tambem, os tres contam por um."""
    rows = [_row({"raid": 4.0, "zone": 2.0, "supertrend": 3.0}) for _ in range(10)]
    rows += [_row({"sweep": 3.0}) for _ in range(10)]
    familias = derive_families(cooccurrence(rows))
    assert set(next(f for f in familias if "raid" in f)) == {"raid", "zone", "supertrend"}


def test_fontes_efetivas_contam_familias_e_nao_fontes() -> None:
    familias = [["raid", "zone"], ["sweep"], ["vsa"]]
    row = _row({"raid": 4.0, "zone": 2.0, "sweep": 3.0})
    assert row.n_sources == 3
    assert effective_sources(row, familias) == 2


# ---------------------------------------------------------------------------
# 5. Resultado, baldes e valor incremental
# ---------------------------------------------------------------------------


def test_balde_de_score_cobre_o_limiar_sozinho() -> None:
    """7 tem balde proprio: a pergunta do item 11 e sobre o limiar exato."""
    assert _bucket_of(7.0) == "7"
    assert _bucket_of(6.0) == "6"
    assert _bucket_of(8.0) == "8-9"
    assert _bucket_of(3.0) == "<4"
    assert _bucket_of(99.0) == "13+"


def test_outcome_ancora_no_anchor_do_grab() -> None:
    """A excursao comeca onde a producao diz que a caca terminou."""
    candles = _candles(200)
    data = _data(candles=candles)
    anchor = candles[100].timestamp
    row = _row({"sweep": 3.0, "vsa": 4.0}, anchor=anchor.isoformat())
    out = outcome_rows(data, [row], random.Random(1))
    assert len(out) == 1
    assert out[0]["n_sources"] == 2
    assert "mfe_5" in out[0] and "ctl_win_5" in out[0]


def test_outcome_ignora_ancora_fora_da_serie() -> None:
    data = _data(candles=_candles(200))
    row = _row({"sweep": 3.0}, anchor=(BASE - timedelta(days=30)).isoformat())
    assert outcome_rows(data, [row], random.Random(1)) == []


def test_valor_incremental_ignora_estrato_sem_os_dois_lados() -> None:
    """Um estrato so com A nao diz nada sobre A, e nao pode entrar na conta."""
    rows = [
        {"stream": "hunt", "tf": "M15", "up": True, "score": 7.0,
         "sources": ["raid"], "win_20": True},
        {"stream": "hunt", "tf": "H1", "up": True, "score": 7.0,
         "sources": ["sweep"], "win_20": False},
    ]
    inc = incremental_value(rows, 20)
    assert inc["raid"]["estratos"] == 0.0
    assert inc["raid"]["delta_win"] == 0.0


def test_valor_incremental_mede_dentro_do_estrato() -> None:
    """A comparacao e com clusters de mesmo score e mesma direcao, nao globais."""
    rows = [
        {"stream": "hunt", "tf": "M15", "up": True, "score": 7.0,
         "sources": ["raid", "zone"], "win_20": True},
        {"stream": "hunt", "tf": "M15", "up": True, "score": 7.0,
         "sources": ["sweep", "vsa"], "win_20": False},
    ]
    inc = incremental_value(rows, 20)
    assert inc["raid"]["delta_win"] == pytest.approx(1.0)
    assert inc["sweep"]["delta_win"] == pytest.approx(-1.0)


def test_valor_incremental_nao_confunde_nivel_do_estrato_com_ganho() -> None:
    """Dois estratos, um bom e um ruim, e A distribuida igualmente nos dois.

    Sem estratificacao a media de A seria puxada pelo estrato bom; com ela, o
    ganho e corretamente zero.
    """
    rows = []
    for tf, win in (("M15", True), ("H1", False)):
        rows.append({"stream": "hunt", "tf": tf, "up": True, "score": 7.0,
                     "sources": ["raid"], "win_20": win})
        rows.append({"stream": "hunt", "tf": tf, "up": True, "score": 7.0,
                     "sources": ["sweep"], "win_20": win})
    assert incremental_value(rows, 20)["raid"]["delta_win"] == pytest.approx(0.0)


def _continuation_snapshot() -> DashboardData:
    """Uma perna ALINHADA com a HTF, que e onde o lado cacado se inverte.

    Existe separado do `_hunt_snapshot` porque o bug que este bloco vigia so
    aparece na continuation: la o argumento `hunted_short` de `_capture_grabs`
    e o lado do wick do pullback, e o lado cacado e o oposto dele.
    """
    candles = _candles(120)
    htf_events = [
        MarketStructure(
            symbol="TESTUSDT",
            timeframe=TimeFrame.H1,
            timestamp=BASE - timedelta(hours=8),
            event=StructureEvent.BREAK_OF_STRUCTURE,
            direction=MarketDirection.BULLISH,
            price_level=100.0,
        )
    ]
    events = [
        MarketStructure(
            symbol="TESTUSDT",
            timeframe=TimeFrame.M15,
            timestamp=candles[20].timestamp,
            event=StructureEvent.BREAK_OF_STRUCTURE,
            direction=MarketDirection.BULLISH,
            price_level=100.0,
        ),
        MarketStructure(
            symbol="TESTUSDT",
            timeframe=TimeFrame.M15,
            timestamp=candles[45].timestamp,
            event=StructureEvent.LIQUIDITY_SWEEP,
            direction=MarketDirection.BEARISH,
            price_level=99.0,
        ),
    ]
    vsa = [
        VolumeSpreadSignal(
            symbol="TESTUSDT",
            timeframe=TimeFrame.M15,
            timestamp=candles[45].timestamp,
            pattern=VSAPattern.DOWN_THRUST,
            direction=MarketDirection.BULLISH,
            price_level=99.0,
            spread_ratio=2.0,
            close_position=0.8,
            volume_ratio=2.5,
            volume_delta=-50.0,
            confidence=80.0,
            description="teste",
        )
    ]
    return _data(
        candles=candles,
        internal_structure_events=events,
        higher_timeframe_events=htf_events,
        higher_timeframe=TimeFrame.H1,
        higher_timeframe_direction=MarketDirection.BULLISH,
        volume_spread_signals=vsa,
    )


def test_o_snapshot_de_continuation_produz_um_grab() -> None:
    assert LiquidityHuntEngine().build_continuation_history(_continuation_snapshot())


@pytest.mark.parametrize("snapshot", [_hunt_snapshot, _continuation_snapshot])
def test_lado_cacado_registrado_e_o_da_producao(snapshot: object) -> None:
    """O lado cacado do cluster e o lado cacado do episodio, nos dois streams.

    Regressao de um erro que o painel de fato cometeu: na continuation o
    `hunted_short` recebido por `_capture_grabs` e o wick do pullback, oposto ao
    lado cacado, e registra-lo cru inverte a direcao da excursao futura -- os
    numeros saem plausiveis e espelhados, que e a forma mais dificil de errar
    de perceber.
    """
    data = snapshot()  # type: ignore[operator]
    engine = LiquidityHuntEngine()
    esperado = {
        (stream, e.end_timestamp.isoformat()): e.hunted_side.value
        for stream, method in (
            ("hunt", "build_history"),
            ("continuation", "build_continuation_history"),
        )
        for e in getattr(engine, method)(data)
    }
    assert esperado, "o fixture precisa gerar episodios"
    for r in collect_clusters(data, "TESTUSDT", "M15", "w0"):
        if r.passed:
            assert r.hunted_side == esperado[(r.stream, r.anchor)]
