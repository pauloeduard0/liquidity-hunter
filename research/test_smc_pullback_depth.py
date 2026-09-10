"""Testes da Etapa 7.1.

Cinco maneiras de esta medicao mentir sem quebrar:

1. **a populacao deixar de ser a da 7.0** -- a etapa inteira so vale porque
   entrada, stop, alvo, horizonte, custo e politica de empate sao os MESMOS.
   Se a 7.1 tivesse a sua propria copia, uma divergencia de uma linha
   produziria uma comparacao invalida sem produzir erro nenhum.
2. **o limiar ver o holdout** -- foi exatamente o furo da 7.0. O quantil tem
   de sair da distribuicao do discovery, e nao do painel inteiro.
3. **o criterio de selecao aceitar "menos negativo"** -- numa populacao de
   expectativa negativa, cortar trades melhora qualquer conta agregada. A
   peneira precisa exigir expectativa LIQUIDA positiva, e recusar o resto.
4. **contar a mesma feature duas vezes** -- `pullback / impulso` E o
   `retrace_pct`. Trata-las como candidatos independentes infla a busca.
5. **o controle de largura de stop nao controlar nada** -- ele e o teste
   central; um estrato construido sem sinal proprio tem que sair zerado.
"""

import math
from datetime import UTC, datetime, timedelta

import pytest
from liquidity_hunter.core.domain import MarketDirection, TimeFrame
from research.smc_pullback_depth import (
    DEPTH_FEATURES,
    MIN_N,
    QUANTILES,
    buckets_of,
    candidate_rules,
    choose,
    cost_r,
    cuts_of,
    depth_of,
    monotonic_score,
    sample_of,
    stats,
    stop_matched,
)
from research.smc_vwap_entry_quality import ROUND_TRIP, Trade, net_r

START = datetime(2026, 1, 1, tzinfo=UTC)
BULL = MarketDirection.BULLISH


def a_trade(**over) -> Trade:
    base = dict(
        family="bos_retest",
        symbol="TESTUSDT",
        timeframe=TimeFrame.H1,
        direction=BULL,
        event_index=10,
        entry_index=20,
        entry=100.0,
        stop=99.0,
        r=1.0,
        ts=int(START.timestamp()),
        test_ordinal=1,
        r_atr=1.0,
        pullback_atr=3.0,
        retrace_pct=60.0,
        swing_dist_atr=1.0,
        penetration_atr=0.5,
    )
    base.update(over)
    trade = Trade(**base)
    trade.hit = {1.0: 0.0, 2.0: 0.0, 3.0: 0.0}
    trade.r_grid = {1.0: -1.0, 2.0: -1.0, 3.0: -1.0}
    trade.mfe_r = 0.5
    trade.mae_r = 1.0
    trade.stopped_first = 1.0
    return trade


def winner(**over) -> Trade:
    trade = a_trade(**over)
    trade.hit = {1.0: 1.0, 2.0: 1.0, 3.0: 0.0}
    trade.r_grid = {1.0: 1.0, 2.0: 2.0, 3.0: -1.0}
    trade.mfe_r = 2.5
    trade.mae_r = 0.4
    trade.stopped_first = 0.0
    return trade


# --------------------------------------------------------------------------
# 1. a populacao e importada, nao recopiada
# --------------------------------------------------------------------------


def test_a_populacao_e_a_da_7_0_e_nao_uma_copia():
    """Se a 7.1 redefinisse o setup, ela mediria outra coisa com o mesmo nome."""
    import research.smc_pullback_depth as depth
    import research.smc_vwap_entry_quality as entry

    # nada de construir setup aqui: os construtores nao existem neste modulo
    for name in ("build_structure_trades", "build_sweep_trades", "structure_retests"):
        assert not hasattr(depth, name), name
    # e o que ela usa e literalmente o objeto da 7.0
    assert depth.collect_combo is entry.collect_combo
    assert depth.net_r is entry.net_r
    assert depth.Trade is entry.Trade
    assert depth.ROUND_TRIP == entry.ROUND_TRIP


def test_o_custo_e_o_mesmo_termo_que_a_7_0_desconta():
    trade = a_trade(entry=100.0, stop=98.0, r=2.0)
    trade.r_grid[2.0] = 0.0
    assert cost_r(trade) == pytest.approx(ROUND_TRIP / 0.02)
    assert net_r(trade) == pytest.approx(-cost_r(trade))


def test_o_custo_em_R_sobe_quando_o_stop_aperta_em_PRECO():
    """O confundidor central da etapa: profundidade mexe na geometria do stop."""
    wide = a_trade(entry=100.0, stop=96.0, r=4.0)
    tight = a_trade(entry=100.0, stop=99.8, r=0.2)
    assert cost_r(tight) > cost_r(wide) * 10


