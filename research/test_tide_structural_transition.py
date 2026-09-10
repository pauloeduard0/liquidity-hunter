"""Testes da Etapa 5.1.

Quatro maneiras de esta medicao mentir sem quebrar:

1. **vazamento** -- uma feature do Tide que enxergue o futuro. A geometria vem
   de uma acumulacao corrente e a agressao de uma janela trailing, mas a
   normalizacao do frontend (`convictionScale`, p90 da janela inteira) e
   lookahead de verdade, e o teste abaixo existe para garantir que ela nao
   entrou aqui por copia.
2. **divergencia do frontend** -- se `phase` ou `aggression` nao forem as
   formulas do `tideRibbon.ts`, a etapa mede outro indicador e chama de Tide.
3. **a cor deixar de ser a estrutura** -- a afirmacao central da secao 1 e uma
   identidade de codigo; se ela quebrar, as conclusoes mudam.
4. **assimetria escondida** -- uma leitura que so funciona numa direcao,
   agregada ate sumir.
"""

from datetime import UTC, datetime, timedelta

import pytest
from liquidity_hunter.core.domain import Candle, MarketDirection, TimeFrame, VWAPAnchor
from research.range_choch import CACHE_DIR, load_series
from research.tide_structural_transition import (
    AGGRESSION_WINDOW,
    CHANGE_LAG,
    DEFAULT_ANCHOR,
    MAGNITUDE_FEATURES,
    PHASE_CLAMP,
    SIGNED_FEATURES,
    VWAP_ANCHOR,
    EpisodeStore,
    Store,
    aggression_series,
    aligned,
    excursion,
    feature_matrix,
    first_choch,
    hit_resume,
    matched_rate,
    opposition_runs,
    phase_series,
    scan_episodes,
    stale_onset,
    tide_geometry,
    vol_terciles,
)


def candle(
    index: int,
    close: float,
    *,
    volume: float = 100.0,
    taker: float = 50.0,
    high: float | None = None,
    low: float | None = None,
    timeframe: TimeFrame = TimeFrame.H1,
) -> Candle:
    step = {TimeFrame.H1: 1, TimeFrame.D1: 24}[timeframe]
    return Candle(
        symbol="TESTUSDT",
        timeframe=timeframe,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=step * index),
        open=close,
        high=close * 1.01 if high is None else high,
        low=close * 0.99 if low is None else low,
        close=close,
        volume=volume,
        taker_buy_volume=taker,
    )


def ramp(length: int = 120, start: float = 100.0, step: float = 0.5) -> list[Candle]:
    return [candle(i, start + step * i) for i in range(length)]


# --------------------------------------------------------------------------
# 1. causalidade
# --------------------------------------------------------------------------


def test_nenhuma_feature_do_tide_muda_quando_o_futuro_e_cortado():
    """Cortar a serie depois do candle medido nao pode mover nenhuma leitura.

    E o teste que a normalizacao do frontend NAO passaria: `convictionScale`
    divide pelo p90 da janela inteira, entao acrescentar um candle no fim muda
    a saturacao de todos os anteriores.
    """
    candles = ramp(140)
    index = 100

    def read(series):
        geometry = tide_geometry(series, "TESTUSDT", TimeFrame.H1)
        phase = phase_series(series, geometry)
        agg = aggression_series(series, AGGRESSION_WINDOW[TimeFrame.H1])
        atr = [0.01] * len(series)
        return feature_matrix(series, atr, geometry, phase, agg)

    full = read(candles)
    cut = read(candles[: index + 1])
    for name in SIGNED_FEATURES + MAGNITUDE_FEATURES:
        assert full[name][index] == pytest.approx(cut[name][index], abs=1e-12), name


