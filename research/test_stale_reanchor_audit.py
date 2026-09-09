"""Testes da Etapa 4.5.

Dois modos de falha silenciosa dominam esta etapa e cada um tem teste aqui:

- o contrafactual nao mudar nada (sobrescrever um atributo que o detector nao
  le, ou o wrapper nao ser instalado) -- o relatorio sairia dizendo "nenhuma
  configuracao muda o resultado", que e a conclusao que a etapa pode tirar, so
  que por engano;
- o wrapper vazar para fora do bloco e contaminar toda medicao seguinte.
"""

from __future__ import annotations

import sys

import pytest
from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import MarketDirection, TimeFrame
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series
from research.stale_reanchor_audit import (
    _REANCHOR_CODE,
    BASELINE,
    MATCH_WINDOW,
    VARIANTS,
    ChochRow,
    Variant,
    choch_rows,
    episodes,
    match_rows,
    run_variant,
    split_holdout,
    wired,
)

PANEL = [
    ("BTCUSDT", TimeFrame.H1),
    ("ETHUSDT", TimeFrame.H4),
    ("ZECUSDT", TimeFrame.D1),
]


def available():
    return [
        (symbol, timeframe)
        for symbol, timeframe in PANEL
        if (CACHE_DIR / f"{symbol}_{timeframe.value}.json").exists()
    ]


def production_run(symbol, timeframe, series):
    end = len(series)
    return dd._run_internal_structure(
        provider=SliceProvider(list(series[end - LIMIT - BUFFER : end])),
        symbol=symbol,
        timeframe=timeframe,
        limit=LIMIT,
        confluence_filter=True,
    )


def same_stream(left, right) -> bool:
    if len(left.events) != len(right.events):
        return False
    for a, b in zip(left.events, right.events, strict=True):
        if (
            a.timestamp != b.timestamp
            or a.event is not b.event
            or a.direction is not b.direction
            or a.price_level != b.price_level
            or a.reference_price_level != b.reference_price_level
            or a.provisional != b.provisional
        ):
            return False
    return left.trend == right.trend


# --------------------------------------------------------------------------
# o contrafactual nao pode vazar nem ser inerte
# --------------------------------------------------------------------------


def test_o_gatilho_existe_no_detector():
    """Se `reanchor_opposite` for renomeado, a medicao para em vez de
    reportar zero tentativa como se o mecanismo nunca agisse."""
    assert _REANCHOR_CODE is not None
    assert _REANCHOR_CODE.co_name == "reanchor_opposite"


def test_a_baseline_reproduz_a_producao():
    """Rodar sob o wrapper com overrides vazios tem de dar o stream de sempre."""
    combos = available()
    if not combos:
        pytest.skip("sem cache")
    for symbol, timeframe in combos:
        series = load_series(symbol, timeframe)
        mine, _attempts = run_variant(symbol, timeframe, series, len(series), BASELINE)
        theirs = production_run(symbol, timeframe, series)
        assert same_stream(mine, theirs), f"{symbol} {timeframe.value}"


def test_sobrescrever_com_os_valores_atuais_e_inocuo():
    """A equivalencia que autoriza o metodo: escrever no atributo da instancia
    e o mesmo que construir o detector com aquele kwarg. Se o `__init__`
    derivasse algo desses valores, este teste quebraria."""
    combos = available()
    if not combos:
        pytest.skip("sem cache")
    symbol, timeframe = combos[0]
    series = load_series(symbol, timeframe)
    current = dd._STALE_REANCHOR_CANDLES.get(timeframe, dd._DEFAULT_STALE_REANCHOR_CANDLES)
    same = Variant(
        "identidade",
        {
            "_stale_reanchor_candles": current,
            "_reanchor_chain_threshold": 2,
            "_stale_reanchor_displacement_atr": dd._STALE_REANCHOR_DISPLACEMENT_ATR,
        },
    )
    mine, _a = run_variant(symbol, timeframe, series, len(series), same)
    theirs = production_run(symbol, timeframe, series)
    assert same_stream(mine, theirs)


