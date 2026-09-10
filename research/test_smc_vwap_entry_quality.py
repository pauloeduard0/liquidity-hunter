"""Testes da Etapa 7.0.

Seis maneiras de esta medicao mentir sem quebrar:

1. **lookahead na feature de entrada** -- `location` e lida no candle da
   entrada, e nada dela pode depender de um candle posterior. E o risco mais
   caro aqui porque uma feature que enxerga o futuro produz um resultado
   *bonito*, nao um erro.
2. **a simulacao resolver empates a favor do trader** -- dentro de um candle
   nao da para saber se o stop ou o alvo veio primeiro; creditar o alvo
   fabrica uma taxa de acerto que nenhuma execucao reproduz.
3. **o recorte trocar a populacao** -- um filtro de VWAP tem que ser um
   SUBCONJUNTO do baseline SMC. Se ele adicionar um trade que o baseline nao
   tinha, deixou de medir "a VWAP melhora este setup" e passou a medir outro
   setup.
4. **o denominador por dia encolher junto com o filtro** -- um filtro que corta
   80% dos trades nao pode ganhar um denominador menor de presente, ou toda
   selecao parece melhorar a conta por dia.
5. **o custo sumir** -- um stop mais apertado e mais barato em preco e mais
   caro em R, e e exatamente isso que "entrar perto da VWAP" faz.
6. **assimetria escondida** -- long e short precisam ler o mesmo numero sobre
   series espelhadas.
"""

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
from research.smc_vwap_entry_quality import (
    CONTROL_CUTS,
    EMA_PERIOD,
    MAX_TESTS,
    RETEST_WINDOW,
    ROUND_TRIP,
    TARGET_HORIZON,
    TARGETS,
    VWAP_CUTS,
    Trade,
    build_structure_trades,
    cut_by_tf,
    net_r,
    simulate,
    span_days,
    structure_retests,
    summarize,
    tag_vwap,
)
from research.tide_structural_transition import phase_series, tide_geometry

START = datetime(2026, 1, 1, tzinfo=UTC)
BULL = MarketDirection.BULLISH
BEAR = MarketDirection.BEARISH


def candle(
    index: int,
    close: float,
    *,
    high: float | None = None,
    low: float | None = None,
    volume: float = 100.0,
) -> Candle:
    return Candle(
        symbol="TESTUSDT",
        timeframe=TimeFrame.H1,
        timestamp=START + timedelta(hours=index),
        open=close,
        high=close * 1.01 if high is None else high,
        low=close * 0.99 if low is None else low,
        close=close,
        volume=volume,
        taker_buy_volume=volume / 2,
    )


def series(prices) -> list[Candle]:
    return [candle(i, price) for i, price in enumerate(prices)]


def wave(n: int = 300) -> list[Candle]:
    return series([100.0 + i * 0.2 + 6.0 * math.sin(i / 7.0) for i in range(n)])


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
        ts=int((START + timedelta(hours=20)).timestamp()),
        test_ordinal=1,
        r_atr=1.0,
        pullback_atr=1.0,
        retrace_pct=50.0,
        swing_dist_atr=1.0,
        penetration_atr=0.5,
    )
    base.update(over)
    return Trade(**base)


# --------------------------------------------------------------------------
# 1. a simulacao
# --------------------------------------------------------------------------


def test_o_stop_leva_o_credito_quando_os_dois_cabem_no_mesmo_candle():
    """Dentro de um candle a ordem e desconhecida; creditar o alvo e otimismo.

    O candle abaixo toca o stop (99) E o alvo de 2R (102) ao mesmo tempo. A
    convencao do repositorio (`vwap_ob_pinbar._measure`) da o candle ao lado
    adverso, e trocar isso inflaria a taxa de acerto de toda a etapa.
    """
    candles = [candle(i, 100.0) for i in range(3)]
    candles.append(candle(3, 100.0, high=103.0, low=98.0))
    candles += [candle(i, 100.0) for i in range(4, 200)]
    trade = a_trade(entry_index=2)
    simulate(trade, candles)
    assert trade.r_grid[2.0] == -1.0
    assert trade.hit[2.0] == 0.0