def test_a_agressao_e_uma_janela_trailing_e_nao_uma_acumulacao():
    """Um candle antigo fora da janela nao pode mais influenciar a leitura."""
    window = AGGRESSION_WINDOW[TimeFrame.H1]
    base = [candle(i, 100.0) for i in range(40)]
    poisoned = list(base)
    poisoned[5] = candle(5, 100.0, volume=100.0, taker=100.0)
    a = aggression_series(base, window)
    b = aggression_series(poisoned, window)
    assert a[5 + window - 1] != b[5 + window - 1]
    assert a[39] == pytest.approx(b[39])


# --------------------------------------------------------------------------
# 2. fidelidade ao frontend
# --------------------------------------------------------------------------


def test_a_agressao_reproduz_a_formula_do_frontend():
    """`2*taker_buy - volume` somado na janela, sobre o volume dela, em %."""
    window = 7
    candles = [candle(i, 100.0, volume=100.0, taker=60.0 if i % 2 else 40.0) for i in range(20)]
    got = aggression_series(candles, window)
    index = 15
    delta = sum(2 * c.taker_buy_volume - c.volume for c in candles[index - window + 1 : index + 1])
    volume = sum(c.volume for c in candles[index - window + 1 : index + 1])
    assert got[index] == pytest.approx(100.0 * delta / volume)


def test_o_phase_e_zero_na_vwap_e_cinquenta_no_sigma():
    """A escala do `buildPhase`: 0 = VWAP, +/-50 = +/-1 sigma, clamp em 150."""
    candles = ramp(60)
    geometry = tide_geometry(candles, "TESTUSDT", TimeFrame.H1)
    phase = phase_series(candles, geometry)
    for index, value in enumerate(phase):
        if value is None:
            continue
        assert abs(value) <= PHASE_CLAMP
        mid = geometry.mid[index]
        span = geometry.span[index]
        assert mid is not None and span is not None
        expected = (candles[index].close - mid) / span * 50
        assert value == pytest.approx(max(-PHASE_CLAMP, min(PHASE_CLAMP, expected)))


def test_a_ancora_da_vwap_segue_o_timeframe_como_na_producao():
    """H4 semanal e D1 mensal -- num dia UTC o D1 tem UMA vela e nao ha banda."""
    assert VWAP_ANCHOR[TimeFrame.H4] is VWAPAnchor.WEEK
    assert VWAP_ANCHOR[TimeFrame.D1] is VWAPAnchor.MONTH
    assert DEFAULT_ANCHOR is VWAPAnchor.SESSION
    daily = [candle(i, 100.0 + i, timeframe=TimeFrame.D1) for i in range(40)]
    mensal = tide_geometry(daily, "TESTUSDT", TimeFrame.D1)
    assert any(span is not None for span in mensal.span)


def test_a_fita_nao_existe_no_primeiro_candle_de_cada_segmento():
    """Sem dispersao acumulada nao ha banda, e o candle e descartado."""
    candles = ramp(60)
    geometry = tide_geometry(candles, "TESTUSDT", TimeFrame.H1)
    assert geometry.span[0] is None


