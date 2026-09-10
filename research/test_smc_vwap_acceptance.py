"""Testes da Etapa 6.0.

Cinco maneiras de esta medicao mentir sem quebrar:

1. **vazamento pela janela de aceitacao** -- e o risco central desta etapa,
   porque aceitacao e alvo vivem os dois no futuro do evento. Se um evento
   cujo desfecho se resolve DENTRO da janela de aceitacao continuar no
   denominador, a feature esta lendo o proprio alvo, e o resultado sera
   espetacular e falso.
2. **lookahead na feature** -- uma leitura de aceitacao em N que dependa de
   candles alem de `e+N`.
3. **polaridade do desfecho** -- `CHOCH_FAILED` carrega a direcao do CHoCH que
   caiu, nao a do trend que nasce. Trocar isso inverte o alvo inteiro sem
   quebrar nada.
4. **assimetria escondida** -- bullish e bearish precisam produzir a MESMA
   leitura sobre series espelhadas; se nao produzirem, o painel agregado mede
   a direcao do periodo.
5. **aceitacao ser momentum disfarcado** -- o controle por quintil de
   deslocamento tem que efetivamente apagar um "estado" construido para nao
   ter nenhum sinal proprio. Se nao apagar, ele nao esta controlando nada.
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
    VWAPAnchor,
)
from research.range_choch import CACHE_DIR, load_series
from research.smc_vwap_acceptance import (
    ACCEPTANCE,
    OUTCOME_CODE,
    PER_WINDOW,
    RETEST_SIGMA,
    WINDOWS_N,
    Rule,
    Store,
    acceptance_features,
    cut_by_tf,
    event_features,
    hit_broken,
    hit_failed,
    matched_rate,
    outcome_of,
    resolve_bins,
    rule_grid,
    trend_duration,
)
from research.tide_structural_transition import VWAP_ANCHOR, tide_geometry

START = datetime(2026, 1, 1, tzinfo=UTC)


def candle(index: int, close: float, *, volume: float = 100.0) -> Candle:
    return Candle(
        symbol="TESTUSDT",
        timeframe=TimeFrame.H1,
        timestamp=START + timedelta(hours=index),
        open=close,
        high=close * 1.01,
        low=close * 0.99,
        close=close,
        volume=volume,
        taker_buy_volume=volume / 2,
    )


def series(prices) -> list[Candle]:
    return [candle(i, price) for i, price in enumerate(prices)]


def wave(n: int = 200) -> list[Candle]:
    return series([100.0 + i * 0.2 + 6.0 * math.sin(i / 7.0) for i in range(n)])


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


def by_ts_of(candles):
    return {c.timestamp: i for i, c in enumerate(candles)}


CHOCH = StructureEvent.CHANGE_OF_CHARACTER
BOS = StructureEvent.BREAK_OF_STRUCTURE
FAILED = StructureEvent.CHOCH_FAILED
BULL = MarketDirection.BULLISH
BEAR = MarketDirection.BEARISH


# --------------------------------------------------------------------------
# 1. o vazamento que esta etapa pode cometer sozinha
# --------------------------------------------------------------------------


def _store_with(rows) -> Store:
    """Um `Store` minimo: cada linha e (outcome, lead), o resto neutro."""
    store = Store()
    for position, (outcome, lead) in enumerate(rows):
        for name in store.f:
            store.f[name].append(0.0)
        for name in store.i:
            store.i[name].append(0)
        store.i["outcome"][position] = OUTCOME_CODE[outcome]
        store.i["lead"][position] = lead
        store.i["ts"][position] = position
    return store


def test_o_desfecho_resolvido_dentro_da_janela_sai_do_denominador():
    # Um CHoCH que falha em 3 candles NAO pode contar no horizonte N=5: a
    # "aceitacao" teria sido medida depois de a falha ja ter acontecido.
    store = _store_with([("failed", 3), ("failed", 9), ("continued", 9)])
    for n, expected in ((1, [1, 1, 0]), (5, [None, 1, 0]), (10, [None, None, None])):
        hit = hit_failed(store, n)
        assert [hit(i) for i in range(3)] == expected


def test_reversed_e_open_nunca_entram_no_alvo_principal():
    # Nem um nem outro e "o evento se sustentou"; enfia-los em qualquer lado
    # do par fabrica separacao.
    store = _store_with([("reversed", 9), ("open", -1), ("continued", 9)])
    hit = hit_failed(store, 5)
    assert [hit(i) for i in range(3)] == [None, None, 0]


def test_o_alvo_secundario_acrescenta_reversed_e_so_ele():
    store = _store_with([("reversed", 9), ("failed", 9), ("open", -1), ("continued", 9)])
    hit = hit_broken(store, 5)
    assert [hit(i) for i in range(4)] == [1, 1, None, 0]


def test_a_amostra_encolhe_quando_a_janela_cresce():
    # Um painel que crescesse com N estaria medindo o passado com o futuro.
    store = _store_with([("failed", lead) for lead in range(1, 30)])
    counts = [
        sum(1 for i in range(len(store)) if hit_failed(store, n)(i) is not None)
        for n in WINDOWS_N
    ]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] > counts[-1]


# --------------------------------------------------------------------------
# 2. causalidade das features
# --------------------------------------------------------------------------


def _accept(candles, index, direction, n):
    geometry = tide_geometry(candles, "TESTUSDT", TimeFrame.H1)
    atr = [0.01] * len(candles)
    return acceptance_features(candles, atr, geometry, index, direction, n)


def test_nenhuma_feature_de_aceitacao_muda_quando_o_futuro_e_cortado():
    candles = wave(200)
    index = 120
    for n in WINDOWS_N:
        full = _accept(candles, index, BULL, n)
        cut = _accept(candles[: index + n + 1], index, BULL, n)
        assert full is not None and cut is not None
        for name in PER_WINDOW:
            a, b = full[name], cut[name]
            assert (math.isnan(a) and math.isnan(b)) or a == pytest.approx(b), name


def test_a_leitura_no_evento_nao_depende_de_nenhum_candle_posterior():
    from research.smc_vwap_acceptance import AT_EVENT
    from research.tide_structural_transition import phase_series

    candles = wave(200)
    index = 130  # meio de um dia UTC: a fita existe
    atr = [0.01] * len(candles)

    def read(window):
        geometry = tide_geometry(window, "TESTUSDT", TimeFrame.H1)
        phase = phase_series(window, geometry)
        return event_features(window, atr, geometry, phase, index, BULL)

    full = read(candles)
    cut = read(candles[: index + 1])
    assert full is not None and cut is not None
    for name in AT_EVENT:
        a, b = full[name], cut[name]
        assert (math.isnan(a) and math.isnan(b)) or a == pytest.approx(b), name


def test_o_envelope_e_o_da_producao_e_nao_um_reimplementado():
    # A ancora e por timeframe (`_VWAP_ANCHOR_PERIOD`). Se esta etapa medisse
    # com SESSION em H4 ela mediria outro indicador e o chamaria de Tide.
    from liquidity_hunter.app import dashboard_data as dd

    for timeframe, anchor in VWAP_ANCHOR.items():
        assert dd._VWAP_ANCHOR_PERIOD[timeframe] is anchor
    assert VWAP_ANCHOR[TimeFrame.H4] is VWAPAnchor.WEEK
    assert VWAP_ANCHOR[TimeFrame.D1] is VWAPAnchor.MONTH


# --------------------------------------------------------------------------
# 3. aritmetica da aceitacao
# --------------------------------------------------------------------------


def test_same_side_reclaim_e_cross_contam_o_que_dizem_contar():
    candles = wave(200)
    geometry = tide_geometry(candles, "TESTUSDT", TimeFrame.H1)
    atr = [0.01] * len(candles)
    index = 150
    got = acceptance_features(candles, atr, geometry, index, BULL, 10)
    assert got is not None

    sides = []
    for step in range(1, 11):
        at = index + step
        mid = geometry.mid[at]
        assert mid is not None
        sides.append(1 if candles[at].close >= mid else -1)
    assert got["same_side"] == pytest.approx(sum(1 for s in sides if s > 0) / 10)
    assert got["reclaim"] == (1.0 if any(s < 0 for s in sides) else 0.0)
    assert got["cross"] == sum(1 for a, b in zip(sides, sides[1:], strict=False) if a != b)
    first = next((i + 1 for i, s in enumerate(sides) if s < 0), 11)
    assert got["first_cross"] == first


def test_reclaim_e_first_cross_concordam_sempre():
    candles = wave(300)
    geometry = tide_geometry(candles, "TESTUSDT", TimeFrame.H1)
    atr = [0.01] * len(candles)
    seen = 0
    for index in range(60, 260, 7):
        for direction in (BULL, BEAR):
            got = acceptance_features(candles, atr, geometry, index, direction, 5)
            if got is None:
                continue
            seen += 1
            assert (got["reclaim"] > 0) == (got["first_cross"] <= 5)
    assert seen > 20


def test_o_reteste_exige_voltar_a_vwap_antes_de_rejeitar():
    # Um preco que nunca chega perto da VWAP nao pode ter "reteste e rejeicao":
    # sem o toque, o `retest_reject` estaria medindo so extensao.
    candles = wave(300)
    geometry = tide_geometry(candles, "TESTUSDT", TimeFrame.H1)
    atr = [0.01] * len(candles)
    for index in range(60, 280, 3):
        for direction in (BULL, BEAR):
            got = acceptance_features(candles, atr, geometry, index, direction, 20)
            if got is None or got["retest_reject"] == 0:
                continue
            touched = False
            for step in range(1, 21):
                at = index + step
                mid, span = geometry.mid[at], geometry.span[at]
                if mid is None or span is None:
                    continue
                sign = 1 if direction is BULL else -1
                distance = sign * (candles[at].close - mid) / span
                if abs(distance) <= RETEST_SIGMA:
                    touched = True
                    break
            assert touched, index


def test_a_janela_pula_a_quebra_da_fita_mas_desiste_se_sobrar_pouco():
    """Em H1 com ancora de SESSION quase toda janela de 20 atravessa a virada.

    Exigir a janela inteira legivel apagaria o timeframe; aceitar qualquer
    resto mediria "aceitacao" sobre um candle so. O meio-termo declarado e
    `MIN_READABLE`, e o `disp` sobrevive dos dois lados porque o controle nao
    depende da VWAP.
    """
    from research.smc_vwap_acceptance import MIN_READABLE, TideGeometry

    candles = wave(200)
    real = tide_geometry(candles, "TESTUSDT", TimeFrame.H1)
    atr = [0.01] * len(candles)
    index = 130

    # fita apagada em quase toda a janela: sobra menos que o piso
    blind = TideGeometry(list(real.mid), list(real.span))
    keep = index + 1
    for at in range(index + 1, index + 11):
        if at != keep:
            blind.mid[at] = None
            blind.span[at] = None
    got = acceptance_features(candles, atr, blind, index, BULL, 10)
    assert got is not None
    assert math.isnan(got["same_side"])
    assert not math.isnan(got["disp"])

    # um unico buraco: a janela vale, e a leitura e sobre os candles legiveis
    holed = TideGeometry(list(real.mid), list(real.span))
    holed.mid[index + 3] = None
    holed.span[index + 3] = None
    got = acceptance_features(candles, atr, holed, index, BULL, 10)
    full = acceptance_features(candles, atr, real, index, BULL, 10)
    assert got is not None and full is not None
    assert not math.isnan(got["same_side"])
    assert MIN_READABLE < 1.0


def test_o_disp_e_o_deslocamento_puro_e_nao_uma_feature_de_vwap():
    # `disp` e o CONTROLE. Se ele fosse calculado contra a VWAP em vez de
    # contra o preco do evento, o controle da secao 10 estaria contaminado
    # pela propria leitura que ele deveria neutralizar.
    candles = wave(200)
    geometry = tide_geometry(candles, "TESTUSDT", TimeFrame.H1)
    atr = [0.02] * len(candles)
    index = 130
    got = acceptance_features(candles, atr, geometry, index, BULL, 10)
    assert got is not None
    unit = atr[index] * candles[index].close
    assert got["disp"] == pytest.approx(
        (candles[index + 10].close - candles[index].close) / unit
    )
    assert "disp" not in ACCEPTANCE


# --------------------------------------------------------------------------
# 4. simetria
# --------------------------------------------------------------------------


def test_bullish_e_bearish_leem_o_mesmo_numero_em_series_espelhadas():
    prices = [100.0 + i * 0.2 + 6.0 * math.sin(i / 7.0) for i in range(200)]
    up = series(prices)
    # Espelho DENTRO da mesma faixa de precos: refletir para uma escala maior
    # muda o cancelamento de ponto flutuante em `E[p^2] - E[p]^2` e a fita
    # ganha buracos que o original nao tem -- o teste passaria a medir isso.
    down = series([240.0 - price for price in prices])
    atr = [0.01] * len(prices)
    geo_up = tide_geometry(up, "TESTUSDT", TimeFrame.H1)
    geo_down = tide_geometry(down, "TESTUSDT", TimeFrame.H1)
    # Janelas inteiramente DENTRO de um dia UTC. Na virada de ancora a fita
    # tem um unico candle acumulado, a variancia e zero a menos de ruido de
    # ponto flutuante, e qual das duas series ganha banda ali e sorteio -- o
    # teste passaria a medir esse sorteio em vez da simetria.
    seen = 0
    for index in range(48, 180):
        if not 1 <= index % 24 <= 13:
            continue
        a = acceptance_features(up, atr, geo_up, index, BULL, 10)
        b = acceptance_features(down, atr, geo_down, index, BEAR, 10)
        if a is None or b is None:
            continue
        seen += 1
        for name in ("same_side", "reclaim", "cross", "first_cross", "outside"):
            assert a[name] == pytest.approx(b[name], abs=1e-9), name
    assert seen > 10


# --------------------------------------------------------------------------
# 5. polaridade do desfecho
# --------------------------------------------------------------------------


def test_choch_failed_da_mesma_direcao_e_a_invalidacao_deste_choch():
    # `internal_structure.py`: o `direction` do CHOCH_FAILED e o do CHoCH que
    # caiu, nao o do trend que nasce dele. Inverter isso troca o alvo inteiro.
    candles = wave(120)
    events = [event(20, CHOCH, BULL), event(40, FAILED, BULL)]
    assert outcome_of(events, by_ts_of(candles), 20, BULL, 100) == ("failed", 20)


def test_o_bos_da_mesma_direcao_confirma_e_o_choch_oposto_e_um_terceiro_caso():
    candles = wave(120)
    ts = by_ts_of(candles)
    assert outcome_of([event(35, BOS, BULL)], ts, 20, BULL, 100) == ("continued", 15)
    assert outcome_of([event(35, CHOCH, BEAR)], ts, 20, BULL, 100) == ("reversed", 15)
    assert outcome_of([event(35, BOS, BEAR)], ts, 20, BULL, 100) == ("open", -1)


def test_vence_o_desfecho_MAIS_PROXIMO_e_nao_o_primeiro_da_lista():
    candles = wave(120)
    ts = by_ts_of(candles)
    events = [event(60, FAILED, BULL), event(30, BOS, BULL)]
    assert outcome_of(events, ts, 20, BULL, 100) == ("continued", 10)


def test_evento_provisional_nunca_resolve_um_desfecho():
    candles = wave(120)
    marks = [
        MarketStructure(
            symbol="TESTUSDT",
            timeframe=TimeFrame.H1,
            timestamp=START + timedelta(hours=30),
            event=FAILED,
            direction=BULL,
            price_level=100.0,
            provisional=True,
        )
    ]
    assert outcome_of(marks, by_ts_of(candles), 20, BULL, 100) == ("open", -1)


def test_o_horizonte_de_desfecho_e_respeitado():
    candles = wave(300)
    ts = by_ts_of(candles)
    assert outcome_of([event(150, BOS, BULL)], ts, 20, BULL, 100) == ("open", -1)
    assert outcome_of([event(150, BOS, BULL)], ts, 20, BULL, 140)[0] == "continued"


def test_a_duracao_da_nova_tendencia_conta_ate_ela_deixar_de_valer():
    trends = [BULL] * 10 + [BEAR] * 10
    assert trend_duration(trends, 2, BULL) == 8
    assert trend_duration(trends, 12, BEAR) == 8
    assert trend_duration([BULL] * 10, 0, BULL) == 10


# --------------------------------------------------------------------------
# 6. o controle tem que apagar um estado sem sinal proprio
# --------------------------------------------------------------------------


def test_a_aceitacao_perde_o_lift_quando_o_estrato_ja_casa_o_deslocamento():
    """Um estado construido para NAO ter sinal proprio.

    O "estado" aqui e literalmente `disp_5 alto`: ele nao le a VWAP, so o
    deslocamento. Contra o estrato simples (timeframe x direcao x
    volatilidade) ele aparece com lift, porque eventos que andaram mais
    falham menos. Contra o estrato que ja inclui o quintil de `disp_5` ele
    tem que ir a zero -- e se nao for, o controle da secao 10 nao esta
    controlando nada e todo numero desta etapa fica sem sentido.
    """
    store = Store()
    total = 1200
    for position in range(total):
        # deslocamento cresce com a posicao; a falha cai com o deslocamento.
        disp = position / total
        failed = 1 if (position % 10) < (9 - int(8 * disp)) else 0
        for name in store.f:
            store.f[name].append(0.0)
        for name in store.i:
            store.i[name].append(0)
        store.f["disp_5"][position] = disp
        store.f["atr"][position] = 0.01
        store.i["ts"][position] = position
        store.i["dir"][position] = 1
        store.i["outcome"][position] = OUTCOME_CODE["failed" if failed else "continued"]
        store.i["lead"][position] = 50
    resolve_bins(store)

    rows = list(range(total))
    fired = [i for i in rows if store.f["disp_5"][i] >= 0.6]
    hit = hit_failed(store, 5)
    simple = matched_rate(store, fired, rows, hit, 5, min_n=50)
    matched = matched_rate(store, fired, rows, hit, 5, by_disp=True, min_n=50)
    assert simple["lift_pp"] < -20.0
    assert abs(matched["lift_pp"]) < 5.0


def test_a_taxa_casada_nao_publica_amostra_pequena():
    store = _store_with([("failed", 50)] * 10)
    for i in range(10):
        store.f["atr"][i] = 0.01
        store.i["dir"][i] = 1
    got = matched_rate(store, list(range(10)), list(range(10)), hit_failed(store, 5), 5)
    assert got["rate"] is None and got["lift_pp"] is None


# --------------------------------------------------------------------------
# 7. a regra, e o corte do holdout
# --------------------------------------------------------------------------


def test_a_regra_nao_aceita_mais_de_duas_condicoes():
    with pytest.raises(AssertionError):
        Rule("tres", 5, [("same_side", ">=", 1.0)] * 3)


def test_a_grade_e_pequena_de_proposito():
    # Cada regra testada e uma chance a mais de a melhor ser a mais sortuda.
    grid = rule_grid()
    assert len(grid) == 4 * len(WINDOWS_N)
    assert all(len(rule.terms) <= 2 for rule in grid)
    assert {rule.n for rule in grid} == set(WINDOWS_N)


def test_o_corte_do_holdout_e_por_timeframe_e_deixa_70_por_cento_atras():
    store = Store()
    for position in range(100):
        for name in store.f:
            store.f[name].append(0.0)
        for name in store.i:
            store.i[name].append(0)
        store.i["ts"][position] = position
        store.i["tf"][position] = 0 if position < 50 else 1
    assert cut_by_tf(store, 0) == 35
    assert cut_by_tf(store, 1) == 85


# --------------------------------------------------------------------------
# 8. dado real: a geometria existe onde a fita existe
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    not (CACHE_DIR / "BTCUSDT_1h.json").exists(), reason="cache ausente"
)
def test_a_fita_quebra_na_virada_de_ancora_e_isso_vira_None_e_nao_zero():
    candles = load_series("BTCUSDT", TimeFrame.H1)[-1200:]
    geometry = tide_geometry(candles, "BTCUSDT", TimeFrame.H1)
    holes = [i for i, mid in enumerate(geometry.mid) if mid is None]
    assert holes, "sem buraco nenhum a ancora nao esta sendo aplicada"
    # O primeiro candle de cada dia UTC nao tem dispersao acumulada.
    # Todo buraco e o PRIMEIRO candle do seu balde de ancora dentro da serie:
    # sem dispersao acumulada nao ha banda. Nao ha buraco no meio de um dia.
    for i in holes:
        assert i == 0 or candles[i - 1].timestamp.date() != candles[i].timestamp.date()