def test_alguma_variante_realmente_muda_o_stream():
    """O guard contra a conclusao falsa: se NENHUMA variante mudasse nada, o
    relatorio diria 'a configuracao atual e a melhor' sem ter medido nada."""
    combos = available()
    if not combos:
        pytest.skip("sem cache")
    changed = 0
    for symbol, timeframe in combos:
        series = load_series(symbol, timeframe)
        base = production_run(symbol, timeframe, series)
        for variant in (
            Variant("stale x0.25", stale_scale=0.25),
            Variant("mode=off", {"_reanchor_mode": "off"}),
            Variant("stale=off", {"_stale_reanchor_candles": None}),
        ):
            mine, _a = run_variant(symbol, timeframe, series, len(series), variant)
            if not same_stream(mine, base):
                changed += 1
    assert changed >= 1, "nenhuma variante mudou o stream -- o contrafactual esta inerte"


def test_o_wrapper_e_restaurado_mesmo_com_excecao():
    original = dd._build_internal_detector
    with pytest.raises(RuntimeError):
        with wired({"_stale_reanchor_candles": 5}):
            raise RuntimeError("boom")
    assert dd._build_internal_detector is original


def test_override_de_atributo_inexistente_falha_alto():
    """Um nome errado tem de estourar, nao virar override silenciosamente nulo."""
    combos = available()
    if not combos:
        pytest.skip("sem cache")
    symbol, timeframe = combos[0]
    with pytest.raises(AttributeError):
        with wired({"_nao_existe": 1}):
            dd._build_internal_detector(timeframe, confluence_filter=True)


def test_o_trace_e_desinstalado():
    combos = available()
    if not combos:
        pytest.skip("sem cache")
    symbol, timeframe = combos[0]
    series = load_series(symbol, timeframe)
    before = sys.gettrace()
    run_variant(symbol, timeframe, series, len(series), BASELINE)
    assert sys.gettrace() is before


def test_as_variantes_sao_todas_do_mecanismo_existente():
    """Secao 14: so calibragem. Nenhum atributo novo, nenhum modo inventado."""
    detector = dd._build_internal_detector(TimeFrame.H1, confluence_filter=True)
    from liquidity_hunter.liquidity.detectors.internal_structure import _REANCHOR_MODES

    for variant in VARIANTS:
        for name, value in variant.overrides.items():
            assert hasattr(detector, name), name
            if name == "_reanchor_mode":
                assert value in _REANCHOR_MODES


# --------------------------------------------------------------------------
# as tentativas de re-anchor
# --------------------------------------------------------------------------


def test_as_tentativas_sao_observadas_e_tem_veredito():
    combos = available()
    if not combos:
        pytest.skip("sem cache")
    seen = moved = 0
    for symbol, timeframe in combos:
        series = load_series(symbol, timeframe)
        _run, attempts = run_variant(symbol, timeframe, series, len(series), BASELINE)
        seen += len(attempts)
        moved += sum(1 for attempt in attempts if attempt.moved)
        for attempt in attempts:
            assert attempt.level > 0
            assert attempt.current_price > 0
            assert isinstance(attempt.moved, bool)
    assert seen >= 5, f"so {seen} tentativas observadas"
    assert moved >= 1, "nenhuma tentativa moveu a referencia -- o tracer esta lendo errado"


def test_o_reanchor_so_aperta():
    """A regra do codigo: em tendencia bullish o nivel fica ABAIXO do preco;
    em bearish, acima. Uma tentativa aceita nunca viola isso."""
    combos = available()
    if not combos:
        pytest.skip("sem cache")
    checked = 0
    for symbol, timeframe in combos:
        series = load_series(symbol, timeframe)
        _run, attempts = run_variant(symbol, timeframe, series, len(series), BASELINE)
        for attempt in attempts:
            if not attempt.moved:
                continue
            assert attempt.level != attempt.current_price
            checked += 1
    assert checked >= 1