def test_o_alvo_e_o_stop_sao_fixados_no_entry_e_nao_seguem_o_preco():
    candles = [candle(i, 100.0) for i in range(2)]
    # sobe ate 2R (102) sem nunca tocar 99
    candles += [candle(i, 100.0 + 0.5 * (i - 1), low=99.5, high=100.0 + 0.5 * (i - 1))
                for i in range(2, 200)]
    trade = a_trade(entry_index=1)
    simulate(trade, candles)
    assert trade.hit[2.0] == 1.0
    assert trade.r_grid[2.0] == 2.0
    assert trade.time_to_2r == 4.0


def test_sem_stop_nem_alvo_o_trade_e_marcado_a_mercado():
    candles = [candle(i, 100.0, high=100.2, low=99.8) for i in range(200)]
    candles[TARGET_HORIZON] = candle(TARGET_HORIZON, 100.5, high=100.6, low=99.8)
    trade = a_trade(entry_index=0)
    simulate(trade, candles)
    assert trade.hit[2.0] == 0.0
    assert -1.0 < trade.r_grid[2.0] < 2.0


def test_mfe_e_mae_param_na_resolucao_do_trade():
    """Um trade estopado no 3o candle nao sofreu a excursao dos 117 seguintes.

    Medir ate o fim do horizonte sempre produzia MAE de centenas de R -- um
    numero que nao descreve nenhuma posicao real, e que contamina toda media.
    """
    candles = [candle(0, 100.0), candle(1, 100.0, high=100.1, low=99.9)]
    candles.append(candle(2, 98.5, high=100.0, low=98.0))  # estopa
    # depois disso, um colapso que nao pertence mais ao trade
    candles += [candle(i, 10.0, high=11.0, low=9.0) for i in range(3, 200)]
    trade = a_trade(entry_index=0)
    simulate(trade, candles)
    assert trade.r_grid[2.0] == -1.0
    assert trade.mae_r == pytest.approx(2.0)  # (100 - 98) / 1
    assert trade.mae_r < 5.0


# --------------------------------------------------------------------------
# 2. custo
# --------------------------------------------------------------------------


def test_o_custo_em_R_cresce_quando_o_stop_aperta():
    """A tensao central da etapa: entrar perto da VWAP costuma apertar o stop.

    O round trip e uma fracao do PRECO; convertido para R ele fica maior
    quanto menor for o stop. Uma etapa que reportasse so o bruto esconderia
    exatamente o custo do que ela esta testando.
    """
    wide = a_trade(entry=100.0, stop=98.0, r=2.0)
    tight = a_trade(entry=100.0, stop=99.5, r=0.5)
    for trade in (wide, tight):
        trade.r_grid[2.0] = 0.0
    assert net_r(wide) == pytest.approx(-ROUND_TRIP / 0.02)
    assert net_r(tight) == pytest.approx(-ROUND_TRIP / 0.005)
    assert net_r(tight) < net_r(wide)


# --------------------------------------------------------------------------
# 3. o recorte nao pode trocar a populacao
# --------------------------------------------------------------------------


def _pool() -> list[Trade]:
    pool = []
    for i in range(300):
        trade = a_trade(
            entry_index=i,
            ts=int((START + timedelta(hours=i)).timestamp()),
            test_ordinal=1 + i % 3,
        )
        trade.vwap_dist_sigma = (i % 40) / 10.0 - 1.5
        trade.vwap_slope_atr = 1.0 if i % 2 else -1.0
        trade.vwap_first_touch = float(i % 3 == 0)
        trade.vwap_reclaim = float(i % 5 == 0)
        trade.vwap_reject = float(i % 7 == 0)
        trade.vwap_between_stop = float(i % 4 == 0)
        trade.vwap_between_target = float(i % 6 == 0)
        trade.vwap_phase = 10.0 * (i % 10)
        trade.ema_side = float(i % 2)
        trade.ema_dist_atr = (i % 20) / 20.0
        trade.hit = {k: float(i % 3 == 0) for k in TARGETS}
        trade.r_grid = {k: (k if i % 3 == 0 else -1.0) for k in TARGETS}
        trade.mfe_r = 1.0
        trade.mae_r = 1.0
        trade.stopped_first = float(i % 3 != 0)
        pool.append(trade)
    return pool