# --------------------------------------------------------------------------
# 3. a identidade central: a cor do Tide E a estrutura
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    not (CACHE_DIR / "BTCUSDT_1h.json").exists(), reason="cache do painel ausente"
)
def test_a_cor_do_tide_e_o_final_trend_e_nao_uma_segunda_leitura():
    """`structureTrendByCandle` do frontend reproduz `trend_by_index`.

    A afirmacao que estrutura toda a Etapa 5.1 e uma identidade de codigo, nao
    uma medicao. Este teste a checa em dados reais reimplementando a regra do
    TypeScript (`BOS/CHoCH -> direction`, `CHOCH_FAILED -> invertido`, segura
    ate o proximo evento) e comparando candle a candle.
    """
    from liquidity_hunter.app import dashboard_data as dd
    from liquidity_hunter.core.domain import StructureEvent
    from research.current_market_pressure import ADVANCES, trend_by_index
    from research.range_choch import BUFFER, LIMIT, SliceProvider

    series = load_series("BTCUSDT", TimeFrame.H1)
    run = dd._run_internal_structure(
        provider=SliceProvider(list(series[-(LIMIT + BUFFER) :])),
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        limit=LIMIT,
        confluence_filter=True,
    )
    by_ts = {c.timestamp: i for i, c in enumerate(run.candles)}
    ours = trend_by_index(run.events, by_ts, len(run.candles))

    # A transcricao literal do `trendAfter` + laco do `structureTrendByCandle`.
    events = sorted(
        (e for e in run.events if not e.provisional),
        key=lambda e: e.timestamp,
    )
    theirs: list[MarketDirection | None] = []
    trend: MarketDirection | None = None
    position = 0
    for c in run.candles:
        while position < len(events) and events[position].timestamp <= c.timestamp:
            event = events[position]
            if event.event in (
                StructureEvent.BREAK_OF_STRUCTURE,
                StructureEvent.CHANGE_OF_CHARACTER,
            ):
                trend = event.direction
            elif event.event is StructureEvent.CHOCH_FAILED:
                trend = (
                    MarketDirection.BEARISH
                    if event.direction is MarketDirection.BULLISH
                    else MarketDirection.BULLISH
                )
            position += 1
        theirs.append(trend)

    assert theirs == ours
    assert ADVANCES  # a lista de advances e a mesma dos dois lados


# --------------------------------------------------------------------------
# 4. simetria e normalizacao pela direcao
# --------------------------------------------------------------------------


def test_alinhar_inverte_o_sinal_em_estrutura_bearish():
    assert aligned(2.0, MarketDirection.BULLISH) == 2.0
    assert aligned(2.0, MarketDirection.BEARISH) == -2.0
    assert aligned(None, MarketDirection.BULLISH) is None


def test_a_oposicao_e_simetrica_entre_as_duas_direcoes():
    """O mesmo valor de feature vira oposicao dos dois lados, sem ramo proprio.

    E o que impede o bug classico: duplicar o codigo bullish/bearish e deixar
    uma das metades com um `>=` onde deveria haver `>`.
    """
    for value in (-3.0, -0.1, 0.1, 3.0):
        bull = aligned(value, MarketDirection.BULLISH)
        bear = aligned(-value, MarketDirection.BEARISH)
        assert bull == bear


# --------------------------------------------------------------------------
# 5. estados, episodios e alvos
# --------------------------------------------------------------------------


def test_a_corrida_de_oposicao_conta_candles_consecutivos():
    runs = opposition_runs([False, True, True, True, False, True])
    assert runs == [0, 1, 2, 3, 0, 1]


def test_um_episodio_nao_atravessa_um_flip_de_estrutura():
    """Depois do flip a oposicao ja e sobre outra perna, e outra pergunta."""
    states = [True] * 10
    trends = [MarketDirection.BULLISH] * 5 + [MarketDirection.BEARISH] * 5
    blocks = scan_episodes(states, trends, gap=3)
    assert len(blocks) == 2
    assert blocks[0] == (0, 4)
    assert blocks[1][0] == 5


def test_um_episodio_tolera_o_buraco_declarado_e_nao_mais():
    trends = [MarketDirection.BULLISH] * 20
    curto = [True] + [False] * 3 + [True] + [False] * 15
    assert scan_episodes(curto, trends, gap=3) == [(0, 4)]
    longo = [True] + [False] * 5 + [True] + [False] * 13
    assert scan_episodes(longo, trends, gap=3)[0] == (0, 0)


def test_uma_perna_ja_parada_nao_conta_como_transicao():
    """`stale_onset` mede a VIRADA, nao a permanencia do estado."""

    class Leg:
        def __init__(self, direction, stale):
            self.direction = direction
            self.stale = stale

    legs = [Leg(MarketDirection.BULLISH, True)] * 5
    assert stale_onset(legs, 0, 4) is None
    legs = [Leg(None, False)] * 5
    assert stale_onset(legs, 0, 4) is None
    legs = [Leg(MarketDirection.BULLISH, False)] * 3 + [Leg(MarketDirection.BULLISH, True)] * 2
    assert stale_onset(legs, 0, 4) == 1
    assert stale_onset(legs, 0, 2) == 0


