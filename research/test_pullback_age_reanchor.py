"""Testes da Etapa 4.6.

Tres modos de falha silenciosa dominam esta etapa, e a primeira rodada caiu
nos tres -- cada um tem teste aqui:

- **o contador nao envelhecer**. A primeira versao contava pivos brutos de
  swing em vez dos `higher_low`/`lower_high` emitidos. Numa expansao forte
  quase toda vela forma um fundo local, entao o contador zerava o tempo todo,
  o contrafactual disparava MENOS que producao e o ZEC nao registrava uma
  unica tentativa de re-anchor. O relatorio teria dito "pullback-age nao
  adianta nada" -- que e uma conclusao possivel desta etapa, so que pelo
  motivo errado.
- **os dois contadores em espacos de indice diferentes**. `current_index` e
  `last_advance_index` indexam o array do detector, que carrega o bootstrap
  buffer; `by_ts` indexa `run.candles`. Sem tirar o deslocamento, o
  cruzamento de N=10 do ZEC saiu datado em 2026-01-27 dizendo estar 57 velas
  antes de um CHoCH de 2026-06-04.
- **o lado errado da reversao**. Numa tendencia bullish a reversao usa os
  slots LOW; listar os HIGH faz toda referencia parecer indisponivel e
  transforma qualquer gatilho em NO_EFFECT.

E os dois da Etapa 4.5, que continuam valendo: o contrafactual precisa mesmo
mudar o stream, e o wrapper nao pode vazar do bloco.
"""

from __future__ import annotations

import sys

import pytest
from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import MarketDirection, StructureEvent, TimeFrame
from liquidity_hunter.liquidity.detectors.internal_structure import InternalStructureDetector
from research.choch_reference_audit import assert_trace_line
from research.pullback_age_reanchor import (
    BASELINE,
    VARIANTS,
    LegRow,
    Observation,
    PullbackAgeDetector,
    Variant,
    _keep,
    aggregate,
    available_references,
    cut_timestamps,
    leg_rows,
    observed_run,
    pullback_kind,
    split_by_timeframe,
    wired,
)
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series
from research.stale_reanchor_audit import ChochRow, choch_rows

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


def signature(run):
    return [
        (
            event.timestamp.isoformat(),
            event.event.value,
            event.direction.value,
            event.provisional,
            round(float(event.price_level), 10),
        )
        for event in run.events
    ]


# --------------------------------------------------------------------------
# a instrumentacao
# --------------------------------------------------------------------------


def test_a_linha_tracada_ainda_e_a_cabeca_do_laco():
    assert_trace_line()


def test_o_trace_nao_altera_o_resultado_da_producao():
    """Observar nao pode mudar o observado -- stream inteiro, evento a evento."""
    panel = available()
    if not panel:
        pytest.skip("sem cache")
    symbol, timeframe = panel[0]
    series = load_series(symbol, timeframe)
    run, observations, _attempts = observed_run(symbol, timeframe, series, len(series))
    assert observations, "o tracer nao colheu retrato nenhum"
    assert signature(run) == signature(production_run(symbol, timeframe, series))


def test_o_tracer_e_removido_no_fim():
    panel = available()
    if not panel:
        pytest.skip("sem cache")
    symbol, timeframe = panel[0]
    series = load_series(symbol, timeframe)
    before = sys.gettrace()
    observed_run(symbol, timeframe, series, len(series))
    assert sys.gettrace() is before


# --------------------------------------------------------------------------
# o contador
# --------------------------------------------------------------------------


def test_o_lado_do_pullback_segue_a_tendencia():
    assert pullback_kind(MarketDirection.BULLISH) == "low"
    assert pullback_kind(MarketDirection.BEARISH) == "high"


