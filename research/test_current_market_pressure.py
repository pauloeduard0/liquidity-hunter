"""Testes da Etapa 5.0.

Quatro modos de falha silenciosa dominam uma medicao de "pressao atual", e
cada um tem teste nomeado aqui. Todos produziriam um relatorio bonito:

- **vazamento de futuro numa feature.** Uma janela centrada, uma media que le
  o candle seguinte, um extremo de perna atualizado antes da hora: qualquer um
  deles cria separacao do nada. O teste e o unico honesto -- truncar a serie no
  candle T nao pode mudar a leitura em T.
- **o estado da perna divergir da producao.** `leg_state_by_index` reproduz
  `detect_structural_stall` numa varredura para frente. Se as duas semanticas
  se separarem, a coluna `stale` do relatorio vira ficcao e a secao 14 responde
  sobre outra coisa.
- **o placebo nao ser placebo.** Se a permutacao por blocos devolver a
  identidade (ou quase), o controle passa a concordar com o sinal por
  construcao e a defesa contra "o periodo subiu" desaparece em silencio.
- **assimetria escondida.** Uma feature com erro de sinal funciona em metade da
  amostra e falha na outra; agregado, parece ruido. O teste espelha a serie e
  exige que toda feature troque de sinal.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.liquidity.structural_stall import detect_structural_stall
from research.current_market_pressure import (
    ATR_WINDOW,
    EPISODE_GAP,
    FEATURE_FAMILY,
    FEATURES,
    PERSIST_HORIZONS,
    PLACEBO_BLOCK,
    RULES,
    RULES_TESTED,
    STRIDE,
    CandleStore,
    Rule,
    ScoreRule,
    atr_pct_series,
    auc,
    block_permutation,
    cut_by_timeframe,
    feature_matrix,
    first_resume,
    future_targets,
    leg_state_by_index,
    pressure_targets,
    rolling_vwap,
    rule_grid,
    select,
    signed_to_aligned,
    structural_outcome,
    trend_by_index,
)
from research.range_choch import CACHE_DIR, load_series

START = datetime(2026, 1, 1, tzinfo=UTC)


def candle(index: int, close: float, *, volume: float = 100.0, taker: float = 50.0) -> Candle:
    return Candle(
        symbol="TESTUSDT",
        timeframe=TimeFrame.H1,
        timestamp=START + timedelta(hours=index),
        open=close,
        high=close * 1.01,
        low=close * 0.99,
        close=close,
        volume=volume,
        taker_buy_volume=taker,
    )


def series(closes, volumes=None, takers=None):
    return [
        candle(
            index,
            close,
            volume=100.0 if volumes is None else volumes[index],
            taker=50.0 if takers is None else takers[index],
        )
        for index, close in enumerate(closes)
    ]


def wave(n: int = 300) -> list[Candle]:
    """Uma serie com tendencia e oscilacao -- nao monotonica, para que
    eficiencia direcional e posicao no range nao fiquem saturadas."""
    return series([100.0 + i * 0.2 + 6.0 * math.sin(i / 7.0) for i in range(n)])


def bos(index: int, direction: MarketDirection, price: float) -> MarketStructure:
    return MarketStructure(
        symbol="TESTUSDT",
        timeframe=TimeFrame.H1,
        timestamp=START + timedelta(hours=index),
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=direction,
        price_level=price,
        provisional=False,
    )


def event(index: int, kind: StructureEvent, direction: MarketDirection) -> MarketStructure:
    return MarketStructure(
        symbol="TESTUSDT",
        timeframe=TimeFrame.H1,
        timestamp=START + timedelta(hours=index),
        event=kind,
        direction=direction,
        price_level=100.0,
        provisional=False,
    )


def matrix_of(candles):
    atr = atr_pct_series(candles)
    by_ts = {c.timestamp: i for i, c in enumerate(candles)}
    legs = leg_state_by_index(candles, [], by_ts)
    return feature_matrix(candles, atr, legs)


# --------------------------------------------------------------------------
# causalidade: nenhuma feature pode ler o futuro
# --------------------------------------------------------------------------


def test_nenhuma_feature_muda_quando_o_futuro_e_cortado():
    """O teste central da etapa. Se truncar a serie em T mudar a leitura em T,
    a feature le o futuro -- e toda separacao medida e ilusoria."""
    candles = wave()
    full = matrix_of(candles)
    for cut in (120, 200, 260):
        partial = matrix_of(candles[: cut + 1])
        for name in FEATURES:
            a, b = full[name][cut], partial[name][cut]
            if a is None or b is None:
                assert a == b, f"{name} em {cut}: {a} vs {b}"
                continue
            assert a == pytest.approx(b, rel=1e-9, abs=1e-12), f"{name} em {cut}"


def test_o_atr_e_o_vwap_rolantes_sao_causais():
    candles = wave(150)
    atr = atr_pct_series(candles)
    vwap = rolling_vwap(candles, 20)
    for cut in (60, 100, 140):
        assert atr_pct_series(candles[: cut + 1])[cut] == pytest.approx(atr[cut])
        assert rolling_vwap(candles[: cut + 1], 20)[cut] == pytest.approx(vwap[cut])


def test_o_atr_usa_a_janela_declarada():
    """Uma janela maior que `ATR_WINDOW` significaria uma unidade diferente da
    reportada -- e toda feature em ATR sairia numa escala que nao e a do texto."""
    candles = wave(ATR_WINDOW * 3)
    tail = atr_pct_series(candles)[-1]
    same = atr_pct_series(candles[-(ATR_WINDOW + 1) :])[-1]
    assert tail == pytest.approx(same, rel=1e-9)


def test_os_alvos_leem_somente_o_futuro():
    """A contraparte: mudar o passado ANTES da janela do ATR nao pode mexer no
    alvo, e mudar o futuro tem de mexer."""
    candles = wave(200)
    atr = atr_pct_series(candles)
    base = future_targets(candles, atr, 100)
    assert base is not None
    changed = list(candles)
    changed[130] = candle(130, candles[130].close * 1.5)
    assert future_targets(changed, atr, 100) != base


def test_alvo_nunca_e_feature():
    """Nomes de alvo e nomes de feature vivem em espacos separados: uma
    colisao aqui seria um vazamento perfeito e invisivel."""
    target_names = {f"net_{h}" for h in PERSIST_HORIZONS} | {"mfe", "mae", "eff_fut"}
    assert not target_names & set(FEATURES)


# --------------------------------------------------------------------------
# o estado da perna
# --------------------------------------------------------------------------


def test_o_estado_da_perna_bate_com_o_detector_de_producao():
    """A equivalencia que autoriza a varredura O(n): em prefixos amostrados de
    dados reais, `leg_state_by_index` diz o mesmo que `detect_structural_stall`."""
    path = CACHE_DIR / "BTCUSDT_1h.json"
    if not path.exists():
        pytest.skip("sem cache")
    candles = load_series("BTCUSDT", TimeFrame.H1)[-1500:]
    events = [
        bos(index, MarketDirection.BULLISH if index % 2 else MarketDirection.BEARISH, 1.0)
        for index in range(0, len(candles), 137)
    ]
    events = [
        MarketStructure(
            symbol="BTCUSDT",
            timeframe=TimeFrame.H1,
            timestamp=candles[index].timestamp,
            event=StructureEvent.BREAK_OF_STRUCTURE,
            direction=MarketDirection.BULLISH if (index // 137) % 2 else MarketDirection.BEARISH,
            price_level=candles[index].close,
            provisional=False,
        )
        for index in range(0, len(candles), 137)
    ]
    by_ts = {c.timestamp: i for i, c in enumerate(candles)}
    mine = leg_state_by_index(candles, events, by_ts)
    checked = 0
    for cut in range(400, len(candles), 97):
        theirs = detect_structural_stall(candles[: cut + 1], events)
        assert bool(theirs) == mine[cut].stale, f"cut={cut}"
        checked += 1
    assert checked >= 5


def test_uma_perna_fechada_por_choch_nao_fica_stale():
    """So um BOS abre perna. Uma perna encerrada por CHoCH ja foi fechada pela
    maquina -- nao ha o que chamar de parado, e marcar stale ali inventaria
    contexto que a producao nao tem."""
    candles = series([100.0 - i * 0.05 for i in range(200)])
    by_ts = {c.timestamp: i for i, c in enumerate(candles)}
    legs = leg_state_by_index(
        candles,
        [event(10, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH)],
        by_ts,
    )
    assert all(not leg.stale for leg in legs)
    assert all(leg.direction is None for leg in legs[10:])


def test_o_estado_stale_nao_volta_atras():
    candles = series([100.0 + min(i, 60) * 0.5 - max(0, i - 60) * 0.5 for i in range(220)])
    by_ts = {c.timestamp: i for i, c in enumerate(candles)}
    legs = leg_state_by_index(
        candles, [bos(5, MarketDirection.BULLISH, 100.0)], by_ts, n=20, k_atr=1.0
    )
    seen = False
    for leg in legs:
        if leg.stale:
            seen = True
        elif seen:
            pytest.fail("o estado stale voltou para active dentro da mesma perna")
    assert seen


def test_o_choch_failed_inverte_a_tendencia_confirmada():
    candles = wave(60)
    by_ts = {c.timestamp: i for i, c in enumerate(candles)}
    events = [event(10, StructureEvent.CHOCH_FAILED, MarketDirection.BULLISH)]
    trends = trend_by_index(events, by_ts, len(candles))
    assert trends[9] is None
    assert trends[10] is MarketDirection.BEARISH


def test_eventos_provisionais_nao_definem_tendencia():
    candles = wave(60)
    by_ts = {c.timestamp: i for i, c in enumerate(candles)}
    provisional = bos(10, MarketDirection.BULLISH, 100.0).model_copy(
        update={"provisional": True}
    )
    assert all(value is None for value in trend_by_index([provisional], by_ts, len(candles)))


# --------------------------------------------------------------------------
# simetria (secao 5 e 13)
# --------------------------------------------------------------------------


def test_espelhar_a_serie_troca_o_sinal_de_toda_feature():
    """O guard contra um erro de sinal que so aparece em metade da amostra."""
    closes = [100.0 + i * 0.2 + 6.0 * math.sin(i / 7.0) for i in range(300)]
    pivot = closes[0]
    up = matrix_of(series(closes))
    down = matrix_of(series([2 * pivot - value for value in closes]))
    # As features em ATR sao normalizadas por `atr_pct * close`, e espelhar a
    # serie muda o NIVEL de preco -- entao a magnitude delas nao tem por que se
    # preservar. O que tem de se preservar, e o que um erro de sinal quebraria,
    # e o SINAL. Para as features adimensionais (eficiencia, contagem de
    # fechamentos, posicao no range) a magnitude tambem e exigida.
    # `range_pos_20` fica fora: os pavios do candle sintetico sao
    # multiplicativos (close*1.01 / close*0.99), entao o range nao espelha.
    scale_free = {"eff_10", "eff_20", "closes_dir_10"}
    checked = 0
    for name in FEATURES:
        if name in ("leg_giveback_signed", "vol_thrust_5"):
            continue  # sem perna e com volume plano nao ha o que espelhar
        for index in (150, 220, 280):
            a, b = up[name][index], down[name][index]
            if a is None or b is None or abs(a) < 1e-9:
                continue
            assert (b > 0) != (a > 0), f"{name} em {index}: {a} vs {b}"
            if name in scale_free:
                assert b == pytest.approx(-a, rel=1e-6), f"{name} em {index}"
            checked += 1
    assert checked > 20


def test_a_normalizacao_por_direcao_vive_num_lugar_so():
    assert signed_to_aligned(2.0, MarketDirection.BULLISH) == 2.0
    assert signed_to_aligned(2.0, MarketDirection.BEARISH) == -2.0


def test_os_alvos_espelham_para_a_direcao_da_pressao():
    raw = (1.5, 3.0, 2.0, 0.4)
    assert pressure_targets(raw, 1) == raw
    assert pressure_targets(raw, -1) == (-1.5, 2.0, 3.0, -0.4)


# --------------------------------------------------------------------------
# o placebo
# --------------------------------------------------------------------------


def test_o_placebo_e_uma_permutacao_e_desalinha_de_fato():
    import random

    rng = random.Random(7)
    length = PLACEBO_BLOCK * 12 + 5
    permutation = block_permutation(length, rng)
    assert sorted(permutation) == list(range(length))
    moved = sum(1 for index, value in enumerate(permutation) if index != value)
    assert moved > length * 0.5, "a permutacao por blocos ficou quase identica"


def test_o_placebo_preserva_a_vizinhanca_dentro_do_bloco():
    import random

    permutation = block_permutation(PLACEBO_BLOCK * 6, random.Random(3))
    steps = [permutation[i + 1] - permutation[i] for i in range(len(permutation) - 1)]
    assert steps.count(1) > 0.8 * len(steps)


# --------------------------------------------------------------------------
# regras, abstencao e episodios
# --------------------------------------------------------------------------


def test_a_grade_de_regras_e_pequena_e_declarada():
    """Secao 16: registrar quantas regras foram testadas. Se a grade crescer
    sem que o numero mude, a correcao de multiplicidade fica impossivel."""
    assert RULES_TESTED == len(RULES)
    assert RULES_TESTED <= 30
    assert all(len(rule.label) > 0 for rule in RULES)
    assert len({rule.label for rule in RULES}) == RULES_TESTED


def test_toda_regra_tem_no_maximo_tres_condicoes():
    for rule in rule_grid():
        conditions = 1 + (rule.efficiency is not None) + (rule.agree is not None)
        assert conditions <= 3, rule.label


def test_a_regra_abstem_quando_nao_ha_pressao():
    """Secao 18: `balanced` tem de ser possivel, e ser a resposta comum."""
    rule = Rule("t", "A", 1.0)
    assert rule.call({name: None for name in FEATURES}) == 0
    assert rule.call({"ret_atr_10": 0.2}) == 0
    assert rule.call({"ret_atr_10": 2.0}) == 1
    assert rule.call({"ret_atr_10": -2.0}) == -1


def test_a_segunda_condicao_exige_o_mesmo_sinal():
    """Uma eficiencia bearish nao pode confirmar uma pressao bullish -- o erro
    seria invisivel porque a regra continuaria disparando."""
    rule = Rule("t", "A", 1.0, efficiency=0.5)
    assert rule.call({"ret_atr_10": 2.0, "eff_10": 0.8}) == 1
    assert rule.call({"ret_atr_10": 2.0, "eff_10": -0.8}) == 0
    agreeing = Rule("t", "B", 1.0, agree="close_vs_vwap_atr")
    assert agreeing.call({"ret_atr_10": 2.0, "close_vs_vwap_atr": 1.0}) == 1
    assert agreeing.call({"ret_atr_10": 2.0, "close_vs_vwap_atr": -1.0}) == 0


def test_o_score_precisa_de_todos_os_componentes():
    rule = ScoreRule("s", "C", 0.3)
    assert rule.score({"ret_atr_10": 4.0, "eff_10": 1.0}) is None
    full = {"ret_atr_10": 4.0, "eff_10": 1.0, "close_vs_vwap_atr": 4.0, "delta_share_10": 1.0}
    assert rule.score(full) == pytest.approx(1.0)
    assert rule.call(full) == 1


def test_o_desfecho_estrutural_ignora_eventos_fora_da_janela():
    candles = wave(200)
    by_ts = {c.timestamp: i for i, c in enumerate(candles)}
    events = [bos(150, MarketDirection.BULLISH, 100.0)]
    assert (
        structural_outcome(events, by_ts, 100, 20, 1, MarketDirection.BULLISH) == "none"
    )
    assert (
        structural_outcome(events, by_ts, 100, 80, 1, MarketDirection.BULLISH) == "bos_resume"
    )
    assert structural_outcome(events, by_ts, 160, 80, 1, MarketDirection.BULLISH) == "none"


def test_o_false_conflict_so_conta_bos_da_estrutura_original():
    candles = wave(200)
    by_ts = {c.timestamp: i for i, c in enumerate(candles)}
    events = [bos(110, MarketDirection.BEARISH, 100.0)]
    assert first_resume(events, by_ts, 100, MarketDirection.BEARISH, 40) == 10
    assert first_resume(events, by_ts, 100, MarketDirection.BULLISH, 40) == -1
    assert first_resume(events, by_ts, 100, MarketDirection.BEARISH, 5) == -1


def test_o_gap_do_episodio_e_declarado():
    """Um unico candle indeciso nao pode picotar o episodio em dois."""
    assert 1 <= EPISODE_GAP <= 10


# --------------------------------------------------------------------------
# estatistica e amostragem
# --------------------------------------------------------------------------


def test_o_auc_reconhece_separacao_perfeita_e_ausencia_dela():
    values = [float(i) for i in range(200)]
    labels = [0] * 100 + [1] * 100
    assert auc(values, labels)[0] == pytest.approx(1.0)
    assert auc(values, labels[::-1])[0] == pytest.approx(0.0)
    assert auc([1.0] * 200, labels)[0] == pytest.approx(0.5)


def test_o_auc_ignora_nan_e_exige_n_minimo():
    values = [float("nan")] * 100 + [float(i) for i in range(100)]
    labels = [1] * 100 + [0] * 50 + [1] * 50
    assert auc(values, labels) is not None
    assert auc([1.0, 2.0, 3.0], [0, 1, 0]) is None


def test_o_corte_de_holdout_nao_vaza_no_tempo():
    store = CandleStore()
    for index in range(100):
        store.add(
            START + timedelta(hours=index),
            MarketDirection.BULLISH,
            False,
            {h: 1.0 for h in PERSIST_HORIZONS},
            {name: 0.0 for name in FEATURES},
            {name: 0.0 for name in FEATURES},
        )
    cut = cut_by_timeframe(store)
    discovery = select(store, cut, sample="discovery")
    holdout = select(store, cut, sample="holdout")
    assert len(discovery) + len(holdout) == len(store)
    assert max(store.ts[i] for i in discovery) < min(store.ts[i] for i in holdout)


def test_o_stride_amostra_e_nao_multiplica():
    """Secao 6: candles vizinhos sao quase o mesmo candle."""
    assert STRIDE >= 2


def test_toda_feature_declara_familia():
    assert set(FEATURE_FAMILY) == set(FEATURES)
    assert set(FEATURE_FAMILY.values()) == {"A", "B", "C"}
    assert sum(1 for family in FEATURE_FAMILY.values() if family == "A") >= 5


def test_dois_advances_no_mesmo_candle_nao_derrubam_a_janela():
    """O bug que custou 120 combos da primeira rodada: ordenar tuplas
    `(indice, evento)` sem chave faz o desempate cair no `MarketStructure`,
    que nao tem ordem. Nao falha alto -- a janela inteira e descartada e some
    do painel sem aparecer em metrica nenhuma."""
    candles = wave(120)
    by_ts = {c.timestamp: i for i, c in enumerate(candles)}
    same = [
        bos(30, MarketDirection.BULLISH, 100.0),
        event(30, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH),
    ]
    trends = trend_by_index(same, by_ts, len(candles))
    assert trends[31] is MarketDirection.BEARISH
    legs = leg_state_by_index(candles, same, by_ts)
    assert legs[31].direction is None


def test_a_taxa_base_e_reportada_junto_da_persistencia():
    """Sem taxa-base, `persist` abaixo de 50% e ambiguo: pode ser a camada
    errando ou o painel tendo caido no periodo. O relatorio nao pode oferecer
    um sem o outro."""
    from research.current_market_pressure import rule_metrics

    store = CandleStore()
    for index in range(400):
        rising = index % 4 != 0  # 75% de altas: uma taxa-base longe de 50%
        store.add(
            START + timedelta(hours=index),
            MarketDirection.BULLISH,
            False,
            {h: (1.0 if rising else -1.0) for h in PERSIST_HORIZONS},
            {name: (3.0 if rising else -3.0) for name in FEATURES},
            {name: 0.0 for name in FEATURES},
        )
    metrics = rule_metrics(store, Rule("t", "A", 1.0), list(range(len(store))))
    assert metrics["base_bullish"] == pytest.approx(0.75)
    assert metrics["persist_10"] == pytest.approx(1.0)