def test_o_choch_failed_conta_na_direcao_invertida():
    """A falha de um CHoCH bullish e uma confirmacao bearish."""
    from liquidity_hunter.core.domain import MarketStructure, StructureEvent

    now = datetime(2026, 1, 1, tzinfo=UTC)
    event = MarketStructure(
        symbol="TESTUSDT",
        timeframe=TimeFrame.H1,
        timestamp=now + timedelta(hours=5),
        event=StructureEvent.CHOCH_FAILED,
        direction=MarketDirection.BULLISH,
        price_level=100.0,
    )
    by_ts = {now + timedelta(hours=i): i for i in range(10)}
    assert first_choch([event], by_ts, 0, MarketDirection.BEARISH, 10) == 5
    assert first_choch([event], by_ts, 0, MarketDirection.BULLISH, 10) == -1


def test_o_bos_nao_conta_como_choch():
    from liquidity_hunter.core.domain import MarketStructure, StructureEvent

    now = datetime(2026, 1, 1, tzinfo=UTC)
    event = MarketStructure(
        symbol="TESTUSDT",
        timeframe=TimeFrame.H1,
        timestamp=now + timedelta(hours=3),
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=MarketDirection.BEARISH,
        price_level=100.0,
    )
    by_ts = {now + timedelta(hours=i): i for i in range(10)}
    assert first_choch([event], by_ts, 0, MarketDirection.BEARISH, 10) == -1


def test_a_excursao_e_medida_na_direcao_da_oposicao():
    candles = [candle(i, 100.0 - i) for i in range(20)]
    atr = [0.01] * 20
    # estrutura bullish -> oposicao aponta para baixo -> sign = -1
    mfe, mae = excursion(candles, atr, 0, -1, 10)
    assert mfe == pytest.approx(10 / (0.01 * 100.0))
    assert mae <= 0


# --------------------------------------------------------------------------
# 6. o controle: taxa lida contra o proprio estrato
# --------------------------------------------------------------------------


def _store(rows):
    store = Store()
    for tf, trend, atr, hit in rows:
        for name in store.f:
            store.f[name].append(0.0)
        store.f["atr"][-1] = atr
        store.i["tf"].append(tf)
        store.i["trend"].append(trend)
        store.i["stale"].append(0)
        store.i["ts"].append(0)
        store.i["resume"].append(-1)
        store.i["choch_lead"].append(-1)
        store.i["vol"].append(0)
        store.i["ret_bin"].append(0)
        for h in store.stale:
            store.stale[h].append(hit)
        for p in store.persist:
            store.persist[p].append(0)
            store.agg_persist[p].append(0)
    return store


def test_a_taxa_e_lida_contra_o_estrato_e_nao_contra_a_media_global():
    """Um estado que so aparece no estrato facil nao pode parecer preditivo.

    Dois estratos com taxas-base 90% e 10%. Um estado que dispara SO no
    primeiro tem taxa bruta 90% -- e lift zero, porque e exatamente a taxa de
    quem nao disparou nada ali. Comparar com a media global (50%) daria
    +40pp de nada.
    """
    rows = [(0, 1, 0.01, 1) for _ in range(900)] + [(0, 1, 0.01, 0) for _ in range(100)]
    rows += [(0, -1, 0.01, 1) for _ in range(100)] + [(0, -1, 0.01, 0) for _ in range(900)]
    store = _store(rows)
    vol_terciles(store)
    pool = list(range(len(store)))
    fired = [i for i in pool if store.i["trend"][i] == 1]
    got = matched_rate(store, fired, pool, lambda i: store.stale[10][i])
    assert got["rate"] == pytest.approx(0.9)
    assert got["matched"] == pytest.approx(0.9)
    assert got["lift_pp"] == pytest.approx(0.0, abs=1e-9)