def test_o_contador_usa_pullback_emitido_e_nao_pivo_bruto():
    """O bug que esvaziava a etapa: contar pivos brutos nunca envelhece."""
    panel = available()
    if not panel:
        pytest.skip("sem cache")
    symbol, timeframe = panel[-1]
    series = load_series(symbol, timeframe)
    run, observations, _ = observed_run(symbol, timeframe, series, len(series))
    emitted = {
        event.timestamp
        for event in run.events
        if event.event in (StructureEvent.HIGHER_LOW, StructureEvent.LOWER_HIGH)
    }
    stamps = [
        run.candles[observation.last_pullback_index].timestamp
        for observation in observations
        if observation.last_pullback_index is not None
    ]
    assert stamps, "nenhum pullback vigente -- o contador nunca teria idade"
    assert set(stamps) <= emitted


def test_os_dois_contadores_vivem_no_mesmo_espaco_de_indice():
    """O bug do bootstrap buffer: idade nao pode contradizer a data."""
    panel = available()
    if not panel:
        pytest.skip("sem cache")
    symbol, timeframe = panel[-1]
    series = load_series(symbol, timeframe)
    run, observations, _ = observed_run(symbol, timeframe, series, len(series))
    checked = 0
    for observation in observations:
        assert run.candles[observation.index].timestamp == observation.timestamp
        for age in (observation.bars_since_advance, observation.bars_since_pullback):
            if age is not None:
                assert 0 <= age < len(run.candles)
                checked += 1
    assert checked, "nenhuma idade calculada"


def test_pullback_e_advance_sao_medidos_do_mesmo_candle():
    observation = Observation(
        timestamp=None, index=100, kind="low", trend="bullish",
        last_advance_index=90, last_pullback_index=40, state={},
    )
    assert observation.bars_since_advance == 10
    assert observation.bars_since_pullback == 60


def test_sem_advance_o_contador_e_indefinido_e_nao_zero():
    observation = Observation(
        timestamp=None, index=10, kind="low", trend="bullish",
        last_advance_index=-1, last_pullback_index=None, state={},
    )
    assert observation.bars_since_advance is None
    assert observation.bars_since_pullback is None


# --------------------------------------------------------------------------
# o contrafactual
# --------------------------------------------------------------------------


def test_o_limiar_efetivo_reproduz_o_teste_por_pullback():
    """A algebra da secao 8, isolada: os dois testes disparam juntos."""
    base = 40
    for advance in (0, 5, 37, 80):
        for pullback in (0, 5, 37, 80):
            if pullback > advance:
                continue
            for current in range(advance, advance + 120):
                effective = max(1, base - (advance - pullback))
                producao_com_efetivo = current - advance >= effective
                por_pullback = current - pullback >= base
                if base - (advance - pullback) >= 1:
                    assert producao_com_efetivo == por_pullback


def test_a_property_devolve_o_valor_de_producao_fora_do_laco():
    detector = PullbackAgeDetector.__new__(PullbackAgeDetector)
    detector._stale_base = 40
    detector._pullback_scan = None
    assert detector._stale_reanchor_candles == 40


def test_a_property_devolve_none_quando_producao_desliga_o_staleness():
    detector = PullbackAgeDetector.__new__(PullbackAgeDetector)
    detector._stale_base = None
    assert detector._stale_reanchor_candles is None


def test_a_varredura_incremental_bate_com_a_varredura_inteira():
    """O cache existe por custo; se ele mentir, o contador inteiro mente.

    Cresce a MESMA lista (e o que `detect` faz com `events`), para exercitar o
    caminho incremental de verdade em vez de forcar um recalculo a cada passo.
    """
    panel = available()
    if not panel:
        pytest.skip("sem cache")
    symbol, timeframe = panel[-1]
    series = load_series(symbol, timeframe)
    run, _observations, _ = observed_run(symbol, timeframe, series, len(series))
    by_ts = {candle.timestamp: i for i, candle in enumerate(run.candles)}
    source = [event for event in run.events if event.timestamp in by_ts]
    detector = PullbackAgeDetector.__new__(PullbackAgeDetector)
    detector._stale_base = 40
    detector._pullback_scan = None

    growing: list = []
    seen_values = set()
    for event in source:
        growing.append(event)
        for trend, wanted in (
            (MarketDirection.BULLISH, StructureEvent.HIGHER_LOW),
            (MarketDirection.BEARISH, StructureEvent.LOWER_HIGH),
        ):
            incremental = detector._last_pullback(growing, by_ts, trend)
            expected = max(
                (by_ts[e.timestamp] for e in growing if e.event is wanted),
                default=None,
            )
            assert incremental == expected
            seen_values.add(incremental)
    assert len(seen_values) > 2, "o contador nunca mudou -- o teste nao provou nada"