def test_todo_recorte_de_vwap_e_subconjunto_do_baseline():
    pool = _pool()
    identity = {id(t) for t in pool}
    for label, predicate in {**VWAP_CUTS, **CONTROL_CUTS}.items():
        if predicate is None:
            continue
        subset = [t for t in pool if predicate(t)]
        assert {id(t) for t in subset} <= identity, label
        assert len(subset) <= len(pool), label


def test_nenhum_recorte_le_um_campo_de_desfecho():
    """Um filtro que consulta o resultado nao e um filtro, e uma resposta.

    Aqui os campos de desfecho sao apagados; todo predicado tem que continuar
    decidivel. Se algum quebrar ou mudar de resposta, ele estava lendo o alvo.
    """
    pool = _pool()
    before = {
        label: [predicate(t) for t in pool]
        for label, predicate in {**VWAP_CUTS, **CONTROL_CUTS}.items()
        if predicate is not None
    }
    for trade in pool:
        trade.hit = {}
        trade.r_grid = {}
        trade.mfe_r = float("nan")
        trade.mae_r = float("nan")
        trade.stopped_first = float("nan")
        trade.time_to_1r = float("nan")
        trade.time_to_2r = float("nan")
    for label, predicate in {**VWAP_CUTS, **CONTROL_CUTS}.items():
        if predicate is None:
            continue
        assert [predicate(t) for t in pool] == before[label], label


# --------------------------------------------------------------------------
# 4. a conta por dia
# --------------------------------------------------------------------------


def test_o_denominador_por_dia_e_o_do_POOL_e_nao_o_do_recorte():
    """Um filtro que corta trades nao ganha um denominador menor de presente.

    Sem isto, selecionar os poucos trades bons de uma janela longa mostraria
    um `net_per_day` inflado por ter tambem encolhido o periodo.
    """
    pool = _pool()
    subset = [t for t in pool if t.entry_index < 30]
    got = summarize(subset, pool)
    assert got["net_per_day"] == pytest.approx(
        sum(net_r(t) for t in subset) / span_days(pool)
    )
    # o span do recorte e MENOR: se ele fosse o denominador, o numero mudaria
    assert span_days(subset) < span_days(pool)


def test_dias_sao_contados_POR_combo_e_nao_no_calendario_global():
    """Somar o calendario global daria o mesmo denominador para 1 simbolo e 70."""
    one = [a_trade(ts=int((START + timedelta(hours=h)).timestamp())) for h in (0, 240)]
    two = one + [
        a_trade(symbol="OTHERUSDT", ts=int((START + timedelta(hours=h)).timestamp()))
        for h in (0, 240)
    ]
    assert span_days(one) == pytest.approx(10.0)
    assert span_days(two) == pytest.approx(20.0)


# --------------------------------------------------------------------------
# 5. o scanner de reteste
# --------------------------------------------------------------------------


def test_o_reteste_e_do_nivel_ROMPIDO_e_nao_do_extremo_novo():
    """`reference_price_level` e o nivel quebrado; `price_level` e o extremo novo.

    Trocar os dois faz o scanner procurar o preco voltar a um lugar onde ele
    nunca esteve -- e devolver zero setups, sem erro nenhum.
    """
    # o evento precisa de ATR acumulado, entao ele nasce depois do warm-up
    prices = [100.0] * 60 + [105.0] * 5 + [101.5] + [106.0] * 200
    candles = series(prices)
    events = [
        MarketStructure(
            symbol="TESTUSDT",
            timeframe=TimeFrame.H1,
            timestamp=candles[60].timestamp,
            event=StructureEvent.BREAK_OF_STRUCTURE,
            direction=BULL,
            price_level=105.0,
            reference_price_level=101.0,
            provisional=False,
        )
    ]
    by_ts = {c.timestamp: i for i, c in enumerate(candles)}
    atr = [0.02] * len(candles)
    trades = build_structure_trades(
        "TESTUSDT", TimeFrame.H1, candles, events, by_ts, atr,
        StructureEvent.BREAK_OF_STRUCTURE, "bos_retest",
    )
    assert trades, "o nivel rompido (101) e retestado no candle 65"
    assert trades[0].entry_index == 65


def test_a_varredura_para_no_primeiro_fechamento_do_lado_errado():
    # o rompimento deixou de valer; o que vier depois nao e reteste deste evento
    prices = [102.0, 100.5, 102.0, 100.2, 102.0]
    candles = series(prices)
    got = structure_retests(candles, 0, 101.0, BULL)
    assert got == []


