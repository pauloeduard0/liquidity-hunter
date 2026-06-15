"""Tests for `InternalStructureDetector`."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    StructureScope,
    TimeFrame,
)
from liquidity_hunter.liquidity.detectors.internal_structure import InternalStructureDetector
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle, make_series

# Pivot sequence (lookback=1, so each pivot sits at its index with 1 flat
# candle on either side). All candles use the default (midpoint) close, so no
# counter-trend break ever holds beyond its reference -- every CHoCH-candidate
# is reported as an unconfirmed LIQUIDITY_SWEEP. This exercises BOS/HL/LH/sweep
# detection without any CHANGE_OF_CHARACTER (those are exercised below):
#
#   index  1: high 200 -> bootstraps active_high; no event
#   index  3: low   90 -> bootstraps active_low; no event
#   index  5: high 220 -> above active_high; trend NEUTRAL ->
#                           BREAK_OF_STRUCTURE bullish; trend BULLISH
#   index  7: low  100 -> above active_low (90) -> HIGHER_LOW
#   index  9: high 210 -> below active_high (220) -> LOWER_HIGH
#   index 11: low   80 -> below active_low (100); trend BULLISH but the
#                           midpoint close (115) doesn't hold below the level
#                           -> LIQUIDITY_SWEEP bearish (ref 100)
#   index 13: high 230 -> above active_high (210); trend BULLISH ->
#                           BREAK_OF_STRUCTURE bullish (continuation)
#   index 15: low   70 -> LIQUIDITY_SWEEP bearish (ref 100, unconfirmed)
#   index 17: high 215 -> below active_high (230) -> LOWER_HIGH
#   index 19: low   60 -> not enough trailing candles to confirm ->
#                           LIQUIDITY_SWEEP bearish (ref 70)
HIGHS = [150.0] * 21
for _index, _value in {1: 200.0, 5: 220.0, 9: 210.0, 13: 230.0, 17: 215.0}.items():
    HIGHS[_index] = _value

LOWS = [140.0] * 21
for _index, _value in {3: 90.0, 7: 100.0, 11: 80.0, 15: 70.0, 19: 60.0}.items():
    LOWS[_index] = _value


def test_internal_structure_detector_full_sequence() -> None:
    candles = make_series(HIGHS, LOWS)

    events = InternalStructureDetector(swing_lookback=1).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 220.0, 200.0),
        (StructureEvent.HIGHER_LOW, MarketDirection.BULLISH, 100.0, 90.0),
        (StructureEvent.LOWER_HIGH, MarketDirection.BEARISH, 210.0, 220.0),
        (StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH, 80.0, 100.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 230.0, 210.0),
        (StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH, 70.0, 100.0),
        (StructureEvent.LOWER_HIGH, MarketDirection.BEARISH, 215.0, 230.0),
        (StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH, 60.0, 70.0),
    ]
    assert [e.timestamp for e in events] == [
        candles[5].timestamp,
        candles[7].timestamp,
        candles[9].timestamp,
        candles[11].timestamp,
        candles[13].timestamp,
        candles[15].timestamp,
        candles[17].timestamp,
        candles[19].timestamp,
    ]
    for event in events:
        assert event.symbol == "BTCUSDT"


def test_first_pivot_of_each_kind_produces_no_event() -> None:
    """Index 1 (first high) and index 3 (first low) bootstrap the active
    references without emitting any event.
    """
    candles = make_series(HIGHS, LOWS)

    events = InternalStructureDetector(swing_lookback=1).detect(candles)

    assert candles[1].timestamp not in [e.timestamp for e in events]
    assert candles[3].timestamp not in [e.timestamp for e in events]


def test_pivot_equal_to_active_reference_produces_no_event() -> None:
    """A pivot exactly equal to the current active reference (neither a
    break nor a label) produces no event, and still becomes the new active
    reference for the next comparison.
    """
    highs = [150.0, 200.0, 150.0, 150.0, 150.0, 200.0, 150.0, 150.0, 150.0]
    lows = [140.0, 140.0, 140.0, 100.0, 140.0, 140.0, 140.0, 90.0, 140.0]
    candles = make_series(highs, lows)

    events = InternalStructureDetector(swing_lookback=1).detect(candles)

    # index 5's high (200) equals active_high (200, from index 1) -> no event.
    # index 7's low (90) breaks active_low (100); trend is still NEUTRAL ->
    # BREAK_OF_STRUCTURE bearish (price-only).
    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH, 90.0, 100.0),
    ]


def test_internal_structure_detector_stamps_internal_scope() -> None:
    candles = make_series(HIGHS, LOWS)

    events = InternalStructureDetector(swing_lookback=1).detect(candles)

    assert events
    assert all(event.scope is StructureScope.INTERNAL for event in events)


def test_internal_structure_detector_returns_empty_for_short_series() -> None:
    candles = make_series(HIGHS[:2], LOWS[:2])

    assert InternalStructureDetector(swing_lookback=1).detect(candles) == []


def test_internal_structure_detector_rejects_mixed_symbols() -> None:
    candles = make_series(HIGHS, LOWS)
    candles[0] = make_candle(0, candles[0].high, candles[0].low, symbol="ETHUSDT")

    with pytest.raises(ValueError, match="same symbol and timeframe"):
        InternalStructureDetector(swing_lookback=1).detect(candles)


def test_internal_structure_detector_rejects_empty_candles() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        InternalStructureDetector().detect([])


def test_internal_structure_detector_rejects_invalid_persistence_candles() -> None:
    with pytest.raises(ValueError, match="persistence_candles must be at least 1"):
        InternalStructureDetector(persistence_candles=0)


# --- The validated CHoCH reference -----------------------------------------
#
# validated_choch_high (the level a bullish CHoCH must break) is set, when a
# *new LL* is confirmed, to the LAST swing high before that LL -- not the
# highest high of the leg. This sequence (lookback=1) has two highs between
# the two most recent LLs (190 at index 7, then a lower 170 at index 9); the
# reference must be 170, the last one:
#
#   index  1: high 200 -> bootstraps active_high
#   index  3: low  100 -> bootstraps active_low
#   index  5: low   80 -> below active_low; trend NEUTRAL -> BOS bearish;
#                           trend BEARISH; new LL -> validated_choch_high = 200
#                           (last_high_pivot), last_ll = 80
#   index  7: high 190 -> re-bootstraps active_high (was retired); no event
#   index  9: high 170 -> below active_high (190) -> LOWER_HIGH (last_high_pivot
#                           is now 170)
#   index 11: low   60 -> below last_ll (80) -> new LL (BOS bearish);
#                           validated_choch_high = last_high_pivot = 170 (NOT
#                           the higher 190), last_ll = 60
#   index 13: high 175 -> sustained break above validated_choch_high (170) ->
#                           CHANGE_OF_CHARACTER bullish, reference 170 (note
#                           175 < 190: it breaks the last high, not the highest)
_TIEBREAK_HIGH_HIGHS = [150.0] * 15
for _index, _value in {1: 200.0, 7: 190.0, 9: 170.0, 13: 175.0}.items():
    _TIEBREAK_HIGH_HIGHS[_index] = _value
_TIEBREAK_HIGH_LOWS = [140.0] * 15
for _index, _value in {3: 100.0, 5: 80.0, 11: 60.0}.items():
    _TIEBREAK_HIGH_LOWS[_index] = _value


def test_bullish_choch_reference_is_last_high_before_new_ll_not_highest() -> None:
    candles = make_series(_TIEBREAK_HIGH_HIGHS, _TIEBREAK_HIGH_LOWS)
    candles[13] = make_candle(13, 175.0, 140.0, close=174.0)
    candles[14] = make_candle(14, 174.0, 171.0, close=173.0)

    events = InternalStructureDetector(swing_lookback=1, persistence_candles=1).detect(candles)

    choch = events[-1]
    assert choch.event is StructureEvent.CHANGE_OF_CHARACTER
    assert choch.direction is MarketDirection.BULLISH
    assert choch.price_level == 175.0
    # The break is sustained from this candle onward (no earlier lag here).
    assert choch.timestamp == candles[13].timestamp
    # 170 (the last high before the final LL), never 190 (the higher bounce).
    assert choch.reference_price_level == 170.0


def test_bearish_choch_reference_is_last_low_before_new_hh_not_lowest() -> None:
    # Mirror: two lows between the two most recent HHs (110, then a higher
    # 130); the bearish CHoCH reference must be 130, the last one, not 110.
    highs = [150.0] * 15
    for index, value in {3: 200.0, 5: 250.0, 11: 280.0}.items():
        highs[index] = value
    lows = [140.0] * 15
    for index, value in {1: 100.0, 7: 110.0, 9: 130.0, 13: 120.0}.items():
        lows[index] = value
    candles = make_series(highs, lows)
    candles[13] = make_candle(13, 150.0, 120.0, close=125.0)
    candles[14] = make_candle(14, 150.0, 121.0, close=124.0)

    events = InternalStructureDetector(swing_lookback=1, persistence_candles=1).detect(candles)

    choch = events[-1]
    assert choch.event is StructureEvent.CHANGE_OF_CHARACTER
    assert choch.direction is MarketDirection.BEARISH
    assert choch.price_level == 120.0
    # The break is sustained from this candle onward (no earlier lag here).
    assert choch.timestamp == candles[13].timestamp
    assert choch.reference_price_level == 130.0


def test_break_above_trailing_high_below_validated_is_a_sweep() -> None:
    """In a bearish leg, a high that breaks the trailing active_high but not
    validated_choch_high is an internal bounce -> LIQUIDITY_SWEEP, trend
    unchanged (no CHoCH).
    """
    # index 1 high 200 -> validated_choch_high becomes 200 at the index-5 LL.
    # index 7 high 160 re-bootstraps active_high; index 9 high 180 breaks that
    # trailing 160 but stays below validated 200 -> sweep.
    highs = [150.0] * 12
    for index, value in {1: 200.0, 7: 160.0, 9: 180.0}.items():
        highs[index] = value
    lows = [140.0] * 12
    for index, value in {3: 120.0, 5: 90.0}.items():
        lows[index] = value
    candles = make_series(highs, lows)

    events = InternalStructureDetector(swing_lookback=1, persistence_candles=1).detect(candles)

    sweep = events[-1]
    assert sweep.event is StructureEvent.LIQUIDITY_SWEEP
    assert sweep.direction is MarketDirection.BULLISH
    assert sweep.price_level == 180.0
    assert sweep.reference_price_level == 160.0
    # The sweep must not flip the trend: no CHANGE_OF_CHARACTER is emitted.
    assert all(e.event is not StructureEvent.CHANGE_OF_CHARACTER for e in events)


# A short sequence (lookback=1) where the low pivot at index 7 (60) breaks
# active_low (90) while trend is BULLISH, used to exercise persistence-based
# confirmation with persistence_candles=2:
#
#   index 1: high 200 -> bootstraps active_high
#   index 3: low   90 -> bootstraps active_low; the first low pivot ever, so
#                          it is never labeled HL and never becomes
#                          candidate_choch_low
#   index 5: high 210 -> BOS bullish (NEUTRAL -> BULLISH); new HH, but
#                          candidate_choch_low is None -> validated_choch_low
#                          stays None
#   index 7: low   60 -> breaks active_low (90); with validated_choch_low
#                          still None there is no CHoCH reference to confirm
#                          against, so this is always a LIQUIDITY_SWEEP --
#                          regardless of persistence_candles or trailing
#                          closes.
_PERSISTENCE_HIGHS = [150.0, 200.0, 150.0, 150.0, 150.0, 210.0, 150.0, 150.0]
_PERSISTENCE_LOWS = [140.0, 140.0, 140.0, 90.0, 140.0, 140.0, 140.0, 60.0]


def _persistence_test_series(
    *, index_8_close: float, index_9_close: float | None
) -> list[Candle]:
    candles = make_series(_PERSISTENCE_HIGHS, _PERSISTENCE_LOWS)
    candles[7] = make_candle(7, 150.0, 60.0, close=65.0)
    candles.append(make_candle(8, 85.0, 70.0, close=index_8_close))
    if index_9_close is not None:
        candles.append(make_candle(9, 95.0, 70.0, close=index_9_close))
    return candles


def test_persistence_irrelevant_without_validated_choch_reference() -> None:
    """Even though the pivot's close (65) and the next 2 candles' closes (75,
    80) would sustain a break of active_low (90), the result is still
    LIQUIDITY_SWEEP: 90 (index 3) was never labeled HL/candidate_choch_low, so
    the BOS at index 5 has nothing to promote and validated_choch_low stays
    None -- there is no CHoCH reference for persistence to confirm a break
    of."""
    candles = _persistence_test_series(index_8_close=75.0, index_9_close=80.0)

    events = InternalStructureDetector(swing_lookback=1, persistence_candles=2).detect(candles)

    assert events[-1].event is StructureEvent.LIQUIDITY_SWEEP
    assert events[-1].direction is MarketDirection.BEARISH
    assert events[-1].price_level == 60.0
    assert events[-1].reference_price_level == 90.0


def test_reversal_within_persistence_window_yields_liquidity_sweep() -> None:
    """The pivot's close (65) clears validated_choch_low (90), but the second
    following candle closes back above it (92) -- a "false break" ->
    LIQUIDITY_SWEEP."""
    candles = _persistence_test_series(index_8_close=75.0, index_9_close=92.0)

    events = InternalStructureDetector(swing_lookback=1, persistence_candles=2).detect(candles)

    assert events[-1].event is StructureEvent.LIQUIDITY_SWEEP
    assert events[-1].direction is MarketDirection.BEARISH
    assert events[-1].price_level == 60.0
    assert events[-1].reference_price_level == 90.0


def test_insufficient_trailing_candles_yields_liquidity_sweep() -> None:
    """The CHoCH-candidate pivot is too close to the end: there aren't
    `persistence_candles` candles after it to evaluate, so the break is
    treated as unconfirmed regardless of its own close."""
    candles = _persistence_test_series(index_8_close=75.0, index_9_close=None)

    events = InternalStructureDetector(swing_lookback=1, persistence_candles=2).detect(candles)

    assert events[-1].event is StructureEvent.LIQUIDITY_SWEEP
    assert events[-1].direction is MarketDirection.BEARISH
    assert events[-1].price_level == 60.0
    assert events[-1].reference_price_level == 90.0


# --- Real-data regression -------------------------------------------------
#
# BTCUSDT 1h, 2026-06-02 22:00 UTC -> 2026-06-08 00:00 UTC (123 candles,
# fetched from Binance, stored as [timestamp_ms, high, low, close]). On this
# window the bearish leg prints its lowest low (59,131 at 2026-06-05 19:00Z)
# preceded by the swing high 61,547 (2026-06-05 16:00Z); that high becomes
# validated_choch_high and stays frozen (no lower low follows), so the break
# above it at 62,960 (the confirming swing-high pivot) is a bullish CHoCH
# referencing 61,547 -- NOT the leg's highest high (~64,495) nor a pullback
# top. The sustained break above 61,547 actually begins three candles before
# that pivot, at 2026-06-07 03:00Z (close 61,701.07) -- the CHoCH is
# timestamped there, while price_level remains the confirming pivot's price
# (62,960).
#
# This is a RULE-semantics regression at swing_lookback=2 (the granularity at
# which this structure's swing highs exist); production defaults to
# swing_lookback=10, at which this window has only 3 pivots and produces no
# CHoCH (asserted below) -- so it is not a production-parity test.
_WINDOW_DATA = Path(__file__).parent / "data" / "btcusdt_1h_2026_06_02_08.json"
_EXPECTED_CHOCH_REFERENCE = 61547.24
_EXPECTED_CHOCH_PRICE = 62960.0


def _load_window_candles() -> list[Candle]:
    rows = json.loads(_WINDOW_DATA.read_text())
    return [
        Candle(
            symbol="BTCUSDT",
            timeframe=TimeFrame.H1,
            timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
            open=close,
            high=high,
            low=low,
            close=close,
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for timestamp_ms, high, low, close in rows
    ]


def test_real_window_bullish_choch_references_validated_high() -> None:
    candles = _load_window_candles()

    events = InternalStructureDetector(swing_lookback=2, persistence_candles=3).detect(candles)

    chochs = [e for e in events if e.event is StructureEvent.CHANGE_OF_CHARACTER]
    assert len(chochs) == 1
    (choch,) = chochs
    assert choch.direction is MarketDirection.BULLISH
    assert choch.price_level == _EXPECTED_CHOCH_PRICE
    assert choch.reference_price_level == _EXPECTED_CHOCH_REFERENCE
    assert choch.timestamp == datetime(2026, 6, 7, 3, 0, tzinfo=UTC)


def test_real_window_production_lookback_emits_no_choch() -> None:
    """At the production default swing_lookback=10 this window is too coarse to
    surface the swing-high structure, so no (spurious) CHoCH is emitted."""
    candles = _load_window_candles()

    events = InternalStructureDetector(swing_lookback=10, persistence_candles=3).detect(candles)

    assert all(e.event is not StructureEvent.CHANGE_OF_CHARACTER for e in events)

def test_choch_detected_when_confirmation_extends_beyond_pivot_index() -> None:
    """
    Testa a lógica de detecção: Garante que o detector não ignore um CHoCH 
    se as velas de confirmação (persistence) terminarem após o índice do pivô.
    """
    highs = [150.0] * 20
    lows = [140.0] * 20

    # Setup: Estrutura que força o CHoCH
    highs[1] = 200.0  # bootstrap active_high
    lows[3] = 100.0   # bootstrap active_low
    lows[5] = 80.0    # Novo LL -> validated_choch_high = 200.0
    highs[7] = 170.0  # LH
    lows[9] = 60.0    # Novo LL -> validated_choch_high = 170.0

    candles = make_series(highs, lows)

    # O Rompimento começa no índice 12
    candles[12] = make_candle(12, high=173.0, low=140.0, close=172.0)
    # O Pivô ocorre no índice 13
    candles[13] = make_candle(13, high=175.0, low=140.0, close=174.0)
    # A confirmação (persistence=2) termina no índice 14
    candles[14] = make_candle(14, high=173.0, low=140.0, close=171.0)

    # Executa o detector com persistência 2
    detector = InternalStructureDetector(swing_lookback=1, persistence_candles=2)
    events = detector.detect(candles)

    # Filtra apenas eventos de CHoCH
    choch_events = [e for e in events if e.event == StructureEvent.CHANGE_OF_CHARACTER]

    # O teste garante que o evento foi capturado (não foi descartado pelo limite de busca)
    assert len(choch_events) > 0, (
        "O CHoCH não foi detectado porque a busca parou no índice do pivô."
    )
    assert choch_events[0].timestamp == candles[12].timestamp, (
        "O timestamp deve ser o da vela de rompimento original."
    )

def test_state_machine_hierarchy_choch_then_bos() -> None:
    """
    Garante que a máquina de estados respeite a hierarquia SMC:
    1. O bootstrap inicial (Bearish) é ignorado na validação.
    2. O primeiro rompimento de alta confirmado gera um CHoCH Bullish.
    3. O rompimento seguinte na mesma direção DEVE ser um BOS Bullish (continuação).
    """
    highs = [150.0] * 40
    lows = [140.0] * 40

    # 1. SETUP DA TENDÊNCIA DE BAIXA INICIAL (Gera o BOS Bearish de bootstrap)
    highs[1] = 200.0
    lows[3] = 100.0
    lows[5] = 80.0
    highs[7] = 170.0  # LH (validated_choch_high)
    lows[9] = 60.0

    candles = make_series(highs, lows)

    # 2. ROMPIMENTO DO CHOCH (velas com 'close' explícito > 170.0 para garantir persistência)
    candles[12] = make_candle(12, high=175.0, low=140.0, close=172.0)
    candles[13] = make_candle(13, high=180.0, low=150.0, close=175.0)  # Pivô do CHoCH
    candles[14] = make_candle(14, high=178.0, low=150.0, close=171.0)

    # 3. RETRAÇÃO (gera o active_high a ser rompido no BOS)
    candles[16] = make_candle(16, high=185.0, low=160.0, close=170.0)  # Novo topo
    candles[18] = make_candle(18, high=175.0, low=165.0, close=168.0)  # Fundo mais alto (HL)

    # 4. ROMPIMENTO DE CONTINUAÇÃO (BOS Bullish com 'close' explícito > 185.0)
    candles[20] = make_candle(20, high=190.0, low=170.0, close=188.0)
    candles[21] = make_candle(21, high=195.0, low=170.0, close=192.0)
    candles[22] = make_candle(22, high=192.0, low=170.0, close=190.0)

    # Executa o detector
    detector = InternalStructureDetector(swing_lookback=1, persistence_candles=2)
    events = detector.detect(candles)

    # Filtra apenas eventos Bullish para ignorar o BOS Bearish de inicialização
    bullish_events = [
        e
        for e in events
        if e.direction == MarketDirection.BULLISH
        and e.event in (StructureEvent.CHANGE_OF_CHARACTER, StructureEvent.BREAK_OF_STRUCTURE)
    ]

    assert len(bullish_events) >= 2, "Deveriam ser detectados pelo menos o CHoCH e o BOS de alta."

    first_bullish = bullish_events[0]
    assert (
        first_bullish.event is StructureEvent.CHANGE_OF_CHARACTER
    ), "O primeiro evento de alta DEVE ser um CHoCH."
    assert first_bullish.reference_price_level == 170.0

    second_bullish = bullish_events[1]
    assert (
        second_bullish.event is StructureEvent.BREAK_OF_STRUCTURE
    ), "O evento seguinte na mesma direção DEVE ser um BOS."

def test_trend_state_does_not_leak_on_liquidity_sweep() -> None:
    """
    Garante que a máquina de estados não inverta a tendência durante um
    Sweep (falso rompimento) do nível de CHoCH. O estado só pode mudar
    se o rompimento for confirmado (persistence validada).
    """
    highs = [150.0] * 30
    lows = [140.0] * 30

    # 1. SETUP: Tendência de Baixa Inicial (Bootstrap)
    highs[1] = 200.0
    lows[3] = 100.0
    lows[5] = 80.0
    highs[7] = 170.0  # LH (Pivô de alta que será nossa referência de CHoCH: validated_choch_high)
    lows[9] = 60.0    # LL 

    candles = make_series(highs, lows)

    # 2. O FALSO ROMPIMENTO (Sweep no CHoCH)
    # Rompe a máxima (175 > 170), mas o fechamento é abaixo (165), rejeitando o rompimento.
    candles[12] = make_candle(12, high=175.0, low=140.0, close=165.0)

    # 3. CONTINUAÇÃO DA TENDÊNCIA ORIGINAL (Queda)
    # O preço rejeita o topo e volta a cair, perdendo o fundo anterior (60.0)
    candles[15] = make_candle(15, high=100.0, low=50.0, close=55.0) # Rompe o fundo de 60.0
    candles[16] = make_candle(16, high=90.0, low=40.0, close=45.0)  # Confirma a queda
    candles[17] = make_candle(17, high=80.0, low=45.0, close=50.0)  # Confirma a queda

    # Executa o detector (exigindo 2 velas de persistência)
    detector = InternalStructureDetector(swing_lookback=1, persistence_candles=2)
    events = detector.detect(candles)

    # Coleta todos os eventos
    sweep_events = [e for e in events if e.event == StructureEvent.LIQUIDITY_SWEEP]
    bos_events = [e for e in events if e.event == StructureEvent.BREAK_OF_STRUCTURE]
    choch_events = [e for e in events if e.event == StructureEvent.CHANGE_OF_CHARACTER]

    # Validações Críticas
    assert len(sweep_events) >= 1, "Um Sweep deveria ser detectado no falso rompimento de 170.0."
    assert len(choch_events) == 0, (
        "ERRO: O código emitiu um CHoCH! O Sweep vazou e mudou o estado da tendência."
    )

    bearish_bos = [e for e in bos_events if e.direction == MarketDirection.BEARISH]
    assert len(bearish_bos) >= 2, "Deveria ter o BOS inicial de bootstrap e o BOS de continuação."

def test_state_machine_does_not_shift_references_on_multiple_sweeps() -> None:
    """
    Garante que múltiplos Sweeps seguidos no mesmo nível de CHoCH não desloquem
    as referências internas de pivô nem criem CHoCHs fantasmas quando a tendência
    original for retomada de forma agressiva.
    """
    highs = [150.0] * 35
    lows = [140.0] * 35

    # 1. SETUP: Tendência de Baixa Inicial (Bootstrap)
    highs[1] = 200.0
    lows[3] = 100.0
    lows[5] = 80.0
    highs[7] = 170.0  # Nosso CHoCH reference inicial (LH)
    lows[9] = 60.0    # LL

    candles = make_series(highs, lows)

    # 2. PRIMEIRO SWEEP (Preço cutuca 172 e volta para 160)
    candles[12] = make_candle(12, high=172.0, low=140.0, close=160.0)

    # 3. SEGUNDO SWEEP (Preço vai mais alto em 175, mas fecha abaixo em 155)
    # Se o código atualizar o CHoCH para 175 ou se perder no estado do pivô, ele vai quebrar aqui
    candles[15] = make_candle(15, high=175.0, low=135.0, close=155.0)

    # 4. RETOMADA AGRESSIVA DA QUEDA (Derretimento)
    candles[18] = make_candle(18, high=110.0, low=50.0, close=52.0)  # Quebra o fundo de 60
    candles[19] = make_candle(19, high=90.0, low=40.0, close=42.0)   # Confirmação 1
    candles[20] = make_candle(20, high=80.0, low=35.0, close=38.0)   # Confirmação 2

    detector = InternalStructureDetector(swing_lookback=1, persistence_candles=2)
    events = detector.detect(candles)

    choch_events = [e for e in events if e.event == StructureEvent.CHANGE_OF_CHARACTER]
    bos_events = [e for e in events if e.event == StructureEvent.BREAK_OF_STRUCTURE]

    # Validações: não pode ter CHoCH, e a continuação tem que ser um BOS Bearish limpo
    assert len(choch_events) == 0, "ERRO: O acúmulo de Sweeps fakes gerou um CHoCH fantasma!"

    bearish_bos = [e for e in bos_events if e.direction == MarketDirection.BEARISH]
    assert len(bearish_bos) >= 2, (
        "A estrutura deveria continuar em BOS Bearish após romper o fundo de 60.0"
    )


# --- Streaming / live-update behavior ---------------------------------------
#
# `InternalStructureDetector.detect()` is a pure function of the candle list
# it is given: all state (active_<side>, pending_<side>, validated_choch_<side>,
# trend, etc.) is local to a single `detect()` call, and the constructor holds
# only `_high_detector`/`_low_detector`/`_persistence_candles`, none of which
# `detect()` mutates. So calling `detect()` repeatedly on the same (or a
# reused) detector instance cannot leak state between calls.
#
# The two scenarios below distinguish:
# - APPEND-ONLY growth (no candles dropped): the prefix of emitted events is
#   stable as more candles arrive, because `collect_pivots` only ever
#   recognizes *new* pivots near the end of the series (a pivot's
#   lookback-window neighbors don't change once they're all present), so the
#   pivot sequence walked from the start is unchanged.
# - SLIDING WINDOW (fixed size, oldest candle dropped as a newest one
#   arrives) -- the pattern `load_dashboard_data` uses via
#   `provider.get_ohlcv(symbol, timeframe, limit)` on every refresh: the
#   window's *first* pivot of each kind seeds the bootstrap (`active_high`/
#   `active_low`/`trend`/`validated_choch_<side>`), and that first pivot
#   changes every time the window shifts. The same underlying candle can
#   therefore be reported as a different event (e.g. BREAK_OF_STRUCTURE vs.
#   LIQUIDITY_SWEEP) across two windows that differ by just one candle at each
#   end -- this is the source of "alternating CHoCH/BOS" flicker on live
#   charts, not state leaking inside `detect()`.
_STREAM_HIGHS = [150.0] * 30
_stream_high_overrides = {1: 200.0, 5: 220.0, 9: 210.0, 13: 230.0, 17: 215.0, 21: 240.0, 25: 235.0}
for _index, _value in _stream_high_overrides.items():
    _STREAM_HIGHS[_index] = _value
_STREAM_LOWS = [140.0] * 30
for _index, _value in {3: 90.0, 7: 100.0, 11: 80.0, 15: 70.0, 19: 60.0, 23: 90.0, 27: 50.0}.items():
    _STREAM_LOWS[_index] = _value


def test_streaming_append_only_growth_is_prefix_stable() -> None:
    """Simulates streaming with no truncation: `detect()` is called on an
    ever-growing prefix of the same series. Earlier emitted events must not
    change (or disappear) as later candles are appended -- there is no
    instance state to leak, and `collect_pivots` only adds new pivots at the
    end."""
    candles = make_series(_STREAM_HIGHS, _STREAM_LOWS)

    detector = InternalStructureDetector(swing_lookback=1)
    previous_events: list[
        tuple[datetime, StructureEvent, MarketDirection, float, float | None]
    ] = []
    for n in range(10, len(candles) + 1):
        events = detector.detect(candles[:n])
        current_events = [
            (e.timestamp, e.event, e.direction, e.price_level, e.reference_price_level)
            for e in events
        ]
        common_length = min(len(previous_events), len(current_events))
        assert previous_events[:common_length] == current_events[:common_length], (
            f"event prefix changed when growing the series to {n} candles"
        )
        previous_events = current_events


def test_streaming_sliding_window_reclassifies_same_candle() -> None:
    """A fixed-size sliding window -- one candle dropped from the front, one
    added at the back, as `load_dashboard_data` does on every refresh --
    re-bootstraps `active_low`/`trend`/`validated_choch_high` from the new
    window's first pivots. The candle at index 11 (a low of 80.0, breaking the
    bootstrap active_low of 100.0) is a LIQUIDITY_SWEEP in the window starting
    at index 0 (trend still NEUTRAL there) but a BREAK_OF_STRUCTURE in the
    window starting at index 1 (where it is now the *first* low pivot,
    breaking nothing yet from NEUTRAL)."""
    candles = make_series(_STREAM_HIGHS, _STREAM_LOWS)
    window_size = 15

    window_0 = candles[0:window_size]
    window_1 = candles[1 : window_size + 1]

    events_0 = InternalStructureDetector(swing_lookback=1).detect(window_0)
    events_1 = InternalStructureDetector(swing_lookback=1).detect(window_1)

    def find_by_timestamp(events: list[MarketStructure], timestamp: datetime) -> MarketStructure:
        return next(e for e in events if e.timestamp == timestamp)

    pivot_in_window_0 = find_by_timestamp(events_0, candles[11].timestamp)
    pivot_in_window_1 = find_by_timestamp(events_1, candles[11].timestamp)

    assert pivot_in_window_0.event is StructureEvent.LIQUIDITY_SWEEP
    assert pivot_in_window_1.event is StructureEvent.BREAK_OF_STRUCTURE