def test_o_estrato_separa_regime_de_volatilidade_dentro_do_timeframe():
    """Um tercil global rotularia timeframe, e nao volatilidade."""
    rows = [(0, 1, 0.001 * (i + 1), 0) for i in range(30)]
    rows += [(1, 1, 10.0 * (i + 1), 0) for i in range(30)]
    store = _store(rows)
    vol_terciles(store)
    m15 = {store.i["vol"][i] for i in range(30)}
    d1 = {store.i["vol"][i] for i in range(30, 60)}
    assert m15 == {0, 1, 2}
    assert d1 == {0, 1, 2}


def test_false_conflict_exige_o_bos_antes_do_choch():
    """Retomada so conta se ela veio ANTES da virada, nao depois."""
    store = _store([(0, 1, 0.01, 0)])
    store.i["resume"][0] = 5
    store.i["choch_lead"][0] = 10
    assert hit_resume(store, 40)(0) == 1
    store.i["choch_lead"][0] = 3
    assert hit_resume(store, 40)(0) == 0
    store.i["resume"][0] = -1
    assert hit_resume(store, 40)(0) == 0


def test_uma_amostra_pequena_nao_vira_taxa():
    """Abaixo do piso a celula devolve `None` em vez de um numero bonito."""
    store = _store([(0, 1, 0.01, 1) for _ in range(10)])
    vol_terciles(store)
    pool = list(range(len(store)))
    got = matched_rate(store, pool, pool, lambda i: store.stale[10][i])
    assert got["rate"] is None


def test_o_store_de_episodios_guarda_o_que_o_relatorio_le():
    eps = EpisodeStore()
    eps.add(
        tf=0,
        trend=1,
        stale=0,
        duration=4,
        choch_lead=12,
        resume=-1,
        ts=0,
        channel=1,
        intensity=1.5,
        width=0.01,
        mfe=2.0,
        mae=0.5,
    )
    assert len(eps) == 1
    assert eps.i["channel"][0] == 1
    assert eps.f["mfe"][0] == pytest.approx(2.0)


def test_a_mudanca_usa_o_lag_declarado():
    values = [float(i) for i in range(20)]
    from research.tide_structural_transition import _change

    assert _change(values, 10, CHANGE_LAG) == pytest.approx(CHANGE_LAG)
    assert _change(values, 2, CHANGE_LAG) is None


def test_o_tide_perde_o_lift_quando_o_estrato_ja_casa_o_preco():
    """O controle 6C na forma severa, checado num caso construido.

    Duas metades: nas linhas de retorno recente ruim o alvo acontece 80% das
    vezes, nas boas 20%. Um "estado" que dispara EXATAMENTE nas ruins parece
    somar +30pp contra o estrato simples -- e some para zero quando o quintil
    de retorno entra no estrato, porque nao havia leitura nenhuma alem do
    preco. E o teste que a Etapa 5.1 precisa passar para poder afirmar
    qualquer coisa.
    """
    rows = [(0, 1, 0.01, 1) for _ in range(800)] + [(0, 1, 0.01, 0) for _ in range(200)]
    rows += [(0, 1, 0.01, 1) for _ in range(200)] + [(0, 1, 0.01, 0) for _ in range(800)]
    store = _store(rows)
    for index in range(len(store)):
        store.f["ret_atr_10"][index] = -5.0 if index < 1000 else 5.0
    vol_terciles(store)
    pool = list(range(len(store)))
    fired = list(range(1000))
    simples = matched_rate(store, fired, pool, lambda i: store.stale[10][i])
    casado = matched_rate(store, fired, pool, lambda i: store.stale[10][i], by_price=True)
    assert simples["lift_pp"] == pytest.approx(30.0, abs=0.5)
    assert casado["lift_pp"] == pytest.approx(0.0, abs=0.5)
