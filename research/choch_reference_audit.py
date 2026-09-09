"""Etapa 4.4: a referencia que o CHoCH exige quebrar e a certa?

Tres etapas fecharam a investigacao do `StructuralStall` como resposta ao ZEC
D1 (4.1 opener de CHoCH, 4.2 ritmo estrutural, 4.3 gate de establishment --
todas reprovadas em painel amplo e holdout). O mecanismo que sobreviveu as
tres e outro, e e geometrico:

- na auditoria original, o CHoCH bearish do ZEC de 2026-06-04 so ocorreu
  depois de metade da queda porque sua referencia (o `higher_low` de 486.00)
  estava 24,6% abaixo do topo de 644.67;
- na Etapa 4.3, `distance_to_bos_atr` foi a feature mais informativa
  (AUC 0,63) para separar pernas que estabelecem continuacao das que nao.

Entao a pergunta aqui nao e mais "quando declarar a perna parada", e sim
**qual estrutura deveria servir de referencia para o CHoCH**.

Como o estado interno e observado
=================================

A cadeia de referencia vive em variaveis locais do closure de
`InternalStructureDetector.detect` -- `validated_choch_<side>`,
`choch_origin_<side>`, `active_<side>`, `candidate_choch_<side>` e seus
baselines, o re-arm, o `pending_bos`. Nada disso e exportado, e a etapa proibe
tocar no detector.

A solucao aqui e ler, nao alterar: um `sys.settrace` instalado so em volta da
chamada, ativo so para o code object de `detect`, tira um retrato de
`frame.f_locals` na cabeca do laco de pivos (a linha `current_index =
index_by_timestamp[timestamp]`). O retrato e o estado **antes** de processar
aquele pivo, que e exatamente o estado que aquele pivo consultou. Custo
medido: 0,34s por janela contra 0,01s sem trace -- barato o bastante para o
painel inteiro. O detector nao sabe que esta sendo observado e nenhum byte de
producao muda.

Classificacao da origem (secao 9)
=================================

A origem de cada referencia NAO e inferida por heuristica: o
`reference_price_level` do CHoCH emitido e casado por identidade contra os
slots do retrato daquele pivo, na ordem de precedencia do proprio codigo
(`validated` > `pending_leg_origin` > `choch_origin` > `rearm` > `active`).
Quando nenhum slot bate, a linha e reportada como `desconhecida` em vez de
empurrada para a categoria mais proxima -- a taxa de desconhecidas e uma
medida da qualidade da leitura, e esta no relatorio.

O que NAO esta sendo perguntado
===============================

A secao 16 do pedido e obrigatoria e vale repetir aqui: nada neste modulo
troca estrutura por deslocamento. Toda referencia alternativa testada e um
nivel que a maquina JA conhecia naquele candle (um pivo confirmado, um
candidate, um baseline, uma origem armada). "O preco caiu muito" nunca e
gatilho.

E a metrica principal nao e antecipacao (secao 14): e antecipacao **liquida**
de falsos flips. Toda alternativa e reportada com o lead que ganha E com
quantas correcoes normais ela transformaria em reversao.

    poetry run python -m research.choch_reference_audit
    poetry run python -m research.choch_reference_audit --expanded --json \
        research/choch_reference_audit_baseline.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from types import FrameType
from typing import Any

from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.liquidity.detectors.internal_structure import InternalStructureDetector
from liquidity_hunter.liquidity.structural_stall import frozen_atr_pct
from research.choch_leg_opener import (
    DISCOVERY_FRACTION,
    SYMBOLS,
    TIMEFRAMES,
    WINDOWS,
)
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series
from research.zec_d1_structure_lag import leg_extreme_before

#: A linha do laco de pivos de `detect` onde o retrato e tirado. Verificada em
#: tempo de execucao por `assert_trace_line()` -- se o detector mudar de
#: tamanho, a medicao para com erro em vez de sair silenciosamente vazia.
LOOP_LINE = 1919
LOOP_SOURCE = "current_index = index_by_timestamp[timestamp]"

#: Os slots da cadeia de referencia, na ordem de precedencia do codigo.
STATE_KEYS = (
    "trend",
    "validated_choch_high",
    "validated_choch_low",
    "validated_choch_high_structural",
    "validated_choch_low_structural",
    "choch_origin_high",
    "choch_origin_low",
    "active_high",
    "active_low",
    "candidate_choch_high",
    "candidate_choch_low",
    "candidate_choch_high_baseline",
    "candidate_choch_low_baseline",
    "bull_choch_origin",
    "bear_choch_origin",
    "bull_choch_rearm",
    "bear_choch_rearm",
    "pending_bos",
    "pending_high",
    "pending_low",
)

#: Quantis do agrupamento por lag (secao 8).
LOW_LAG_Q = 0.25
HIGH_LAG_Q = 0.75

#: Janela retrospectiva para achar o extremo de onde o movimento partiu.
MOVE_LOOKBACK = 120
#: Janela para julgar o desfecho de uma quebra contrafactual.
OUTCOME_WINDOW = 80
#: "Retorno rapido a tendencia anterior" (secao 14).
QUICK_FLIP_BACK = 20

ZEC_BEARISH = ("ZECUSDT", TimeFrame.D1, "2026-06-04T00:00:00+00:00")
ZEC_BULLISH = ("ZECUSDT", TimeFrame.D1, "2026-08-19T00:00:00+00:00")


def assert_trace_line() -> None:
    """A linha tracada e mesmo a cabeca do laco de pivos?

    O retrato inteiro depende de um numero de linha. Se o detector for
    editado, este assert quebra a medicao de proposito -- o modo de falha
    silencioso (tracar a linha errada e colher zero retratos) produziria um
    relatorio inteiro de `None` com cara de resultado.
    """
    import inspect

    source = inspect.getsource(InternalStructureDetector.detect)
    start = InternalStructureDetector.detect.__code__.co_firstlineno
    line = source.splitlines()[LOOP_LINE - start].strip()
    if line != LOOP_SOURCE:
        raise RuntimeError(
            f"a linha {LOOP_LINE} do detector virou {line!r}, nao {LOOP_SOURCE!r}; "
            "o retrato do estado interno esta apontando para o lugar errado"
        )


# --------------------------------------------------------------------------
# o retrato do estado interno
# --------------------------------------------------------------------------


@dataclass
class StateSnapshot:
    """A cadeia de referencia como estava antes de processar um pivo."""

    timestamp: datetime
    state: dict[str, Any]


def _pivot_of(value: Any) -> tuple[float, datetime] | None:
    """`(preco, timestamp)` de um `Pivot`, ou `None`."""
    if value is None or value == "<ausente>":
        return None
    price = getattr(value, "price", None)
    timestamp = getattr(value, "timestamp", None)
    if price is None or timestamp is None:
        return None
    return (float(price), timestamp)


def traced_run(
    symbol: str, timeframe: TimeFrame, series: Sequence[Candle], end: int, limit: int = LIMIT
):
    """Uma janela de producao, com os retratos do estado interno junto.

    Roda exatamente `dd._run_internal_structure` -- mesma fiacao, mesmos
    passes, mesmo resultado -- e devolve `(run, snapshots)`. O `settrace` e
    instalado e removido em volta da chamada, e so responde ao code object de
    `detect`, entao nada mais do processo e observado ou afetado.
    """
    start = end - limit - BUFFER
    code = InternalStructureDetector.detect.__code__
    snapshots: list[StateSnapshot] = []

    def line_tracer(frame: FrameType, event: str, arg: Any):
        if event == "line" and frame.f_lineno == LOOP_LINE:
            local = frame.f_locals
            snapshots.append(
                StateSnapshot(
                    timestamp=local["timestamp"],
                    state={key: local.get(key, "<ausente>") for key in STATE_KEYS},
                )
            )
        return line_tracer

    def call_tracer(frame: FrameType, event: str, arg: Any):
        if event == "call" and frame.f_code is code:
            frame.f_trace_lines = True
            return line_tracer
        return None

    previous = sys.gettrace()
    sys.settrace(call_tracer)
    try:
        run = dd._run_internal_structure(
            provider=SliceProvider(list(series[max(start, 0) : end])),
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            confluence_filter=True,
        )
    finally:
        sys.settrace(previous)
    return run, snapshots


def snapshot_before(
    snapshots: Sequence[StateSnapshot], timestamp: datetime
) -> StateSnapshot | None:
    """O ultimo retrato em ou antes de `timestamp` -- o estado que aquele
    candle consultou. Causal por construcao: retratos posteriores nao entram."""
    chosen = None
    for snapshot in snapshots:
        if snapshot.timestamp <= timestamp:
            chosen = snapshot
        else:
            break
    return chosen


#: Os slots que podem servir de referencia de CHoCH, na ordem de precedencia
#: do detector. `pending_leg_origin` e o `pullback_ref` do `pending_bos`
#: oposto, que o codigo insere entre `validated` e `choch_origin`.
def reference_slots(
    state: dict[str, Any], bullish_choch: bool
) -> list[tuple[str, float, datetime]]:
    """Os candidatos a referencia daquele lado, na ordem do codigo."""
    side = "high" if bullish_choch else "low"
    wanted = MarketDirection.BEARISH if bullish_choch else MarketDirection.BULLISH
    out: list[tuple[str, float, datetime]] = []

    validated = _pivot_of(state.get(f"validated_choch_{side}"))
    if validated:
        out.append(("validated", *validated))

    pending = state.get("pending_bos")
    if pending is not None and pending != "<ausente>":
        if getattr(pending, "direction", None) is wanted:
            origin = _pivot_of(getattr(pending, "pullback_ref", None))
            if origin:
                out.append(("pending_leg_origin", *origin))

    blind = _pivot_of(state.get(f"choch_origin_{side}"))
    if blind:
        out.append(("choch_origin", *blind))

    rearm = _pivot_of(state.get(f"{'bull' if bullish_choch else 'bear'}_choch_rearm"))
    if rearm:
        out.append(("rearm", *rearm))

    active = _pivot_of(state.get(f"active_{side}"))
    if active:
        out.append(("active_fallback", *active))
    return out


#: Quantos retratos vizinhos consultar ao nomear a origem de uma referencia.
CLASSIFY_SPAN = 3


def classify_reference(
    snapshots: Sequence[StateSnapshot],
    event: MarketStructure,
    tolerance: float = 1e-6,
) -> tuple[str, int]:
    """De qual slot veio a referencia deste CHoCH, e de qual retrato.

    Casamento por identidade de preco, na ordem de precedencia do codigo. Nao
    ha heuristica de proximidade: se nenhum slot bate, a resposta e
    `desconhecida` e entra assim no relatorio.

    O retrato e tirado na CABECA da iteracao de pivo, entao ele mostra o
    estado *entrando* naquele pivo -- e o CHoCH e emitido no meio do
    processamento de algum pivo, depois de mutacoes que ainda vao acontecer.
    O estado que ele consultou fica portanto ENTRE dois retratos consecutivos.
    Por isso a busca varre `CLASSIFY_SPAN` retratos para cada lado e devolve
    tambem o deslocamento usado: nomear a origem e uma consulta retrospectiva,
    nao uma afirmacao causal, e a distribuicao dos deslocamentos esta no
    relatorio para que isso fique visivel.
    """
    level = event.reference_price_level
    if level is None:
        return ("sem_referencia", 0)
    bullish = event.direction is MarketDirection.BULLISH
    anchor = -1
    for position, snapshot in enumerate(snapshots):
        if snapshot.timestamp <= event.timestamp:
            anchor = position
        else:
            break
    if anchor < 0:
        return ("sem_retrato", 0)
    # Do retrato mais proximo para o mais distante: um casamento a 3 pivos de
    # distancia so vale se nenhum vizinho explicar o nivel. Varrer na ordem
    # ingenua (-3 para cima) faz o retrato MAIS VELHO ganhar, o que enche a
    # tabela de origens erradas -- foi o que aconteceu na primeira rodada.
    order = [0]
    for distance in range(1, CLASSIFY_SPAN + 1):
        order.extend((-distance, distance))
    for offset in order:
        position = anchor + offset
        if not 0 <= position < len(snapshots):
            continue
        for name, price, _timestamp in reference_slots(snapshots[position].state, bullish):
            if abs(price - level) <= tolerance * max(abs(price), 1.0):
                return (name, offset)
    return ("desconhecida", 0)


# --------------------------------------------------------------------------
# um CHoCH, medido
# --------------------------------------------------------------------------


@dataclass
class Alternative:
    """Uma referencia que a maquina JA conhecia, e o que ela teria feito."""

    #: De onde ela veio: `last_confirmed_pullback`, `candidate`, `baseline`...
    source: str
    price: float
    timestamp: str
    #: Idade e distancia no candle do CHoCH oficial, para comparar com a oficial.
    age_bars: int
    distance_atr: float
    #: Primeiro fechamento alem dela, com a mesma persistencia da producao.
    break_index: int | None = None
    break_timestamp: str | None = None
    #: Quanto antes do CHoCH oficial (positivo = mais cedo).
    lead_bars: int | None = None
    lead_atr: float | None = None
    #: Desfecho da quebra antecipada (avaliacao retrospectiva, secao 14).
    outcome: str | None = None
    #: A direcao nova ainda valia `QUICK_FLIP_BACK` velas depois?
    held_quick: bool | None = None


@dataclass
class ChochCase:
    """Um CHoCH nao-provisional: sua referencia, seu atraso e as alternativas."""

    symbol: str
    timeframe: str
    window: int
    timestamp: str
    index: int
    direction: str
    price_level: float
    # --- a referencia oficial ---
    reference_price: float | None
    reference_timestamp: str | None
    reference_structural: bool | None
    reference_source: str
    #: Deslocamento em retratos usado para nomear a origem (0 = o retrato que
    #: entra no pivo do evento). Reportado para nao esconder a ambiguidade.
    reference_source_offset: int
    reference_age_bars: int | None
    #: Distancia da referencia ao extremo de onde o movimento partiu -- a
    #: medida do ZEC: "a referencia estava 24,6% abaixo do topo".
    reference_to_extreme_atr: float | None
    reference_to_extreme_pct: float | None
    #: Distancia da referencia ao preco no proprio candle do CHoCH.
    reference_distance_atr: float | None
    #: Pivos confirmados que surgiram depois da referencia e antes do CHoCH.
    pivots_since_reference: int
    # --- o atraso (secao 6) ---
    move_start_index: int | None
    choch_lag_bars: int | None
    choch_lag_atr: float | None
    move_completed_at_choch_pct: float | None
    # --- alternativas (secoes 3, 10, 11) ---
    alternatives: list[dict] = field(default_factory=list)
    frozen_atr: float = 0.0


def swing_lookback_of(timeframe: TimeFrame) -> int:
    """O `swing_lookback` da producao -- e a latencia de confirmacao do pivo."""
    return dd._INTERNAL_STRUCTURE_PARAMS[timeframe][0]


def confirmed_pullbacks(
    events: Sequence[MarketStructure],
    upto: datetime,
    bullish_choch: bool,
    candles: Sequence[Candle] | None = None,
    lookback: int = 0,
) -> list[MarketStructure]:
    """Pivos de pullback confirmados ate `upto`, do lado que um CHoCH rompe.

    Um CHoCH bullish rompe para CIMA, entao o nivel relevante e um
    `lower_high`; um bearish rompe para baixo, e o nivel e um `higher_low`.
    Sao os unicos pivos que o detector emite no stream (ele nunca emite
    `higher_high` / `lower_low`), e sao objetos que a maquina ja conhece --
    a secao 3 proibe inventar pivo novo.

    Um pivo e DATADO na vela que o formou, mas so e CONHECIDO `lookback`
    velas depois -- e o que o detector espera para confirma-lo. Usar o pivo a
    partir da sua data seria um lookahead de `swing_lookback` velas, e foi
    exatamente o erro da primeira rodada desta etapa: o lead das alternativas
    saia inflado por essa janela. Aqui o pivo so entra depois de confirmado.
    """
    wanted = StructureEvent.LOWER_HIGH if bullish_choch else StructureEvent.HIGHER_LOW
    out = []
    by_ts = (
        {candle.timestamp: i for i, candle in enumerate(candles)} if candles else {}
    )
    for event in events:
        if event.provisional or event.event is not wanted:
            continue
        index = by_ts.get(event.timestamp)
        if index is not None and candles is not None:
            confirmed_at = index + lookback
            if confirmed_at >= len(candles) or candles[confirmed_at].timestamp > upto:
                continue
        elif event.timestamp > upto:
            continue
        out.append(event)
    return out


def first_close_beyond(
    candles: Sequence[Candle],
    start: int,
    end: int,
    level: float,
    bullish: bool,
    persistence: int,
) -> int | None:
    """Primeiro candle em [start, end] que fecha alem de `level` e SUSTENTA.

    Mesma exigencia da producao: o fechamento alem do nivel vale como quebra
    so se os `persistence` candles seguintes tambem fecharem alem. Sem isso a
    comparacao seria injusta com o detector -- ele paga esse custo e a
    alternativa nao pagaria.
    """
    for index in range(start, min(end, len(candles) - persistence)):
        window = candles[index : index + persistence + 1]
        if bullish and all(candle.close > level for candle in window):
            return index
        if not bullish and all(candle.close < level for candle in window):
            return index
    return None


def outcome_after(
    events: Sequence[MarketStructure],
    by_ts: dict[datetime, int],
    index: int,
    direction: MarketDirection,
    window: int = OUTCOME_WINDOW,
) -> tuple[str, bool]:
    """O que a estrutura fez depois de uma quebra contrafactual (secao 14).

    - `confirmado`   -- veio um BOS da nova direcao (a reversao era real);
    - `falso_flip`   -- veio um BOS da direcao ANTIGA (era correcao normal);
    - `choch_failed` -- a maquina marcou `✕` na nova direcao;
    - `sem_desfecho` -- a janela acabou.

    `held_quick` responde separadamente se a nova direcao ainda valia
    `QUICK_FLIP_BACK` velas depois -- o "retorno rapido" do pedido.
    """
    verdict = "sem_desfecho"
    held = True
    for event in events:
        position = by_ts.get(event.timestamp)
        if position is None or position <= index or position > index + window:
            continue
        if event.provisional:
            continue
        if event.event is StructureEvent.CHOCH_FAILED and event.direction is direction:
            verdict = "choch_failed"
            held = position - index > QUICK_FLIP_BACK
            break
        if event.event is StructureEvent.BREAK_OF_STRUCTURE:
            if event.direction is direction:
                verdict = "confirmado"
            else:
                verdict = "falso_flip"
                held = position - index > QUICK_FLIP_BACK
            break
    return verdict, held


def measure_choch(
    event: MarketStructure,
    index: int,
    run,
    snapshots: Sequence[StateSnapshot],
    symbol: str,
    timeframe: TimeFrame,
    window: int,
    persistence: int,
) -> ChochCase | None:
    """Tudo o que as secoes 4-11 pedem para um CHoCH."""
    candles = run.candles
    by_ts = {candle.timestamp: position for position, candle in enumerate(candles)}
    atr = frozen_atr_pct(candles, index)
    if atr <= 0 or event.price_level <= 0:
        return None
    unit = event.price_level * atr
    bullish = event.direction is MarketDirection.BULLISH

    snapshot = snapshot_before(snapshots, event.timestamp)
    source, source_offset = classify_reference(snapshots, event)

    reference = event.reference_price_level
    ref_index = by_ts.get(event.reference_timestamp) if event.reference_timestamp else None
    # O extremo de onde o movimento partiu: topo antes de uma reversao bearish,
    # fundo antes de uma bullish.
    start_index, start_price = leg_extreme_before(
        candles, index, bullish_move=bullish, lookback=MOVE_LOOKBACK
    )
    close = candles[index].close
    lag_bars = index - start_index
    lag_atr = abs(close - start_price) / unit

    # Quanto do movimento ja tinha acontecido: do extremo de partida ate o
    # extremo alcancado na janela de desfecho.
    tail = candles[start_index : min(len(candles), index + OUTCOME_WINDOW)]
    if bullish:
        far = max(candle.high for candle in tail)
    else:
        far = min(candle.low for candle in tail)
    total = abs(far - start_price)
    done = abs(close - start_price) / total * 100 if total > 0 else None

    pivots_since = 0
    if event.reference_timestamp is not None:
        pivots_since = sum(
            1
            for other in run.events
            if not other.provisional
            and other.event in (StructureEvent.LOWER_HIGH, StructureEvent.HIGHER_LOW)
            and event.reference_timestamp < other.timestamp < event.timestamp
        )

    case = ChochCase(
        symbol=symbol,
        timeframe=timeframe.value,
        window=window,
        timestamp=event.timestamp.isoformat(),
        index=index,
        direction=event.direction.value,
        price_level=event.price_level,
        reference_price=reference,
        reference_timestamp=(
            event.reference_timestamp.isoformat() if event.reference_timestamp else None
        ),
        reference_structural=event.reference_structural,
        reference_source=source,
        reference_source_offset=source_offset,
        reference_age_bars=None if ref_index is None else index - ref_index,
        reference_to_extreme_atr=(
            None if reference is None else abs(start_price - reference) / unit
        ),
        reference_to_extreme_pct=(
            None if reference is None else abs(start_price - reference) / start_price * 100
        ),
        reference_distance_atr=None if reference is None else abs(close - reference) / unit,
        pivots_since_reference=pivots_since,
        move_start_index=start_index,
        choch_lag_bars=lag_bars,
        choch_lag_atr=round(lag_atr, 3),
        move_completed_at_choch_pct=None if done is None else round(done, 1),
        frozen_atr=round(atr, 6),
    )

    # --- as alternativas que ja existiam (secoes 3, 10, 11) ---
    candidates: list[tuple[str, float, datetime]] = []
    pullbacks = confirmed_pullbacks(
        run.events, event.timestamp, bullish, candles, swing_lookback_of(timeframe)
    )
    if pullbacks:
        last = pullbacks[-1]
        candidates.append(("last_confirmed_pullback", last.price_level, last.timestamp))
    if snapshot is not None:
        side = "high" if bullish else "low"
        for key, name in (
            (f"candidate_choch_{side}", "candidate"),
            (f"candidate_choch_{side}_baseline", "candidate_baseline"),
        ):
            pivot = _pivot_of(snapshot.state.get(key))
            if pivot:
                candidates.append((name, *pivot))

    for name, price, timestamp in candidates:
        if price <= 0:
            continue
        pivot_index = by_ts.get(timestamp)
        # Uma alternativa so vale se for MAIS PROXIMA na direcao da quebra --
        # uma referencia mais distante nunca antecipa nada.
        alternative = Alternative(
            source=name,
            price=price,
            timestamp=timestamp.isoformat(),
            age_bars=0 if pivot_index is None else index - pivot_index,
            distance_atr=round(abs(close - price) / unit, 3),
        )
        # A busca comeca depois da CONFIRMACAO do pivo, nao da sua data.
        search_from = (
            pivot_index + 1 + (swing_lookback_of(timeframe) if name.endswith("pullback") else 0)
            if pivot_index is not None
            else 0
        )
        # Ate o proprio candle do CHoCH, inclusive: se a alternativa so
        # quebra ali, o lead e zero -- e isso e a resposta "nao havia nada
        # antes", nao um dado faltando.
        break_index = first_close_beyond(
            candles, search_from, index + 1, price, bullish, persistence
        )
        if break_index is not None:
            alternative.break_index = break_index
            alternative.break_timestamp = candles[break_index].timestamp.isoformat()
            alternative.lead_bars = index - break_index
            alternative.lead_atr = round(
                abs(close - candles[break_index].close) / unit, 3
            )
            verdict, held = outcome_after(
                run.events, by_ts, break_index, event.direction
            )
            alternative.outcome = verdict
            alternative.held_quick = held
        case.alternatives.append(asdict(alternative))
    return case


# --------------------------------------------------------------------------
# 14. o custo INCONDICIONAL da alternativa
# --------------------------------------------------------------------------

#: Quanto tempo depois de uma quebra alternativa um CHoCH real ainda conta
#: como "a maquina chegou la sozinha".
COINCIDE_WINDOW = 20


def unconditional_pullback_breaks(
    run, timeframe: TimeFrame, persistence: int
) -> dict:
    """Toda quebra do ultimo pullback confirmado, e nao so as que viraram CHoCH.

    Esta e a medida que falta em toda a analise por evento, e ela e a que
    decide a etapa. Medir alternativas SO nos candles onde um CHoCH real
    aconteceu condiciona a amostra no desfecho: mede o adiantamento e nao mede
    o unico custo que importa -- as quebras que fariam a maquina virar a
    tendencia onde hoje ela (corretamente) nao vira.

    Aqui a varredura e sobre a janela inteira: para cada vela, qual era o
    ultimo pullback confirmado do lado contrario a tendencia vigente, e o
    preco fechou alem dele com a persistencia da producao? Cada quebra e
    classificada em:

    - `coincide`  -- havia um CHoCH real da mesma direcao ate `COINCIDE_WINDOW`
                     velas depois: a maquina chegou la de qualquer forma;
    - `extra`     -- nao havia. E um flip que so existiria sob a alternativa.

    As `extra` sao entao julgadas pelo desfecho real: se a estrutura seguiu na
    direcao nova, o flip extra teria acertado; se voltou, era correcao normal.
    """
    candles = run.candles
    by_ts = {candle.timestamp: i for i, candle in enumerate(candles)}
    lookback = swing_lookback_of(timeframe)

    # Tendencia vigente por vela, com a semantica do `_advance_boundaries`.
    trend_by_index: list[MarketDirection | None] = [None] * len(candles)
    current: MarketDirection | None = None
    advances = sorted(
        (
            (by_ts[event.timestamp], event)
            for event in run.events
            if not event.provisional
            and event.event
            in (
                StructureEvent.BREAK_OF_STRUCTURE,
                StructureEvent.CHANGE_OF_CHARACTER,
                StructureEvent.CHOCH_FAILED,
            )
            and event.timestamp in by_ts
        ),
        key=lambda pair: pair[0],
    )
    position = 0
    for index in range(len(candles)):
        while position < len(advances) and advances[position][0] <= index:
            event = advances[position][1]
            if event.event is StructureEvent.CHOCH_FAILED:
                current = (
                    MarketDirection.BEARISH
                    if event.direction is MarketDirection.BULLISH
                    else MarketDirection.BULLISH
                )
            else:
                current = event.direction
            position += 1
        trend_by_index[index] = current

    real_choch = {
        MarketDirection.BULLISH: [],
        MarketDirection.BEARISH: [],
    }
    for event in run.events:
        if event.provisional or event.event is not StructureEvent.CHANGE_OF_CHARACTER:
            continue
        index = by_ts.get(event.timestamp)
        if index is not None:
            real_choch[event.direction].append(index)

    counts = {"coincide": 0, "extra": 0}
    extra_outcomes: list[str] = []
    # Um mesmo nivel nao pode contar duas vezes: so a PRIMEIRA quebra de cada
    # pullback conta, como no detector (o nivel e consumido).
    consumed: set[tuple[float, int]] = set()

    for index in range(len(candles) - persistence):
        trend = trend_by_index[index]
        if trend is None:
            continue
        # Uma reversao rompe o lado contrario a tendencia vigente.
        reversal = (
            MarketDirection.BEARISH
            if trend is MarketDirection.BULLISH
            else MarketDirection.BULLISH
        )
        bullish_break = reversal is MarketDirection.BULLISH
        pullbacks = confirmed_pullbacks(
            run.events, candles[index].timestamp, bullish_break, candles, lookback
        )
        if not pullbacks:
            continue
        level = pullbacks[-1].price_level
        key = (round(level, 8), 1 if bullish_break else 0)
        if key in consumed:
            continue
        window = candles[index : index + persistence + 1]
        if bullish_break:
            broke = all(candle.close > level for candle in window)
        else:
            broke = all(candle.close < level for candle in window)
        if not broke:
            continue
        consumed.add(key)
        near = any(
            index <= real <= index + COINCIDE_WINDOW for real in real_choch[reversal]
        )
        if near:
            counts["coincide"] += 1
        else:
            counts["extra"] += 1
            verdict, _held = outcome_after(run.events, by_ts, index, reversal)
            extra_outcomes.append(verdict)
    return {
        "coincide": counts["coincide"],
        "extra": counts["extra"],
        "extra_outcomes": extra_outcomes,
    }


# --------------------------------------------------------------------------
# coleta
# --------------------------------------------------------------------------


def persistence_of(timeframe: TimeFrame) -> int:
    """O `persistence_candles` que a producao usa naquele timeframe."""
    return dd._INTERNAL_STRUCTURE_PARAMS[timeframe][1]


def collect(
    symbols: Sequence[str],
    timeframes: Sequence[TimeFrame] = TIMEFRAMES,
    windows: int = WINDOWS,
    limit: int = LIMIT,
    unconditional: dict | None = None,
) -> list[ChochCase]:
    """Todo CHoCH nao-provisional do painel, com o estado interno junto."""
    assert_trace_line()
    cases: list[ChochCase] = []
    for symbol in symbols:
        for timeframe in timeframes:
            if not (CACHE_DIR / f"{symbol}_{timeframe.value}.json").exists():
                continue
            try:
                series = load_series(symbol, timeframe)
            except Exception as error:  # noqa: BLE001 - vela corrompida no cache
                print(f"! cache invalido: {symbol} {timeframe.value}: {error}")
                continue
            persistence = persistence_of(timeframe)
            for window in range(windows):
                end = len(series) - window * limit
                if end - limit - BUFFER < 0:
                    break
                try:
                    run, snapshots = traced_run(symbol, timeframe, series, end, limit)
                except Exception as error:  # noqa: BLE001 - feed morto
                    print(f"! janela invalida: {symbol} {timeframe.value} w{window}: {error}")
                    continue
                by_ts = {candle.timestamp: i for i, candle in enumerate(run.candles)}
                if unconditional is not None:
                    scan = unconditional_pullback_breaks(run, timeframe, persistence)
                    slot = unconditional.setdefault(
                        timeframe.value,
                        {"coincide": 0, "extra": 0, "extra_outcomes": []},
                    )
                    slot["coincide"] += scan["coincide"]
                    slot["extra"] += scan["extra"]
                    slot["extra_outcomes"].extend(scan["extra_outcomes"])
                for event in run.events:
                    if event.provisional or event.event is not StructureEvent.CHANGE_OF_CHARACTER:
                        continue
                    index = by_ts.get(event.timestamp)
                    if index is None or index < 2:
                        continue
                    case = measure_choch(
                        event, index, run, snapshots, symbol, timeframe, window, persistence
                    )
                    if case is not None:
                        cases.append(case)
    return cases


# --------------------------------------------------------------------------
# estatistica
# --------------------------------------------------------------------------


def spearman(pairs: Sequence[tuple[float, float]]) -> float | None:
    """Correlacao de postos -- monotonica, sem supor linearidade nem escala."""
    if len(pairs) < 10:
        return None

    def ranks(values: Sequence[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        position = 0
        while position < len(order):
            stop = position
            while stop + 1 < len(order) and values[order[stop + 1]] == values[order[position]]:
                stop += 1
            mean = (position + stop) / 2 + 1
            for slot in range(position, stop + 1):
                out[order[slot]] = mean
            position = stop + 1
        return out

    xs = ranks([pair[0] for pair in pairs])
    ys = ranks([pair[1] for pair in pairs])
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=False))
    den_x = sum((x - mean_x) ** 2 for x in xs) ** 0.5
    den_y = sum((y - mean_y) ** 2 for y in ys) ** 0.5
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def quantile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    position = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[position]


def median_of(values: Sequence[float | None]) -> float | None:
    clean = [value for value in values if value is not None]
    return statistics.median(clean) if clean else None


def alternative_of(case: ChochCase, source: str) -> dict | None:
    for alternative in case.alternatives:
        if alternative["source"] == source:
            return alternative
    return None


def alternative_summary(cases: Sequence[ChochCase], source: str) -> dict:
    """Secao 14: lead ganho E o preco pago em falsos flips."""
    rows = [alt for case in cases if (alt := alternative_of(case, source))]
    broke = [row for row in rows if row["break_index"] is not None]
    earlier = [row for row in broke if (row["lead_bars"] or 0) > 0]
    outcomes = [row["outcome"] for row in earlier]
    total = len(earlier)
    summary = {
        "n_casos": len(cases),
        "n_com_referencia": len(rows),
        "n_quebrou": len(broke),
        "n_antecipou": total,
        "lead_bars_mediano": median_of([row["lead_bars"] for row in earlier]),
        "lead_atr_mediano": median_of([row["lead_atr"] for row in earlier]),
        "distancia_atr_mediana": median_of([row["distance_atr"] for row in rows]),
    }
    for verdict in ("confirmado", "falso_flip", "choch_failed", "sem_desfecho"):
        summary[verdict] = (
            round(outcomes.count(verdict) / total * 100, 1) if total else 0.0
        )
    quick = [row["held_quick"] for row in earlier if row["held_quick"] is not None]
    summary["retorno_rapido_pct"] = (
        round(sum(1 for held in quick if not held) / len(quick) * 100, 1) if quick else 0.0
    )
    return summary


def split_holdout(cases: Sequence[ChochCase]) -> tuple[list[ChochCase], list[ChochCase]]:
    """70/30 por tempo, DENTRO de cada timeframe (a correcao da Etapa 4.2)."""
    discovery: list[ChochCase] = []
    holdout: list[ChochCase] = []
    groups: dict[str, list[ChochCase]] = {}
    for case in cases:
        groups.setdefault(case.timeframe, []).append(case)
    for group in groups.values():
        group.sort(key=lambda case: case.timestamp)
        cut = int(len(group) * DISCOVERY_FRACTION)
        discovery.extend(group[:cut])
        holdout.extend(group[cut:])
    return discovery, holdout


# --------------------------------------------------------------------------
# 13. ZEC D1 -- as duas timelines obrigatorias
# --------------------------------------------------------------------------


def zec_timeline(case_key: tuple[str, TimeFrame, str], span: int = 60) -> dict:
    """A cadeia de referencia, vela a vela, em volta de um CHoCH do ZEC.

    Reproduz a janela de producao e anda pelos retratos do estado interno,
    reportando quando cada slot nasceu, mudou ou ficou congelado.
    """
    symbol, timeframe, timestamp = case_key
    target = datetime.fromisoformat(timestamp)
    series = load_series(symbol, timeframe)
    run, snapshots = traced_run(symbol, timeframe, series, len(series))
    by_ts = {candle.timestamp: i for i, candle in enumerate(run.candles)}
    if target not in by_ts:
        return {"erro": f"{timestamp} fora da janela"}
    index = by_ts[target]
    event = next(
        (
            item
            for item in run.events
            if item.timestamp == target
            and item.event is StructureEvent.CHANGE_OF_CHARACTER
            and not item.provisional
        ),
        None,
    )
    if event is None:
        return {"erro": f"nenhum CHoCH nao-provisional em {timestamp}"}

    persistence = persistence_of(timeframe)
    case = measure_choch(
        event, index, run, snapshots, symbol, timeframe, 0, persistence
    )
    bullish = event.direction is MarketDirection.BULLISH

    # A evolucao dos slots nos `span` pivos anteriores ao CHoCH.
    rows = []
    previous: tuple | None = None
    for snapshot in snapshots:
        if snapshot.timestamp > target:
            break
        position = by_ts.get(snapshot.timestamp)
        if position is None or position < index - span:
            continue
        slots = reference_slots(snapshot.state, bullish)
        effective = slots[0] if slots else None
        close = run.candles[position].close
        unit = event.price_level * (case.frozen_atr if case else 0.0)
        signature = tuple((name, round(price, 6)) for name, price, _ in slots)
        if signature == previous:
            continue
        previous = signature
        rows.append(
            {
                "timestamp": snapshot.timestamp.isoformat(),
                "bars_to_choch": index - position,
                "trend": getattr(snapshot.state.get("trend"), "value", None),
                "efetiva_source": effective[0] if effective else None,
                "efetiva_price": effective[1] if effective else None,
                "efetiva_nasceu": effective[2].isoformat() if effective else None,
                "efetiva_distancia_atr": (
                    round(abs(close - effective[1]) / unit, 2)
                    if effective and unit > 0
                    else None
                ),
                "slots": [
                    {"source": name, "price": price, "nasceu": stamp.isoformat()}
                    for name, price, stamp in slots
                ],
            }
        )
    return {"case": asdict(case) if case else None, "evolucao": rows}


# --------------------------------------------------------------------------
# relatorio
# --------------------------------------------------------------------------


def num(value, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def build(symbols: Sequence[str], windows: int) -> dict:
    unconditional: dict = {}
    cases = collect(symbols, windows=windows, unconditional=unconditional)
    print(f"simbolos: {len(symbols)}  CHoCH nao-provisionais: {len(cases)}")
    report: dict = {"symbols": list(symbols), "n_choch": len(cases)}

    # -- qualidade da leitura do estado interno --
    sources = {}
    for case in cases:
        sources[case.reference_source] = sources.get(case.reference_source, 0) + 1
    print("\n== LEITURA DO ESTADO INTERNO (casamento por identidade) ==")
    for name, count in sorted(sources.items(), key=lambda item: -item[1]):
        print(f"  {name:<22} {count:>5}  {count / len(cases) * 100:>5.1f}%")
    report["reference_sources"] = sources
    offsets: dict[int, int] = {}
    for case in cases:
        offsets[case.reference_source_offset] = offsets.get(case.reference_source_offset, 0) + 1
    print(f"  deslocamento de retrato usado: {dict(sorted(offsets.items()))}")
    report["classify_offsets"] = {str(k): v for k, v in sorted(offsets.items())}

    # -- 1/2/18: correlacoes --
    print("\n== 1/2. DISTANCIA E IDADE DA REFERENCIA vs ATRASO (Spearman) ==")
    correlations = {}
    for name, getter in (
        ("reference_to_extreme_atr", lambda c: c.reference_to_extreme_atr),
        ("reference_distance_atr", lambda c: c.reference_distance_atr),
        ("reference_age_bars", lambda c: c.reference_age_bars),
        ("pivots_since_reference", lambda c: float(c.pivots_since_reference)),
    ):
        for target_name, target in (
            ("choch_lag_bars", lambda c: c.choch_lag_bars),
            ("move_completed_pct", lambda c: c.move_completed_at_choch_pct),
        ):
            pairs = [
                (getter(case), target(case))
                for case in cases
                if getter(case) is not None and target(case) is not None
            ]
            rho = spearman(pairs)
            correlations[f"{name}~{target_name}"] = None if rho is None else round(rho, 3)
            print(f"  {name:>26} ~ {target_name:<20} rho={num(rho, 3):>7}  n={len(pairs)}")
    report["correlations"] = correlations

    # -- 8: grupos por quantil de atraso --
    lags = [case.choch_lag_bars for case in cases if case.choch_lag_bars is not None]
    low_cut, high_cut = quantile(lags, LOW_LAG_Q), quantile(lags, HIGH_LAG_Q)
    groups = {"LOW LAG": [], "MIDDLE": [], "HIGH LAG": []}
    for case in cases:
        if case.choch_lag_bars is None:
            continue
        key = (
            "LOW LAG"
            if case.choch_lag_bars <= low_cut
            else "HIGH LAG"
            if case.choch_lag_bars >= high_cut
            else "MIDDLE"
        )
        groups[key].append(case)
    print(f"\n== 8. GRUPOS POR ATRASO (cortes {low_cut:.0f} / {high_cut:.0f} velas) ==")
    header = (
        f"{'grupo':<9} {'n':>5} {'lag':>6} {'mov%':>6} {'ref->extremo ATR':>17} "
        f"{'ref idade':>10} {'pivos':>6} {'weak%':>6}"
    )
    print(header)
    group_report = {}
    for name, group in groups.items():
        if not group:
            continue
        weak = sum(1 for case in group if case.reference_structural is False)
        row = {
            "n": len(group),
            "lag_bars_mediano": median_of([c.choch_lag_bars for c in group]),
            "move_completed_pct_mediano": median_of(
                [c.move_completed_at_choch_pct for c in group]
            ),
            "ref_to_extreme_atr_mediano": median_of(
                [c.reference_to_extreme_atr for c in group]
            ),
            "ref_age_bars_mediano": median_of([c.reference_age_bars for c in group]),
            "pivots_since_ref_mediano": median_of(
                [float(c.pivots_since_reference) for c in group]
            ),
            "weak_ref_pct": round(weak / len(group) * 100, 1),
        }
        group_report[name] = row
        print(
            f"{name:<9} {row['n']:>5} {num(row['lag_bars_mediano'], 0):>6} "
            f"{num(row['move_completed_pct_mediano'], 0):>6} "
            f"{num(row['ref_to_extreme_atr_mediano']):>17} "
            f"{num(row['ref_age_bars_mediano'], 0):>10} "
            f"{num(row['pivots_since_ref_mediano'], 0):>6} {row['weak_ref_pct']:>5.1f}%"
        )
    report["lag_groups"] = group_report

    # -- 9: atraso por source --
    print("\n== 9. ATRASO POR SOURCE DA REFERENCIA ==")
    print(f"{'source':<22} {'n':>5} {'lag':>6} {'mov%':>6} {'ref->extremo ATR':>17} {'idade':>7}")
    by_source = {}
    for name in sources:
        group = [case for case in cases if case.reference_source == name]
        row = {
            "n": len(group),
            "lag_bars_mediano": median_of([c.choch_lag_bars for c in group]),
            "move_completed_pct_mediano": median_of(
                [c.move_completed_at_choch_pct for c in group]
            ),
            "ref_to_extreme_atr_mediano": median_of(
                [c.reference_to_extreme_atr for c in group]
            ),
            "ref_age_bars_mediano": median_of([c.reference_age_bars for c in group]),
        }
        by_source[name] = row
        print(
            f"{name:<22} {row['n']:>5} {num(row['lag_bars_mediano'], 0):>6} "
            f"{num(row['move_completed_pct_mediano'], 0):>6} "
            f"{num(row['ref_to_extreme_atr_mediano']):>17} "
            f"{num(row['ref_age_bars_mediano'], 0):>7}"
        )
    report["by_source"] = by_source

    # -- 10/11/14: as alternativas, com o preco --
    print("\n== 10/11/14. ALTERNATIVAS: LEAD GANHO x FALSOS FLIPS ==")
    alternatives = {}
    for source in ("last_confirmed_pullback", "candidate", "candidate_baseline"):
        summary = alternative_summary(cases, source)
        alternatives[source] = summary
        if not summary["n_antecipou"]:
            print(f"  {source:<24} nenhuma antecipacao (n={summary['n_com_referencia']})")
            continue
        print(
            f"  {source:<24} n={summary['n_antecipou']:>4} de {summary['n_com_referencia']:>4} "
            f"| lead {num(summary['lead_bars_mediano'], 0)} velas / "
            f"{num(summary['lead_atr_mediano'])} ATR | "
            f"confirmado {summary['confirmado']:>4.1f}% | "
            f"FALSO FLIP {summary['falso_flip']:>4.1f}% | "
            f"✕ {summary['choch_failed']:>4.1f}% | "
            f"retorno rapido {summary['retorno_rapido_pct']:>4.1f}%"
        )
    report["alternatives"] = alternatives

    # -- 14: o custo incondicional --
    print("\n== 14. QUEBRAS DO ULTIMO PULLBACK EM TODA A JANELA (nao so nos CHoCH) ==")
    total_c = total_e = 0
    all_outcomes: list[str] = []
    print(f"{'tf':>5} {'coincide':>9} {'extra':>7} {'extra/total':>12} {'extra confirmado':>17}")
    uncond_report = {}
    for timeframe, slot in unconditional.items():
        total = slot["coincide"] + slot["extra"]
        outcomes = slot["extra_outcomes"]
        confirmed = (
            outcomes.count("confirmado") / len(outcomes) * 100 if outcomes else 0.0
        )
        uncond_report[timeframe] = {
            "coincide": slot["coincide"],
            "extra": slot["extra"],
            "extra_pct": round(slot["extra"] / total * 100, 1) if total else 0.0,
            "extra_confirmado_pct": round(confirmed, 1),
            "extra_falso_flip_pct": round(
                outcomes.count("falso_flip") / len(outcomes) * 100, 1
            )
            if outcomes
            else 0.0,
        }
        row = uncond_report[timeframe]
        print(
            f"{timeframe:>5} {slot['coincide']:>9} {slot['extra']:>7} "
            f"{row['extra_pct']:>11.1f}% {row['extra_confirmado_pct']:>16.1f}%"
        )
        total_c += slot["coincide"]
        total_e += slot["extra"]
        all_outcomes.extend(outcomes)
    total = total_c + total_e
    extra_share = total_e / total * 100 if total else 0.0
    extra_ok = (
        all_outcomes.count("confirmado") / len(all_outcomes) * 100 if all_outcomes else 0.0
    )
    print(
        f"TOTAL {total_c:>9} {total_e:>7} {extra_share:>11.1f}% {extra_ok:>16.1f}%"
    )
    report["unconditional_total"] = {
        "coincide": total_c,
        "extra": total_e,
        "extra_pct": round(extra_share, 1),
        "extra_confirmado_pct": round(extra_ok, 1),
        "extra_falso_flip_pct": round(
            all_outcomes.count("falso_flip") / len(all_outcomes) * 100, 1
        )
        if all_outcomes
        else 0.0,
    }
    report["unconditional_breaks"] = uncond_report

    # -- 15: holdout --
    discovery, holdout = split_holdout(cases)
    print(f"\n== 15. HOLDOUT (70/30 por timeframe): {len(discovery)} / {len(holdout)} ==")
    split_report = {}
    for source in ("last_confirmed_pullback", "candidate"):
        d = alternative_summary(discovery, source)
        h = alternative_summary(holdout, source)
        split_report[source] = {"discovery": d, "holdout": h}
        print(
            f"  {source:<24} discovery: falso_flip {d['falso_flip']:>4.1f}% "
            f"(n={d['n_antecipou']}) | holdout: falso_flip {h['falso_flip']:>4.1f}% "
            f"(n={h['n_antecipou']})"
        )
    report["holdout"] = split_report

    # -- por timeframe --
    print("\n== POR TIMEFRAME ==")
    per_tf = {}
    for timeframe in [t.value for t in TIMEFRAMES]:
        group = [case for case in cases if case.timeframe == timeframe]
        if not group:
            continue
        summary = alternative_summary(group, "last_confirmed_pullback")
        per_tf[timeframe] = {
            "n": len(group),
            "lag_bars_mediano": median_of([c.choch_lag_bars for c in group]),
            "move_completed_pct_mediano": median_of(
                [c.move_completed_at_choch_pct for c in group]
            ),
            "ref_to_extreme_atr_mediano": median_of(
                [c.reference_to_extreme_atr for c in group]
            ),
            "pullback_falso_flip_pct": summary["falso_flip"],
            "pullback_lead_bars": summary["lead_bars_mediano"],
        }
        row = per_tf[timeframe]
        print(
            f"  {timeframe:>4} n={row['n']:>5} lag={num(row['lag_bars_mediano'], 0):>5} "
            f"mov%={num(row['move_completed_pct_mediano'], 0):>5} "
            f"ref->extremo={num(row['ref_to_extreme_atr_mediano']):>6} ATR | "
            f"pullback: lead {num(row['pullback_lead_bars'], 0):>4} velas, "
            f"falso flip {row['pullback_falso_flip_pct']:>4.1f}%"
        )
    report["by_timeframe"] = per_tf
    return report


def print_zec(name: str, timeline: dict) -> None:
    print(f"\n== 13. ZEC D1 -- {name} ==")
    if "erro" in timeline:
        print(f"  {timeline['erro']}")
        return
    case = timeline["case"]
    print(
        f"  referencia oficial: {num(case['reference_price'])} "
        f"({case['reference_source']}, structural={case['reference_structural']}) "
        f"nascida em {str(case['reference_timestamp'])[:10]}, "
        f"idade {case['reference_age_bars']} velas"
    )
    print(
        f"  distancia ao extremo do movimento: "
        f"{num(case['reference_to_extreme_atr'])} ATR / "
        f"{num(case['reference_to_extreme_pct'])}%"
    )
    print(
        f"  atraso: {case['choch_lag_bars']} velas / {num(case['choch_lag_atr'])} ATR | "
        f"movimento ja feito: {num(case['move_completed_at_choch_pct'], 1)}%"
    )
    print(f"  pivos confirmados desde a referencia: {case['pivots_since_reference']}")
    for alternative in case["alternatives"]:
        print(
            f"    alternativa {alternative['source']:<24} "
            f"{num(alternative['price'])} de {str(alternative['timestamp'])[:10]} | "
            f"quebra {str(alternative['break_timestamp'])[:10]} | "
            f"lead {alternative['lead_bars']} velas | desfecho {alternative['outcome']}"
        )
    print("  evolucao da referencia efetiva:")
    for row in timeline["evolucao"]:
        print(
            f"    {row['timestamp'][:10]} (-{row['bars_to_choch']:>3} velas) "
            f"trend={str(row['trend']):<8} {str(row['efetiva_source']):<18} "
            f"{num(row['efetiva_price']):>10} nascida {str(row['efetiva_nasceu'])[:10]} "
            f"dist={num(row['efetiva_distancia_atr'])} ATR"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--expanded", action="store_true")
    parser.add_argument("--windows", type=int, default=WINDOWS)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.symbols:
        symbols = args.symbols
    elif args.expanded:
        from research.choch_leg_opener import expanded_symbols

        symbols = expanded_symbols()
    else:
        symbols = SYMBOLS

    report = build(symbols, args.windows)
    zec_cases = (
        ("CHoCH bearish 2026-06-04", ZEC_BEARISH),
        ("CHoCH bullish 2026-08-19", ZEC_BULLISH),
    )
    for name, key in zec_cases:
        timeline = zec_timeline(key)
        report[f"zec_{key[2][:10]}"] = timeline
        print_zec(name, timeline)

    if args.json:
        args.json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\n-> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