def test_o_reteste_exige_o_pavio_no_nivel_E_o_fechamento_defendendo():
    candles = [
        candle(0, 102.0, low=101.5),
        candle(1, 103.0, low=100.9),   # pavio no nivel, fechamento defende
        candle(2, 102.0, low=101.5),   # nao chega no nivel
    ]
    assert structure_retests(candles, 0, 101.0, BULL) == [1]
    # sem tocar o nivel em candle nenhum, nao ha reteste
    assert structure_retests(candles, 0, 90.0, BULL) == []


def test_no_maximo_MAX_TESTS_revisitas_e_elas_sao_numeradas():
    candles = [candle(0, 102.0, low=101.5)] + [
        candle(i, 102.0, low=100.9) for i in range(1, 10)
    ]
    got = structure_retests(candles, 0, 101.0, BULL)
    assert len(got) == MAX_TESTS
    assert got == [1, 2, 3]


def test_a_janela_de_reteste_e_a_declarada():
    def flat(n: int) -> list[Candle]:
        return [candle(i, 102.0, low=101.5) for i in range(n)]

    late = flat(80)
    late[RETEST_WINDOW + 1] = candle(RETEST_WINDOW + 1, 102.0, low=100.9)
    assert structure_retests(late, 0, 101.0, BULL) == []
    edge = flat(80)
    edge[RETEST_WINDOW] = candle(RETEST_WINDOW, 102.0, low=100.9)
    assert structure_retests(edge, 0, 101.0, BULL) == [RETEST_WINDOW]


def test_a_profundidade_do_pullback_nao_le_candle_posterior_a_entrada():
    """O extremo da perna vai ate o candle de ENTRADA, nao ate o fim da janela.

    Este e o vazamento mais caro que esta populacao pode ter, porque ele
    *melhora* o resultado em vez de quebrar alguma coisa: se o extremo puder
    vir de um candle posterior a entrada, `pullback_atr` sabe quanto o preco
    ainda vai subir, e um teste que so olhasse a VWAP nunca veria isso.

    A serie abaixo faz um topo MUITO maior depois do reteste. Com o extremo
    causal a profundidade e pequena; com o extremo da janela inteira ela seria
    enorme.
    """
    prices = (
        [100.0] * 60          # warm-up de ATR
        + [110.0] * 3         # rompe
        + [104.0]             # reteste do nivel 103
        + [400.0] * 200       # o futuro, que a feature NAO pode enxergar
    )
    candles = series(prices)
    events = [
        MarketStructure(
            symbol="TESTUSDT",
            timeframe=TimeFrame.H1,
            timestamp=candles[60].timestamp,
            event=StructureEvent.BREAK_OF_STRUCTURE,
            direction=BULL,
            price_level=110.0,
            reference_price_level=103.0,
            provisional=False,
        )
    ]
    by_ts = {c.timestamp: i for i, c in enumerate(candles)}
    atr = [0.02] * len(candles)
    trades = build_structure_trades(
        "TESTUSDT", TimeFrame.H1, candles, events, by_ts, atr,
        StructureEvent.BREAK_OF_STRUCTURE, "bos_retest",
    )
    assert trades
    trade = trades[0]
    unit = atr[trade.entry_index] * trade.entry
    # o extremo causal e o topo de 110 (x1.01 de pavio), nao os 400 do futuro
    causal = max(c.high for c in candles[60 : trade.entry_index + 1])
    assert trade.pullback_atr == pytest.approx((causal - trade.entry) / unit)
    assert trade.pullback_atr < 10.0


SMC_CONTEXT_FIELDS = (
    "pullback_atr",
    "retrace_pct",
    "impulse_atr",
    "leg_atr",
    "swing_dist_atr",
    "penetration_atr",
    "r_atr",
)