# --------------------------------------------------------------------------
# 2. a identidade entre retrace_pct e pullback/impulso
# --------------------------------------------------------------------------


def test_retrace_pct_E_pullback_sobre_impulso_e_nao_uma_feature_nova():
    trade = a_trade(pullback_atr=3.0, retrace_pct=60.0, impulse_atr=5.0)
    assert trade.retrace_pct == pytest.approx(
        100.0 * trade.pullback_atr / trade.impulse_atr
    )


def test_pullback_over_leg_so_existe_onde_ha_perna():
    assert math.isnan(depth_of(a_trade(), "pullback_over_leg"))
    trade = a_trade(pullback_atr=3.0, leg_atr=6.0)
    assert depth_of(trade, "pullback_over_leg") == pytest.approx(0.5)


def test_as_tres_features_sao_lidas_pelo_mesmo_acessor():
    trade = a_trade(pullback_atr=3.0, retrace_pct=60.0, leg_atr=6.0)
    assert depth_of(trade, "pullback_atr") == 3.0
    assert depth_of(trade, "retrace_pct") == 60.0
    assert set(DEPTH_FEATURES) == {"pullback_atr", "retrace_pct", "pullback_over_leg"}


# --------------------------------------------------------------------------
# 3. o limiar nao pode ver o holdout
# --------------------------------------------------------------------------


def _split_pool(n: int = 600) -> list[Trade]:
    """Discovery raso, holdout profundo: o quantil DENUNCIA de onde ele saiu."""
    out = []
    for i in range(n):
        old = i < int(n * 0.7)
        out.append(
            a_trade(
                ts=int((START + timedelta(hours=i)).timestamp()),
                pullback_atr=1.0 + (i % 10) / 10 if old else 90.0 + (i % 10),
            )
        )
    return out


def test_o_quantil_sai_do_discovery_e_nao_do_painel_inteiro():
    pool = _split_pool()
    cuts = cuts_of(pool)
    discovery = [t for t in pool if sample_of(t, cuts) == "discovery"]
    got = candidate_rules(discovery)
    assert got, "sem candidato nao ha o que testar"
    for entry in got:
        if entry["feature"] != "pullback_atr":
            continue
        # a distribuicao do discovery vai de 1,0 a 1,9; um limiar acima disso
        # so poderia ter vindo do holdout
        assert entry["threshold"] < 5.0, entry


def test_o_corte_70_30_e_por_timeframe():
    pool = [
        a_trade(
            timeframe=TimeFrame.M15 if i < 300 else TimeFrame.H4,
            ts=int((START + timedelta(hours=i)).timestamp()),
        )
        for i in range(600)
    ]
    cuts = cuts_of(pool)
    m15 = [t for t in pool if t.timeframe is TimeFrame.M15]
    h4 = [t for t in pool if t.timeframe is TimeFrame.H4]
    assert sum(1 for t in m15 if sample_of(t, cuts) == "discovery") == 210
    assert sum(1 for t in h4 if sample_of(t, cuts) == "discovery") == 210


def test_o_numero_de_candidatos_e_o_declarado():
    pool = [
        a_trade(
            ts=int((START + timedelta(hours=i)).timestamp()),
            pullback_atr=1.0 + i / 100,
            retrace_pct=50.0 + i / 20,
            leg_atr=10.0,
        )
        for i in range(1000)
    ]
    got = candidate_rules(pool)
    assert len(got) == len(DEPTH_FEATURES) * len(QUANTILES)


# --------------------------------------------------------------------------
# 4. o criterio de selecao recusa "menos negativo"
# --------------------------------------------------------------------------


def test_nada_e_escolhido_quando_tudo_e_negativo():
    """O furo que a 7.0 pagou: cortar trades melhora qualquer conta agregada."""
    candidates = [
        {"n": 1000, "net_r": -0.10, "pf": 0.8, "coverage": 0.5, "feature": "x",
         "quantile": 0.5, "threshold": 1.0},
        {"n": 1000, "net_r": -0.01, "pf": 0.99, "coverage": 0.2, "feature": "x",
         "quantile": 0.7, "threshold": 2.0},
    ]
    assert choose(candidates) is None


def test_um_candidato_positivo_precisa_de_PF_maior_que_1_E_amostra():
    positivo_sem_pf = {"n": 1000, "net_r": 0.05, "pf": 0.9, "coverage": 0.3,
                       "feature": "x", "quantile": 0.5, "threshold": 1.0}
    positivo_sem_n = {"n": MIN_N - 1, "net_r": 0.30, "pf": 1.5, "coverage": 0.1,
                      "feature": "x", "quantile": 0.6, "threshold": 2.0}
    bom = {"n": 1000, "net_r": 0.12, "pf": 1.2, "coverage": 0.3,
           "feature": "x", "quantile": 0.5, "threshold": 1.5}
    assert choose([positivo_sem_pf]) is None
    assert choose([positivo_sem_n]) is None
    assert choose([positivo_sem_pf, positivo_sem_n, bom]) is bom