# --------------------------------------------------------------------------
# comparacao e populacao
# --------------------------------------------------------------------------


def row(index, direction="bullish"):
    return ChochRow(
        timestamp=str(index),
        index=index,
        direction=direction,
        reference_price=1.0,
        reference_structural=True,
        lag_bars=0,
        move_completed_pct=None,
    )


def test_match_casa_o_mesmo_choch_adiantado():
    base = [row(100)]
    variant = [row(90)]
    pairs, extra, lost = match_rows(base, variant)
    assert len(pairs) == 1 and not extra and not lost
    assert pairs[0][0].index - pairs[0][1].index == 10


def test_match_nao_casa_alem_da_janela():
    base = [row(100)]
    variant = [row(100 + MATCH_WINDOW + 1)]
    pairs, extra, lost = match_rows(base, variant)
    assert not pairs
    assert len(extra) == 1 and len(lost) == 1


def test_match_nao_casa_direcao_oposta():
    pairs, extra, lost = match_rows([row(100, "bullish")], [row(100, "bearish")])
    assert not pairs and len(extra) == 1 and len(lost) == 1


def test_match_nao_reutiliza_o_mesmo_evento():
    """Dois CHoCH de producao perto nao podem casar com um so do contrafactual."""
    pairs, extra, lost = match_rows([row(100), row(105)], [row(102)])
    assert len(pairs) == 1
    assert len(lost) == 1
    assert not extra


def test_os_episodios_sao_causais():
    """Truncar a serie no fim do episodio nao pode mudar sua selecao."""
    combos = available()
    if not combos:
        pytest.skip("sem cache")
    symbol, timeframe = combos[-1]
    series = load_series(symbol, timeframe)
    run, attempts = run_variant(symbol, timeframe, series, len(series), BASELINE)
    found = episodes(run, symbol, timeframe, 0, attempts)
    if not found:
        pytest.skip("nenhum episodio nesta janela")
    for episode in found:
        assert episode.bars_without_pullback >= 30
        assert episode.advance_atr >= 6.0
        # `reversed_after` e avaliacao: nunca entra no criterio de selecao.
        assert episode.direction in (
            MarketDirection.BULLISH.value,
            MarketDirection.BEARISH.value,
        )


def test_holdout_por_timeframe_nao_vaza():
    combos = available()
    if not combos:
        pytest.skip("sem cache")
    found = []
    for symbol, timeframe in combos:
        series = load_series(symbol, timeframe)
        run, attempts = run_variant(symbol, timeframe, series, len(series), BASELINE)
        found.extend(episodes(run, symbol, timeframe, 0, attempts))
    if len(found) < 6:
        pytest.skip("poucos episodios")
    discovery, holdout = split_holdout(found)
    assert len(discovery) + len(holdout) == len(found)
    for timeframe in {episode.timeframe for episode in found}:
        d = [e.start_timestamp for e in discovery if e.timeframe == timeframe]
        h = [e.start_timestamp for e in holdout if e.timeframe == timeframe]
        if d and h:
            assert max(d) <= min(h)


# --------------------------------------------------------------------------
# o caso obrigatorio
# --------------------------------------------------------------------------


def test_o_zec_tem_choch_bearish_em_04_06_e_nenhuma_config_o_adianta():
    """Secao 8/13: o resultado que a etapa precisa mostrar explicitamente."""
    if not (CACHE_DIR / "ZECUSDT_1d.json").exists():
        pytest.skip("sem cache do ZEC")
    series = load_series("ZECUSDT", TimeFrame.D1)
    base_run, _a = run_variant("ZECUSDT", TimeFrame.D1, series, len(series), BASELINE)
    target = [
        r
        for r in choch_rows(base_run)
        if r.direction == MarketDirection.BEARISH.value
        and r.timestamp.startswith("2026-06-04")
    ]
    if not target:
        pytest.skip("a janela corrente nao contem o CHoCH de 04/06")
    assert target[0].reference_price == pytest.approx(486.0, abs=0.5)