def test_nenhuma_feature_de_CONTEXTO_SMC_muda_quando_o_futuro_e_cortado():
    """O mesmo teste de truncamento que as features de VWAP ja tinham.

    A causalidade precisa ser verificada **por familia de feature**, e nao so
    na familia que a etapa esta investigando. Foi exatamente essa assimetria
    que deixou o vazamento passar: na 7.0 a VWAP tinha teste de truncamento e
    o contexto SMC nao, e o efeito mais forte do painel apareceu justamente na
    familia desprotegida.

    Aqui a serie e cortada NO candle de entrada de cada trade, e toda leitura
    de contexto tem de sair identica. Com a implementacao antiga -- extremo da
    perna lido ate o fim da janela de reteste -- `pullback_atr`, `retrace_pct`,
    `impulse_atr` e `leg_atr` divergem.
    """
    candles = wave(400)
    events = [
        MarketStructure(
            symbol="TESTUSDT",
            timeframe=TimeFrame.H1,
            timestamp=candles[i].timestamp,
            event=StructureEvent.BREAK_OF_STRUCTURE,
            direction=BULL,
            price_level=candles[i].high,
            reference_price_level=candles[i].close * 0.995,
            origin_price_level=candles[i].close * 0.97,
            provisional=False,
        )
        for i in range(60, 250, 6)
    ]
    atr = [0.01] * len(candles)

    def collect(window):
        by_ts = {c.timestamp: i for i, c in enumerate(window)}
        return build_structure_trades(
            "TESTUSDT", TimeFrame.H1, window, events, by_ts, atr,
            StructureEvent.BREAK_OF_STRUCTURE, "bos_retest",
        )

    full = {t.entry_index: t for t in collect(candles)}
    assert len(full) > 5, "sem trades nao ha o que comparar"
    checked = 0
    for entry_index, trade in full.items():
        # a serie cortada precisa de horizonte para simular; o que se compara
        # sao as features, entao basta que o trade seja CONSTRUIDO ali
        cut = candles[: entry_index + 1] + [
            candle(entry_index + 1 + k, candles[entry_index].close)
            for k in range(TARGET_HORIZON + 2)
        ]
        rebuilt = {t.entry_index: t for t in collect(cut)}
        other = rebuilt.get(entry_index)
        if other is None:
            continue
        checked += 1
        for name in SMC_CONTEXT_FIELDS:
            a, b = getattr(trade, name), getattr(other, name)
            assert (math.isnan(a) and math.isnan(b)) or a == pytest.approx(b), (
                f"{name} @ {entry_index}"
            )
    assert checked > 5


def test_o_pullback_e_medido_do_extremo_ate_a_entrada_e_e_nao_negativo():
    candles = wave(300)
    events = [
        MarketStructure(
            symbol="TESTUSDT",
            timeframe=TimeFrame.H1,
            timestamp=candles[i].timestamp,
            event=StructureEvent.BREAK_OF_STRUCTURE,
            direction=BULL,
            price_level=candles[i].high,
            reference_price_level=candles[i].close * 0.995,
            provisional=False,
        )
        for i in range(60, 240, 7)
    ]
    by_ts = {c.timestamp: i for i, c in enumerate(candles)}
    atr = [0.01] * len(candles)
    trades = build_structure_trades(
        "TESTUSDT", TimeFrame.H1, candles, events, by_ts, atr,
        StructureEvent.BREAK_OF_STRUCTURE, "bos_retest",
    )
    assert trades
    for trade in trades:
        assert trade.pullback_atr >= 0.0
        if not math.isnan(trade.impulse_atr) and trade.impulse_atr > 0:
            assert trade.retrace_pct == pytest.approx(
                100.0 * trade.pullback_atr / trade.impulse_atr
            )


# --------------------------------------------------------------------------
# 6. causalidade das features de entrada
# --------------------------------------------------------------------------


def _tag(candles, trade):
    atr = [0.01] * len(candles)
    geometry = tide_geometry(candles, "TESTUSDT", TimeFrame.H1)
    phase = phase_series(candles, geometry)
    ema = ema_of(candles)
    tag_vwap(trade, candles, atr, geometry, phase, ema)
    return trade


def ema_of(candles):
    from liquidity_hunter.indicators.ema import ema_series

    return ema_series(candles, EMA_PERIOD)


VWAP_FIELDS = (
    "vwap_side",
    "vwap_dist_atr",
    "vwap_dist_sigma",
    "vwap_phase",
    "vwap_slope_atr",
    "vwap_width",
    "vwap_crossed_since",
    "vwap_touches_since",
    "vwap_first_touch",
    "vwap_time_near",
    "vwap_reclaim",
    "vwap_reject",
    "vwap_between_stop",
    "vwap_between_target",
    "ema_dist_atr",
    "ema_side",
)