# --------------------------------------------------------------------------
# 5. o controle de largura de stop
# --------------------------------------------------------------------------


def test_o_controle_de_stop_zera_um_efeito_que_e_so_geometria():
    """Um "sinal" construido para nao ter nada alem da largura do stop.

    Aqui a profundidade e uma funcao EXATA de `r_atr`, e o desfecho tambem --
    o pior caso possivel para um controle por estratos. Contra o pool inteiro
    o corte profundo separa por 3R; estratificado por decil de largura o
    residual tem de cair muito.

    O teste afirma **reducao**, e nao zero, porque zero seria mentira: um
    confundidor continuo perfeitamente alinhado com a variavel de estrato
    sobrevive dentro do unico estrato que atravessa o limiar. Essa e uma
    limitacao declarada do controle, nao um bug -- e por isso um residual
    pequeno depois dele nao prova sinal proprio.
    """
    pool = []
    for i in range(2000):
        width = 0.2 + (i % 20) / 10.0
        # profundidade colada na largura, e o desfecho tambem
        deep = width > 1.2
        trade = (winner if deep else a_trade)(
            r_atr=width,
            pullback_atr=width * 5.0,
            entry=100.0,
            stop=100.0 - width,
            r=width,
            ts=int((START + timedelta(hours=i)).timestamp()),
        )
        pool.append(trade)
    naive_deep = stats([t for t in pool if t.pullback_atr >= 6.0])
    naive_shallow = stats([t for t in pool if t.pullback_atr < 6.0])
    assert naive_deep["gross_r"] - naive_shallow["gross_r"] > 2.0

    naive = naive_deep["gross_r"] - naive_shallow["gross_r"]
    matched = stop_matched(pool, "pullback_atr", 6.0)
    assert matched["delta_gross"] is not None
    assert abs(matched["delta_gross"]) < 0.4 * naive
    # e o residual vive num punhado de trades, nao no pool inteiro
    assert matched["n"] < 0.25 * len(pool)


def test_o_controle_preserva_um_efeito_que_e_independente_do_stop():
    """O espelho do teste acima: se o sinal for real, o controle nao pode mata-lo."""
    pool = []
    for i in range(2000):
        width = 0.2 + (i % 20) / 10.0
        deep = (i // 20) % 2 == 0  # profundidade INDEPENDENTE da largura
        trade = (winner if deep else a_trade)(
            r_atr=width,
            pullback_atr=8.0 if deep else 2.0,
            entry=100.0,
            stop=100.0 - width,
            r=width,
            ts=int((START + timedelta(hours=i)).timestamp()),
        )
        pool.append(trade)
    matched = stop_matched(pool, "pullback_atr", 6.0)
    assert matched["delta_gross"] > 2.0


# --------------------------------------------------------------------------
# 6. baldes e monotonia
# --------------------------------------------------------------------------


def test_os_baldes_sao_quintis_disjuntos_que_cobrem_o_pool():
    pool = [a_trade(pullback_atr=float(i % 100)) for i in range(1000)]
    got = buckets_of(pool, "pullback_atr")
    assert len(got) == 5
    assert sum(len(rows) for _lo, _hi, rows in got) == len(pool)
    seen = set()
    for _lo, _hi, rows in got:
        ids = {id(t) for t in rows}
        assert not (ids & seen)
        seen |= ids


def test_monotonia_distingue_rampa_de_balde_solto():
    assert monotonic_score([1.0, 2.0, 3.0, 4.0, 5.0]) == 1.0
    assert monotonic_score([1.0, 5.0, 1.0, 5.0, 1.0]) == pytest.approx(0.5)
    assert monotonic_score([5.0, 4.0, 3.0, 2.0, 1.0]) == 0.0


def test_stats_reporta_bruto_liquido_e_custo_sempre_juntos():
    """A 7.0 mostrou que um liquido que melhora sem bruto e corretagem."""
    got = stats([winner(), a_trade(), winner()])
    for key in ("gross_r", "net_r", "cost_r", "cost_r_median", "pf", "pf_gross"):
        assert key in got
    assert got["gross_r"] > got["net_r"]


def test_a_expectativa_e_reportada_nos_tres_alvos():
    got = stats([winner() for _ in range(10)])
    assert got["gross_1r"] == 1.0
    assert got["gross_2r"] == 2.0
    assert got["gross_3r"] == -1.0