def test_o_lado_da_reversao_e_o_oposto_da_tendencia():
    state = {
        "validated_choch_low": type("P", (), {"price": 10.0})(),
        "active_high": type("P", (), {"price": 99.0})(),
    }
    bullish = available_references(state, bullish_trend=True)
    assert "validated_choch_low" in bullish
    assert "active_high" not in bullish
    bearish = available_references(state, bullish_trend=False)
    assert "active_high" in bearish


# --------------------------------------------------------------------------
# a fiacao
# --------------------------------------------------------------------------


def test_sobrescrever_com_os_valores_atuais_e_inocuo():
    """`Variant("producao")` tem de reproduzir producao byte a byte."""
    panel = available()
    if not panel:
        pytest.skip("sem cache")
    symbol, timeframe = panel[0]
    series = load_series(symbol, timeframe)
    baseline = signature(production_run(symbol, timeframe, series))
    with wired(BASELINE, timeframe):
        wrapped = signature(production_run(symbol, timeframe, series))
    assert wrapped == baseline


def test_o_wrapper_nao_vaza_do_bloco_nem_em_excecao():
    original = dd._build_internal_detector
    with pytest.raises(RuntimeError):
        with wired(Variant("x", pullback_age=True), TimeFrame.D1):
            raise RuntimeError("boom")
    assert dd._build_internal_detector is original


def test_a_troca_de_classe_nao_contamina_a_classe_de_producao():
    panel = available()
    if not panel:
        pytest.skip("sem cache")
    symbol, timeframe = panel[0]
    series = load_series(symbol, timeframe)
    with wired(Variant("p", pullback_age=True), timeframe):
        production_run(symbol, timeframe, series)
    detector = dd._build_internal_detector(timeframe, confluence_filter=True)
    assert type(detector) is InternalStructureDetector
    assert not isinstance(detector, PullbackAgeDetector)


def test_alguma_variante_realmente_muda_o_stream():
    """Se nada muda, a etapa mediria o proprio baseline e chamaria de resposta."""
    panel = available()
    if not panel:
        pytest.skip("sem cache")
    symbol, timeframe = panel[-1]
    series = load_series(symbol, timeframe)
    baseline = signature(production_run(symbol, timeframe, series))
    changed = []
    for variant in VARIANTS:
        if variant is BASELINE:
            continue
        with wired(variant, timeframe):
            if signature(production_run(symbol, timeframe, series)) != baseline:
                changed.append(variant.label)
    assert changed, "nenhuma variante mexeu no stream -- o contrafactual esta inerte"


def test_a_semantica_nova_muda_o_stream_por_si_so():
    """Nao basta a grade mudar: `pullback x1.00` usa o limiar DE PRODUCAO."""
    panel = available()
    if not panel:
        pytest.skip("sem cache")
    symbol, timeframe = panel[-1]
    series = load_series(symbol, timeframe)
    baseline = signature(production_run(symbol, timeframe, series))
    variant = next(v for v in VARIANTS if v.label == "pullback x1.00")
    assert variant.scale == 1.0
    with wired(variant, timeframe):
        assert signature(production_run(symbol, timeframe, series)) != baseline


def test_a_escala_respeita_o_valor_por_timeframe():
    variant = Variant("meio", pullback_age=True, scale=0.5)
    for timeframe in (TimeFrame.M15, TimeFrame.H1, TimeFrame.D1):
        base = dd._STALE_REANCHOR_CANDLES[timeframe]
        assert variant.base_for(timeframe) == max(1, round(base * 0.5))