def test_nenhuma_feature_de_entrada_muda_quando_o_futuro_e_cortado():
    candles = wave(300)
    entry = 130
    full = _tag(candles, a_trade(event_index=120, entry_index=entry,
                                entry=candles[entry].close,
                                stop=candles[entry].close * 0.99,
                                r=candles[entry].close * 0.01))
    cut = _tag(candles[: entry + 1], a_trade(event_index=120, entry_index=entry,
                                             entry=candles[entry].close,
                                             stop=candles[entry].close * 0.99,
                                             r=candles[entry].close * 0.01))
    for name in VWAP_FIELDS:
        a, b = getattr(full, name), getattr(cut, name)
        assert (math.isnan(a) and math.isnan(b)) or a == pytest.approx(b), name


def test_a_contagem_since_nao_passa_do_candle_de_entrada():
    candles = wave(300)
    early = _tag(candles, a_trade(event_index=120, entry_index=130,
                                  entry=candles[130].close,
                                  stop=candles[130].close * 0.99,
                                  r=candles[130].close * 0.01))
    late = _tag(candles, a_trade(event_index=120, entry_index=160,
                                 entry=candles[160].close,
                                 stop=candles[160].close * 0.99,
                                 r=candles[160].close * 0.01))
    assert late.vwap_touches_since >= early.vwap_touches_since
    assert late.vwap_crossed_since >= early.vwap_crossed_since


def test_vwap_entre_entry_e_stop_e_geometria_e_nao_desfecho():
    """Ele pergunta onde a LINHA esta, nao se o preco chegou nela."""
    candles = wave(300)
    entry = 140
    price = candles[entry].close
    geometry = tide_geometry(candles, "TESTUSDT", TimeFrame.H1)
    mid = geometry.mid[entry]
    assert mid is not None
    # stop colocado do outro lado da VWAP: a linha fica no meio, por construcao
    trade = a_trade(event_index=130, entry_index=entry, entry=price,
                    stop=mid - abs(price - mid) - 1.0,
                    r=abs(price - (mid - abs(price - mid) - 1.0)))
    _tag(candles, trade)
    assert trade.vwap_between_stop == 1.0


# --------------------------------------------------------------------------
# 7. simetria
# --------------------------------------------------------------------------


def test_long_e_short_leem_o_mesmo_numero_em_series_espelhadas():
    prices = [100.0 + i * 0.2 + 6.0 * math.sin(i / 7.0) for i in range(200)]
    up = series(prices)
    down = series([240.0 - price for price in prices])
    seen = 0
    # Toda a janela [event_index, entry] dentro de UM dia UTC: na virada de
    # ancora a fita tem um candle so acumulado, a variancia e zero a menos de
    # ruido de ponto flutuante, e qual das duas series ganha banda ali e
    # sorteio -- o teste passaria a medir esse sorteio.
    for entry in range(60, 180):
        if not 11 <= entry % 24 <= 23:
            continue
        a = a_trade(event_index=entry - 10, entry_index=entry, entry=up[entry].close,
                    stop=up[entry].close * 0.99, r=up[entry].close * 0.01)
        b = a_trade(direction=BEAR, event_index=entry - 10, entry_index=entry,
                    entry=down[entry].close, stop=down[entry].close * 1.01,
                    r=down[entry].close * 0.01)
        _tag(up, a)
        _tag(down, b)
        if math.isnan(a.vwap_dist_sigma) or math.isnan(b.vwap_dist_sigma):
            continue
        seen += 1
        assert a.vwap_side == b.vwap_side
        assert a.vwap_dist_sigma == pytest.approx(b.vwap_dist_sigma, abs=1e-6)
        assert a.vwap_crossed_since == b.vwap_crossed_since
    assert seen > 10


# --------------------------------------------------------------------------
# 8. o corte do holdout
# --------------------------------------------------------------------------


def test_o_corte_do_holdout_e_por_timeframe_e_deixa_70_por_cento_atras():
    trades = [
        a_trade(timeframe=TimeFrame.M15 if i < 50 else TimeFrame.H1, ts=i)
        for i in range(100)
    ]
    assert cut_by_tf(trades, TimeFrame.M15) == 35
    assert cut_by_tf(trades, TimeFrame.H1) == 85
    assert cut_by_tf(trades, TimeFrame.D1) == 0