# --------------------------------------------------------------------------
# o recorte
# --------------------------------------------------------------------------


def row(timeframe: str, timestamp: str) -> ChochRow:
    return ChochRow(
        timestamp=timestamp, index=0, direction="bearish", reference_price=None,
        reference_structural=None, lag_bars=0, move_completed_pct=None,
    )


def test_o_corte_e_calculado_por_timeframe():
    rows = [
        LegRow("S", "1d", 0, f"2026-01-{day:02d}", 0, "bearish", None, None, None,
               None, None, None, None)
        for day in range(1, 11)
    ] + [
        LegRow("S", "1h", 0, f"2020-01-{day:02d}", 0, "bearish", None, None, None,
               None, None, None, None)
        for day in range(1, 5)
    ]
    cuts = cut_timestamps(rows)
    assert cuts["1d"].startswith("2026-01-08")
    assert cuts["1h"].startswith("2020-01-03")


def test_o_recorte_particiona_sem_sobreposicao():
    rows = [row("1d", f"2026-01-{day:02d}") for day in range(1, 11)]
    cut = "2026-01-08"
    discovery = _keep(rows, "discovery", cut)
    holdout = _keep(rows, "holdout", cut)
    assert len(discovery) + len(holdout) == len(rows)
    assert not {r.timestamp for r in discovery} & {r.timestamp for r in holdout}
    assert _keep(rows, "painel", cut) == rows


def test_o_holdout_de_episodios_e_por_timeframe():
    class Fake:
        def __init__(self, timeframe, stamp):
            self.timeframe = timeframe
            self.start_timestamp = stamp

    rows = [Fake("1d", f"2026-01-{d:02d}") for d in range(1, 11)]
    rows += [Fake("1h", f"2020-01-{d:02d}") for d in range(1, 11)]
    discovery, holdout = split_by_timeframe(rows, key=lambda r: r.start_timestamp)
    assert len(discovery) == 14 and len(holdout) == 6
    for timeframe in ("1d", "1h"):
        assert sum(1 for r in holdout if r.timeframe == timeframe) == 3


def test_a_agregacao_pondera_os_extras_pelo_tamanho():
    summaries = [
        {"n_baseline": 1, "n_variant": 1, "extra": 10, "perdidos": 0, "adiantados": 0,
         "atrasados": 0, "lead_mediano": 1, "lag_mediano_variant": 1,
         "move_pct_variant": 1, "extra_confirmado": 100.0, "extra_choch_failed": 0.0,
         "extra_voltou": 0.0, "extra_sem_desfecho": 0.0,
         "extra_voltou_10": 0.0, "extra_voltou_20": 0.0, "extra_voltou_40": 0.0},
        {"n_baseline": 1, "n_variant": 1, "extra": 90, "perdidos": 0, "adiantados": 0,
         "atrasados": 0, "lead_mediano": 1, "lag_mediano_variant": 1,
         "move_pct_variant": 1, "extra_confirmado": 0.0, "extra_choch_failed": 0.0,
         "extra_voltou": 0.0, "extra_sem_desfecho": 0.0,
         "extra_voltou_10": 0.0, "extra_voltou_20": 0.0, "extra_voltou_40": 0.0},
    ]
    out = aggregate(summaries)
    assert out["extra"] == 100
    assert out["extra_confirmado"] == 10.0


def test_o_painel_produz_linhas_com_os_cinco_candidatos():
    panel = available()
    if not panel:
        pytest.skip("sem cache")
    symbol, timeframe = panel[-1]
    series = load_series(symbol, timeframe)
    run, observations, _ = observed_run(symbol, timeframe, series, len(series))
    rows = leg_rows(run, observations, symbol, timeframe, 0)
    assert rows, "nenhum CHoCH no painel"
    assert len(rows) == len(choch_rows(run))
    assert any(r.bars_since_pullback is not None for r in rows)
    assert any(r.reference_age_bars is not None for r in rows)
    assert all(r.choch_lag_bars is not None for r in rows)
