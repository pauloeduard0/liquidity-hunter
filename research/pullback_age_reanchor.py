"""Etapa 4.6: o staleness deveria contar velas desde o ultimo PULLBACK?

As Etapas 4.1-4.5 fecharam cinco hipoteses sobre o atraso do CHoCH bearish do
ZEC D1. A 4.5 terminou num ponto especifico: o unico mecanismo que ataca a
"perna sem pullback" e o stale re-anchor, e o contador dele e

    current_index - last_advance_index                (linha 2004 do detector)

`last_advance_index` e atualizado em TODO advance -- todo BOS, todo flip. Numa
expansao forte (BOS -> BOS -> BOS) o contador nunca chega ao limiar, mesmo que
nenhum pullback estrutural novo tenha aparecido entre eles. A hipotese desta
etapa e que o que envelhece uma referencia de reversao nao e a ausencia de
BOS, e sim a ausencia de um pullback confirmado novo.

Secao 2 -- o que e "o ultimo pullback confirmado", no codigo
============================================================

O laco de decisao do detector (linha 1917) itera **pivos de swing**, nao
velas:

    for timestamp, kind, price in pivots:
        current_index = index_by_timestamp[timestamp]

`pivots` vem de `collect_pivots(candles, high_detector, low_detector)` -- ja
sao pivos confirmados pelo `swing_lookback`, mas **datados na vela que os
formou**. `last_advance_index` segue a mesma convencao (o indice da vela do
advance), entao os dois contadores comparados aqui vivem no mesmo espaco de
indices. Nao ha lookahead novo: e a convencao que o proprio detector usa.

Do lado que interessa para uma reversao:

- tendencia BULLISH -> o CHoCH rompe para BAIXO -> o pullback e um pivo de
  `low` (que o detector emite como `higher_low` quando ele sobe);
- tendencia BEARISH -> o pullback e um pivo de `high` (`lower_high`).

O contador contrafactual usa o pivo bruto daquele lado, e nao o evento
`higher_low`/`lower_high` emitido, por um motivo causal: no ponto do codigo
onde o staleness e avaliado (linha 1968) o pivo corrente **ainda nao foi
classificado**. O pivo bruto e o objeto que a maquina ja tem na mao naquele
candle. A secao 3 mede a diferenca entre os dois conjuntos, para nao esconder
essa escolha.

Como o contrafactual da secao 8 e construido sem tocar em producao
==================================================================

`self._stale_reanchor_candles` e lido em **um unico ponto** (linha 1968) e
escrito num unico ponto (`__init__`, linha 995). Entao trocar

    current_index - last_advance_index  >= base
por
    current_index - last_pullback_index >= base

e algebricamente identico a manter o teste de producao e usar um limiar
efetivo

    base_efetivo = base - (last_advance_index - last_pullback_index)

Um subtipo de pesquisa (`PullbackAgeDetector`) transforma esse atributo numa
`property` que le `current_index`, `last_advance_index`, `trend` e `pivots`
**do frame do proprio `detect`** e devolve `base_efetivo`. Producao nao muda:
o subtipo so e instalado trocando `__class__` da instancia que
`_build_internal_detector` acabou de construir, dentro de um `contextmanager`
que restaura tudo no fim.

AVISO HONESTO SOBRE O ACOPLAMENTO
---------------------------------

`stale_after` e usado em DOIS lugares: o gatilho e o inicio da janela de
selecao do nivel (`window_start = current_index - stale_after + 1`). Nao ha
como mudar so o gatilho sem tocar no arquivo de producao. Entao a variante
`pullback-age` muda as duas coisas ao mesmo tempo -- exatamente o que a secao
8 pede para evitar. A defesa e um **controle de lead casado** (secao 9):
variantes que so adiantam o gatilho de forma uniforme (`base x0.5`, `x0.75`)
sofrem o mesmo encurtamento de janela. Se `pullback-age` cair na mesma curva
custo/beneficio que o adiantamento uniforme de mesmo lead, o contador novo
nao acrescenta nada, e a resposta e A/B. Isso e reportado explicitamente em
vez de ser apresentado como isolamento limpo.

    poetry run python -m research.pullback_age_reanchor
    poetry run python -m research.pullback_age_reanchor --expanded --json \
        research/pullback_age_reanchor_baseline.json
"""

from __future__ import annotations

import argparse
import bisect
import json
import statistics
import sys
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from types import FrameType
from typing import Any

from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.liquidity.detectors.internal_structure import InternalStructureDetector
from research.choch_leg_opener import (
    DISCOVERY_FRACTION,
    SYMBOLS,
    TIMEFRAMES,
    WINDOWS,
)
from research.choch_reference_audit import (
    LOOP_LINE,
    STATE_KEYS,
    assert_trace_line,
    quantile,
    spearman,
    swing_lookback_of,
)
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series
from research.stale_reanchor_audit import (
    MOVE_LOOKBACK,
    QUICK_BACK,
    ChochRow,
    Episode,
    _mean_tr_pct,
    choch_rows,
    episodes,
    variant_summary,
)
from research.zec_d1_structure_lag import leg_extreme_before

#: Os limiares que a secao 4 pede no ZEC.
ZEC_THRESHOLDS = (10, 20, 30, 40, 50, 60)

ZEC_BEARISH = ("ZECUSDT", TimeFrame.D1, "2026-06-04T00:00:00+00:00")

#: Locais extras do frame de `detect` -- alem de `STATE_KEYS`, que a 4.4 ja le.
LOOP_KEYS = ("current_index", "last_advance_index", "trend", "kind", "price")


# --------------------------------------------------------------------------
# 3. o contador contrafactual
# --------------------------------------------------------------------------


def pullback_kind(trend: MarketDirection) -> str:
    """O lado do pivo que serve de pullback numa tendencia (secao 2)."""
    return "low" if trend is MarketDirection.BULLISH else "high"


class PullbackAgeDetector(InternalStructureDetector):
    """`stale_reanchor_candles` reescrito como idade do ultimo pullback.

    Nao redefine `detect`: so troca o atributo que o gatilho de staleness le,
    por uma `property` que devolve o limiar EFETIVO -- aquele que faz o teste
    de producao (`current_index - last_advance_index >= limiar`) valer
    exatamente quando `current_index - last_pullback_index >= base`.
    """

    #: preenchido por `wired()`; a `property` abaixo depende dele.
    _stale_base: int | None = None
    #: `(id(events), quantos ja lidos, ultimo higher_low, ultimo lower_high)`.
    _pullback_scan: tuple[int, int, int | None, int | None] | None = None

    def _last_pullback(
        self,
        events: Sequence[Any],
        by_ts: dict[datetime, int],
        trend: MarketDirection,
    ) -> int | None:
        """Indice do ultimo `higher_low`/`lower_high` JA emitido.

        Nao usa o pivo bruto de swing: numa expansao forte quase toda vela tem
        um fundo local, e um contador sobre pivos brutos praticamente nunca
        envelhece -- na primeira rodada desta etapa ele zerava tanto que o
        contrafactual disparava MENOS que producao e o ZEC nao registrou uma
        unica tentativa de re-anchor. O objeto certo e o que a secao 2 pede: o
        pullback que a maquina ja CLASSIFICOU como estrutural.

        Causal por construcao: `events` so contem o que ja foi emitido, e o
        pivo corrente ainda nao foi classificado neste ponto do laco. A
        varredura e incremental (consome so os eventos novos), senao a medicao
        do painel inteiro ficaria quadratica.
        """
        token = id(events)
        cached = self._pullback_scan
        if cached is None or cached[0] != token:
            cached = (token, 0, None, None)
        _token, seen, higher_low, lower_high = cached
        for event in events[seen:]:
            position = by_ts.get(event.timestamp)
            if position is None:
                continue
            if event.event is StructureEvent.HIGHER_LOW:
                higher_low = position
            elif event.event is StructureEvent.LOWER_HIGH:
                lower_high = position
        self._pullback_scan = (token, len(events), higher_low, lower_high)
        return higher_low if trend is MarketDirection.BULLISH else lower_high

    @property  # type: ignore[override]
    def _stale_reanchor_candles(self) -> int | None:
        base = self._stale_base
        if base is None:
            return None
        local = sys._getframe(1).f_locals
        current = local.get("current_index")
        advance = local.get("last_advance_index")
        trend = local.get("trend")
        by_ts = local.get("index_by_timestamp")
        events = local.get("events")
        if current is None or advance is None or events is None or by_ts is None:
            # Leitura fora do laco de decisao (um `repr`, um teste): devolve o
            # valor de producao em vez de inventar um limiar.
            return base
        if trend not in (MarketDirection.BULLISH, MarketDirection.BEARISH):
            return base
        pullback = self._last_pullback(events, by_ts, trend)
        if pullback is None:
            return base
        return max(1, base - (advance - pullback))

    @_stale_reanchor_candles.setter
    def _stale_reanchor_candles(self, value: int | None) -> None:
        self._stale_base = value


@dataclass(frozen=True)
class Variant:
    """Uma configuracao medida contra producao."""

    label: str
    #: `True` = instala `PullbackAgeDetector` (semantica nova).
    pullback_age: bool = False
    #: Multiplicador sobre o `stale_reanchor_candles` daquele timeframe.
    scale: float = 1.0

    def base_for(self, timeframe: TimeFrame) -> int | None:
        base = dd._STALE_REANCHOR_CANDLES.get(
            timeframe, dd._DEFAULT_STALE_REANCHOR_CANDLES
        )
        return None if base is None else max(1, round(base * self.scale))


BASELINE = Variant("producao")
#: Secao 13: os limiares ATUAIS primeiro (x1.0), depois a grade pequena.
VARIANTS = [
    BASELINE,
    Variant("pullback x1.00", pullback_age=True, scale=1.00),
    Variant("pullback x0.50", pullback_age=True, scale=0.50),
    Variant("pullback x0.75", pullback_age=True, scale=0.75),
    Variant("pullback x1.25", pullback_age=True, scale=1.25),
    # controle de lead casado: adiantar o gatilho SEM olhar pullback nenhum,
    # sofrendo o mesmo encurtamento de janela (ver o aviso do docstring).
    Variant("advance x0.50", scale=0.50),
    Variant("advance x0.75", scale=0.75),
]


@contextmanager
def wired(variant: Variant, timeframe: TimeFrame):
    """`_build_internal_detector` sob `variant`, restaurado no fim.

    Producao continua construindo o detector: o wrapper so troca o
    `__class__` da instancia pronta (e move o valor do atributo para
    `_stale_base`, porque a `property` do subtipo e um descritor de dados e
    passa na frente do `__dict__`).
    """
    original = dd._build_internal_detector

    def build(tf: TimeFrame, *, confluence_filter: bool):
        detector = original(tf, confluence_filter=confluence_filter)
        current = detector.__dict__.pop("_stale_reanchor_candles", None)
        base = variant.base_for(tf) if current is not None else None
        if variant.pullback_age:
            detector.__class__ = PullbackAgeDetector
            detector._stale_base = base
            detector._pullback_scan = None
        else:
            detector._stale_reanchor_candles = base
        return detector

    dd._build_internal_detector = build
    try:
        yield
    finally:
        dd._build_internal_detector = original


# --------------------------------------------------------------------------
# a observacao do laco (secoes 2-5)
# --------------------------------------------------------------------------


@dataclass
class Observation:
    """Um pivo do laco de decisao, com os dois contadores e o estado."""

    timestamp: datetime
    index: int
    kind: str
    trend: str | None
    last_advance_index: int
    last_pullback_index: int | None
    state: dict[str, Any]

    @property
    def bars_since_advance(self) -> int | None:
        if self.last_advance_index < 0:
            return None
        return self.index - self.last_advance_index

    @property
    def bars_since_pullback(self) -> int | None:
        if self.last_pullback_index is None:
            return None
        return self.index - self.last_pullback_index


def observed_run(
    symbol: str, timeframe: TimeFrame, series: Sequence[Candle], end: int,
    variant: Variant = BASELINE, limit: int = LIMIT,
):
    """Uma janela sob `variant`, com um retrato por pivo E as tentativas de
    re-anchor.

    Mesmo mecanismo da Etapa 4.4 (`traced_run`), com `LOOP_KEYS` a mais e o
    tracer de `reanchor_opposite` da 4.5 no mesmo passe -- os dois juntos
    para nao pagar duas execucoes por combinacao. O `settrace` so responde
    aos dois code objects, e ha teste que compara o stream tracado com o
    nao-tracado evento a evento.
    """
    from research.stale_reanchor_audit import _REANCHOR_CODE, ReanchorAttempt

    if _REANCHOR_CODE is None:
        raise RuntimeError("reanchor_opposite nao encontrado -- a medicao pararia vazia")
    assert_trace_line()
    start = end - limit - BUFFER
    code = InternalStructureDetector.detect.__code__
    raw: list[dict[str, Any]] = []
    attempts: list[ReanchorAttempt] = []
    pending: list[dict] = []

    def line_tracer(frame: FrameType, event: str, arg: Any):
        if event == "line" and frame.f_lineno == LOOP_LINE:
            local = frame.f_locals
            snapshot = {key: local.get(key, "<ausente>") for key in STATE_KEYS}
            snapshot.update({key: local.get(key) for key in LOOP_KEYS})
            snapshot["timestamp"] = local.get("timestamp")
            raw.append(snapshot)
        return line_tracer

    def reanchor_tracer(frame: FrameType, event: str, arg: Any):
        if event == "return" and pending:
            call = pending.pop()
            attempts.append(
                ReanchorAttempt(
                    level=float(call["level"]),
                    timestamp=call["ts"].isoformat() if call["ts"] else "",
                    current_price=float(call["price"]),
                    moved=bool(arg),
                )
            )
        return None

    def call_tracer(frame: FrameType, event: str, arg: Any):
        if event != "call":
            return None
        if frame.f_code is code:
            frame.f_trace_lines = True
            return line_tracer
        if frame.f_code is _REANCHOR_CODE:
            local = frame.f_locals
            pending.append(
                {
                    "level": local.get("level"),
                    "ts": local.get("ts"),
                    "price": local.get("current_price"),
                }
            )
            return reanchor_tracer
        return None

    previous = sys.gettrace()
    sys.settrace(call_tracer)
    try:
        with wired(variant, timeframe):
            run = dd._run_internal_structure(
                provider=SliceProvider(list(series[max(start, 0) : end])),
                symbol=symbol,
                timeframe=timeframe,
                limit=limit,
                confluence_filter=True,
            )
    finally:
        sys.settrace(previous)

    by_ts = {candle.timestamp: i for i, candle in enumerate(run.candles)}
    # Os pullbacks ESTRUTURAIS, na mesma definicao que a `property` usa em
    # tempo de execucao (secao 2), para que o painel offline e o contrafactual
    # midam a mesma coisa.
    higher_lows = sorted(
        by_ts[event.timestamp]
        for event in run.events
        if event.event is StructureEvent.HIGHER_LOW and event.timestamp in by_ts
    )
    lower_highs = sorted(
        by_ts[event.timestamp]
        for event in run.events
        if event.event is StructureEvent.LOWER_HIGH and event.timestamp in by_ts
    )

    def previous(side: list[int], index: int) -> int | None:
        position = bisect.bisect_left(side, index) - 1
        return side[position] if position >= 0 else None

    observations: list[Observation] = []
    for snapshot in raw:
        timestamp = snapshot.pop("timestamp")
        index = by_ts.get(timestamp)
        if index is None:
            continue
        # `current_index` e `last_advance_index` indexam o array do detector,
        # que carrega o bootstrap buffer na frente; `by_ts` indexa `run.candles`.
        # Sem tirar esse deslocamento os dois contadores comparados aqui
        # viveriam em espacos diferentes -- na primeira rodada isso datou o
        # cruzamento de N=10 do ZEC em 2026-01-27 alegando 57 velas antes de
        # um CHoCH de 2026-06-04, quatro meses de diferenca.
        raw_index = snapshot.get("current_index")
        offset = (raw_index - index) if raw_index is not None else 0
        raw_advance = snapshot.get("last_advance_index")
        advance = -1 if raw_advance is None or raw_advance < 0 else raw_advance - offset
        trend = snapshot.get("trend")
        side_index: int | None = None
        if trend is MarketDirection.BULLISH:
            side_index = previous(higher_lows, index)
        elif trend is MarketDirection.BEARISH:
            side_index = previous(lower_highs, index)
        observations.append(
            Observation(
                timestamp=timestamp,
                index=index,
                kind=snapshot.get("kind") or "",
                trend=trend.value if isinstance(trend, MarketDirection) else None,
                last_advance_index=advance,
                last_pullback_index=side_index,
                state={key: snapshot.get(key) for key in STATE_KEYS},
            )
        )
    return run, observations, attempts


def run_with_attempts(
    symbol: str, timeframe: TimeFrame, series: Sequence[Candle], end: int, variant: Variant,
    limit: int = LIMIT,
):
    """`observed_run` sem os retratos -- as tentativas e seus vereditos.

    Secao 5 exige saber se os guards deixariam passar. Em vez de reimplementar
    `reanchor_opposite` (cinco guards, incluindo o release gap por ATR), o
    tracer observa a funcao real: `moved=False` E o proprio NO_EFFECT, dito
    pela maquina.
    """
    run, _observations, attempts = observed_run(
        symbol, timeframe, series, end, variant, limit
    )
    return run, attempts


# --------------------------------------------------------------------------
# 2. o registro do ultimo pullback vigente
# --------------------------------------------------------------------------


def pullback_roles(state: dict[str, Any], price: float, tolerance: float) -> list[str]:
    """Em quais slots da cadeia esse preco aparece (candidate/baseline/validated).

    Secao 2 pede saber se o pullback "virou candidate/baseline/validated". Nao
    ha id de pivo no detector, entao a identidade e por preco, com tolerancia
    relativa -- e por isso o resultado e reportado como conjunto de slots, nao
    como afirmacao de identidade.
    """
    out = []
    for key in STATE_KEYS:
        value = state.get(key)
        level = getattr(value, "price", None)
        if level is None:
            continue
        if abs(float(level) - price) <= tolerance:
            out.append(key)
    return out


@dataclass
class PullbackRecord:
    """O ultimo pullback confirmado vigente num candle (secao 2)."""

    pullback_timestamp: str
    confirmation_timestamp: str | None
    price: float
    kind: str
    #: `swing_pivot` sempre: e o unico objeto que o laco de decisao conhece ali.
    source: str
    #: Slots da cadeia que carregam esse nivel naquele candle.
    slots: list[str]
    emitted_structural: bool
    was_official_reference: bool


def pullback_record(
    observation: Observation,
    run,
    candles: Sequence[Candle],
    lookback: int,
    official_references: set[str],
) -> PullbackRecord | None:
    index = observation.last_pullback_index
    if index is None or index >= len(candles):
        return None
    timestamp = candles[index].timestamp
    kind = pullback_kind(MarketDirection(observation.trend)) if observation.trend else ""
    price = candles[index].high if kind == "high" else candles[index].low
    confirmed_at = index + lookback
    wanted = (
        StructureEvent.LOWER_HIGH if kind == "high" else StructureEvent.HIGHER_LOW
    )
    emitted = any(
        event.timestamp == timestamp and event.event is wanted and not event.provisional
        for event in run.events
    )
    tolerance = abs(price) * 1e-9 + 1e-12
    return PullbackRecord(
        pullback_timestamp=timestamp.isoformat(),
        confirmation_timestamp=(
            candles[confirmed_at].timestamp.isoformat()
            if confirmed_at < len(candles)
            else None
        ),
        price=float(price),
        kind=kind,
        source="swing_pivot",
        slots=pullback_roles(observation.state, float(price), tolerance),
        emitted_structural=emitted,
        was_official_reference=f"{price:.10g}" in official_references,
    )


def official_reference_prices(run) -> set[str]:
    """Os niveis que algum CHoCH nao-provisional usou como referencia."""
    out = set()
    for event in run.events:
        if event.provisional or event.event is not StructureEvent.CHANGE_OF_CHARACTER:
            continue
        level = event.reference_price_level
        if level is not None:
            out.add(f"{float(level):.10g}")
    return out


# --------------------------------------------------------------------------
# 6/7. o painel por CHoCH
# --------------------------------------------------------------------------


@dataclass
class LegRow:
    """Um CHoCH de producao com os cinco candidatos a explicar seu atraso."""

    symbol: str
    timeframe: str
    window: int
    timestamp: str
    index: int
    direction: str
    # A/B/C -- os contadores desta etapa
    bars_since_advance: int | None
    bars_since_pullback: int | None
    bos_since_pullback: int | None
    # D/E -- os da Etapa 4.4
    reference_age_bars: int | None
    pivots_since_reference: int | None
    reference_to_extreme_atr: float | None
    # o alvo
    choch_lag_bars: int | None

    @property
    def start_timestamp(self) -> str:
        return self.timestamp


def _observation_at(observations: Sequence[Observation], index: int) -> Observation | None:
    """O ultimo retrato em ou antes de `index` -- causal por construcao."""
    chosen = None
    for observation in observations:
        if observation.index <= index:
            chosen = observation
        else:
            break
    return chosen


def leg_rows(
    run, observations: Sequence[Observation], symbol: str, timeframe: TimeFrame, window: int
) -> list[LegRow]:
    candles = run.candles
    by_ts = {candle.timestamp: i for i, candle in enumerate(candles)}
    mean_tr = _mean_tr_pct(candles)
    advances = sorted(
        by_ts[event.timestamp]
        for event in run.events
        if not event.provisional
        and event.event is StructureEvent.BREAK_OF_STRUCTURE
        and event.timestamp in by_ts
    )
    pivot_indexes = sorted(observation.index for observation in observations)
    rows: list[LegRow] = []
    for event in run.events:
        if event.provisional or event.event is not StructureEvent.CHANGE_OF_CHARACTER:
            continue
        index = by_ts.get(event.timestamp)
        if index is None or index < 2:
            continue
        observation = _observation_at(observations, index)
        if observation is None:
            continue
        bullish = event.direction is MarketDirection.BULLISH
        start_index, start_price = leg_extreme_before(
            candles, index, bullish_move=bullish, lookback=MOVE_LOOKBACK
        )
        pullback_index = observation.last_pullback_index
        bos_since = (
            sum(1 for position in advances if pullback_index < position <= index)
            if pullback_index is not None
            else None
        )
        reference = event.reference_price_level
        reference_index = None
        if reference is not None:
            tolerance = abs(float(reference)) * 1e-9 + 1e-12
            for key in STATE_KEYS:
                value = observation.state.get(key)
                level = getattr(value, "price", None)
                stamp = getattr(value, "timestamp", None)
                if level is None or stamp is None:
                    continue
                if abs(float(level) - float(reference)) <= tolerance:
                    reference_index = by_ts.get(stamp)
                    break
        to_extreme = None
        if reference is not None and mean_tr > 0 and start_price:
            to_extreme = round(
                abs(float(reference) - start_price) / start_price / mean_tr, 2
            )
        rows.append(
            LegRow(
                symbol=symbol,
                timeframe=timeframe.value,
                window=window,
                timestamp=event.timestamp.isoformat(),
                index=index,
                direction=event.direction.value,
                bars_since_advance=(
                    index - observation.last_advance_index
                    if observation.last_advance_index >= 0
                    else None
                ),
                bars_since_pullback=(
                    index - pullback_index if pullback_index is not None else None
                ),
                bos_since_pullback=bos_since,
                reference_age_bars=(
                    index - reference_index if reference_index is not None else None
                ),
                pivots_since_reference=(
                    sum(
                        1
                        for position in pivot_indexes
                        if reference_index < position < index
                    )
                    if reference_index is not None
                    else None
                ),
                reference_to_extreme_atr=to_extreme,
                choch_lag_bars=index - start_index,
            )
        )
    return rows


# --------------------------------------------------------------------------
# 4/5. o ZEC (diagnostico, NUNCA treinamento -- secao 15)
# --------------------------------------------------------------------------


def available_references(state: dict[str, Any], bullish_trend: bool) -> dict[str, float]:
    """Os slots de REVERSAO com preco naquele candle, na ordem do codigo.

    Numa tendencia bullish a reversao e um CHoCH bearish, e o nivel que o
    preco tem de perder e do lado LOW -- e o lado que `reanchor_opposite`
    colapsa para cima em tendencia bullish. Escrever "high" aqui listaria os
    slots errados e faria toda referencia parecer indisponivel.
    """
    side = "low" if bullish_trend else "high"
    out: dict[str, float] = {}
    for key in (
        f"validated_choch_{side}",
        f"choch_origin_{side}",
        f"active_{side}",
        f"candidate_choch_{side}",
    ):
        price = getattr(state.get(key), "price", None)
        if price is not None:
            out[key] = float(price)
    return out


def zec_reconstruction() -> dict:
    """Secoes 4 e 5 no caso obrigatorio, com os dois contadores lado a lado."""
    symbol, timeframe, choch_ts = ZEC_BEARISH
    series = load_series(symbol, timeframe)
    lookback = swing_lookback_of(timeframe)
    run, observations, _attempts = observed_run(symbol, timeframe, series, len(series))
    candles = run.candles
    by_ts = {candle.timestamp: i for i, candle in enumerate(candles)}
    official = official_reference_prices(run)

    target = next(
        (
            event
            for event in run.events
            if not event.provisional
            and event.event is StructureEvent.CHANGE_OF_CHARACTER
            and event.direction is MarketDirection.BEARISH
            and event.timestamp.isoformat() == choch_ts
        ),
        None,
    )
    if target is None:
        return {"erro": f"CHoCH bearish {choch_ts} nao esta neste stream"}
    choch_index = by_ts[target.timestamp]

    # A perna e limitada ao proprio CHoCH que a abriu -- sem isso a "perna"
    # varre 2023-2026 inteiro e os limiares cruzam em datas sem sentido.
    opener = max(
        (
            by_ts[event.timestamp]
            for event in run.events
            if not event.provisional
            and event.event is StructureEvent.CHANGE_OF_CHARACTER
            and event.direction is MarketDirection.BULLISH
            and event.timestamp in by_ts
            and by_ts[event.timestamp] < choch_index
        ),
        default=0,
    )
    contiguous = [
        observation
        for observation in observations
        if opener <= observation.index <= choch_index and observation.trend == "bullish"
    ]

    bos = [
        {
            "timestamp": event.timestamp.isoformat(),
            "index": by_ts[event.timestamp],
            "price_level": float(event.price_level),
        }
        for event in run.events
        if not event.provisional
        and event.event is StructureEvent.BREAK_OF_STRUCTURE
        and event.direction is MarketDirection.BULLISH
        and event.timestamp in by_ts
        and opener <= by_ts[event.timestamp] <= choch_index
    ]

    timeline = []
    for observation in contiguous[-40:]:
        record = pullback_record(observation, run, candles, lookback, official)
        timeline.append(
            {
                "timestamp": observation.timestamp.isoformat(),
                "index": observation.index,
                "kind": observation.kind,
                "bars_since_advance": observation.bars_since_advance,
                "bars_since_pullback": observation.bars_since_pullback,
                "pullback": asdict(record) if record else None,
                "referencias": available_references(observation.state, bullish_trend=True),
            }
        )

    # Secao 4: quando o contador por pullback teria cruzado cada limiar.
    crossings = []
    for threshold in ZEC_THRESHOLDS:
        hit = next(
            (
                observation
                for observation in contiguous
                if (age := observation.bars_since_pullback) is not None and age >= threshold
            ),
            None,
        )
        if hit is None:
            crossings.append({"threshold": threshold, "timestamp": None})
            continue
        crossings.append(
            {
                "threshold": threshold,
                "timestamp": hit.timestamp.isoformat(),
                "index": hit.index,
                "velas_antes_do_choch": choch_index - hit.index,
                "bars_since_advance_ali": hit.bars_since_advance,
                "referencias": available_references(hit.state, bullish_trend=True),
            }
        )

    # Secao 5: o que o contrafactual REALMENTE tentou e conseguiu ali.
    detail = {}
    for variant in (BASELINE, Variant("pullback x1.00", pullback_age=True)):
        variant_run, attempts = run_with_attempts(
            symbol, timeframe, series, len(series), variant
        )
        detail[variant.label] = {
            "tentativas": [
                asdict(attempt)
                for attempt in attempts
                if "2026-03" <= attempt.timestamp <= "2026-07"
            ],
            "choch": [
                asdict(row)
                for row in choch_rows(variant_run)
                if "2026-04" <= row.timestamp <= "2026-08"
            ],
        }
    return {
        "leg_opener_index": opener,
        "choch_index": choch_index,
        "choch_timestamp": choch_ts,
        "bos": bos,
        "timeline": timeline,
        "crossings": crossings,
        "variantes": detail,
    }


# --------------------------------------------------------------------------
# coleta e relatorio
# --------------------------------------------------------------------------


def split_by_timeframe(rows: Sequence[Any], key=lambda row: row.timestamp):
    """70/30 por tempo, DENTRO de cada timeframe (a correcao da Etapa 4.2)."""
    discovery: list = []
    holdout: list = []
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(row.timeframe, []).append(row)
    for group in groups.values():
        group.sort(key=key)
        cut = int(len(group) * DISCOVERY_FRACTION)
        discovery.extend(group[:cut])
        holdout.extend(group[cut:])
    return discovery, holdout


def cut_timestamps(rows: Sequence[LegRow]) -> dict[str, str]:
    """A data de corte 70/30 de cada timeframe, congelada antes do holdout."""
    cuts: dict[str, str] = {}
    groups: dict[str, list[str]] = {}
    for row in rows:
        groups.setdefault(row.timeframe, []).append(row.timestamp)
    for timeframe, stamps in groups.items():
        stamps.sort()
        cuts[timeframe] = stamps[int(len(stamps) * DISCOVERY_FRACTION)] if stamps else ""
    return cuts


def num(value, digits: int = 1) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def aggregate(summaries: Sequence[dict]) -> dict:
    if not summaries:
        return {}
    out: dict[str, Any] = {}
    for key in ("n_baseline", "n_variant", "extra", "perdidos", "adiantados", "atrasados"):
        out[key] = sum(row[key] for row in summaries)
    for key in ("lead_mediano", "lag_mediano_variant", "move_pct_variant"):
        values = [row[key] for row in summaries if row.get(key) is not None]
        out[key] = round(statistics.median(values), 1) if values else None
    extra_total = out["extra"]
    for key in (
        "extra_confirmado", "extra_choch_failed", "extra_voltou", "extra_sem_desfecho",
        *[f"extra_voltou_{h}" for h in QUICK_BACK],
    ):
        weighted = sum(row.get(key, 0.0) * row["extra"] for row in summaries)
        out[key] = round(weighted / extra_total, 1) if extra_total else 0.0
    return out


def _keep(rows: Sequence[ChochRow], split: str, cut: str) -> list[ChochRow]:
    """O recorte 70/30 de um stream, pela data de corte congelada do timeframe."""
    if split == "discovery":
        return [row for row in rows if row.timestamp < cut]
    if split == "holdout":
        return [row for row in rows if row.timestamp >= cut]
    return list(rows)


def build(symbols: Sequence[str], windows: int, variants: Sequence[Variant]) -> dict:
    all_rows: list[LegRow] = []
    all_episodes: list[Episode] = []
    episodes_variant: dict[str, list[Episode]] = {}
    per_variant: dict[str, list[tuple[str, str, list[ChochRow], list[ChochRow]]]] = {
        variant.label: [] for variant in variants
    }
    counters: list[tuple[str, int, int]] = []
    combos = 0

    for symbol in symbols:
        for timeframe in TIMEFRAMES:
            if not (CACHE_DIR / f"{symbol}_{timeframe.value}.json").exists():
                continue
            try:
                series = load_series(symbol, timeframe)
            except Exception as error:  # noqa: BLE001 - cache com vela corrompida
                print(f"! cache invalido: {symbol} {timeframe.value}: {error}")
                continue
            for window in range(windows):
                end = len(series) - window * LIMIT
                if end - LIMIT - BUFFER < 0:
                    break
                try:
                    base_run, observations, base_attempts = observed_run(
                        symbol, timeframe, series, end, BASELINE
                    )
                except Exception as error:  # noqa: BLE001 - feed morto
                    print(f"! janela invalida: {symbol} {timeframe.value} w{window}: {error}")
                    continue
                combos += 1
                base_rows = choch_rows(base_run)
                all_rows.extend(leg_rows(base_run, observations, symbol, timeframe, window))
                all_episodes.extend(
                    episodes(base_run, symbol, timeframe, window, base_attempts)
                )
                # 3. os dois contadores, em TODO pivo (nao so nos CHoCH)
                for observation in observations:
                    advance = observation.bars_since_advance
                    pullback = observation.bars_since_pullback
                    if advance is None or pullback is None:
                        continue
                    counters.append((timeframe.value, advance, pullback))

                for variant in variants:
                    if variant is BASELINE:
                        rows = base_rows
                    else:
                        try:
                            run, _obs, attempts = observed_run(
                                symbol, timeframe, series, end, variant
                            )
                        except Exception:  # noqa: BLE001 - variante degenerada
                            continue
                        rows = choch_rows(run)
                        if variant.label == "pullback x1.00":
                            episodes_variant.setdefault(variant.label, []).extend(
                                episodes(base_run, symbol, timeframe, window, attempts)
                            )
                    per_variant[variant.label].append(
                        (symbol, timeframe.value, base_rows, rows)
                    )

    print(f"combos medidos: {combos}  CHoCH no painel: {len(all_rows)}")
    report: dict = {"symbols": list(symbols), "combos": combos, "n_choch": len(all_rows)}

    # ---- 1/3. quao diferentes sao os dois contadores ----
    print("\n== 1/3. bars_since_pullback vs bars_since_advance (todo pivo) ==")
    header = (
        f"{'tf':>5} {'pivos':>7} {'adv med':>8} {'pull med':>9} "
        f"{'dif med':>8} {'dif>0':>7} {'p90 dif':>8}"
    )
    print(header)
    print("-" * len(header))
    counter_report = {}
    for timeframe in [t.value for t in TIMEFRAMES] + ["TOTAL"]:
        group = (
            counters if timeframe == "TOTAL"
            else [row for row in counters if row[0] == timeframe]
        )
        if not group:
            continue
        advances = [row[1] for row in group]
        pullbacks = [row[2] for row in group]
        diffs = [row[2] - row[1] for row in group]
        counter_report[timeframe] = {
            "n": len(group),
            "advance_mediana": statistics.median(advances),
            "pullback_mediana": statistics.median(pullbacks),
            "dif_mediana": statistics.median(diffs),
            "dif_positiva_pct": round(sum(1 for d in diffs if d > 0) / len(diffs) * 100, 1),
            "dif_p90": quantile(diffs, 0.90),
        }
        row = counter_report[timeframe]
        print(
            f"{timeframe:>5} {row['n']:>7} {row['advance_mediana']:>8.0f} "
            f"{row['pullback_mediana']:>9.0f} {row['dif_mediana']:>8.0f} "
            f"{row['dif_positiva_pct']:>6.1f}% {row['dif_p90']:>8.0f}"
        )
    report["counters"] = counter_report

    # quantas vezes o gatilho novo dispararia e o atual nao
    print("\n  gatilho no limiar de producao (x1.00): pivos em que")
    fires = {}
    for timeframe in [t.value for t in TIMEFRAMES]:
        base = dd._STALE_REANCHOR_CANDLES.get(
            TimeFrame(timeframe), dd._DEFAULT_STALE_REANCHOR_CANDLES
        )
        group = [row for row in counters if row[0] == timeframe]
        if not group or base is None:
            continue
        both = sum(1 for _t, a, p in group if a >= base and p >= base)
        only_new = sum(1 for _t, a, p in group if a < base <= p)
        only_old = sum(1 for _t, a, p in group if p < base <= a)
        fires[timeframe] = {"base": base, "ambos": both, "so_novo": only_new, "so_atual": only_old}
        print(
            f"    {timeframe:>4} N={base:>3} ambos {both:>6} | "
            f"so o novo {only_new:>6} | so o atual {only_old:>6}"
        )
    report["fires"] = fires

    # ---- 2/7. correlacao com o atraso do CHoCH ----
    print("\n== 2/7. SPEARMAN vs CHoCH lag (positivo = mais idade, mais atraso) ==")
    features = {
        "A bars_since_advance": lambda row: row.bars_since_advance,
        "B bars_since_pullback": lambda row: row.bars_since_pullback,
        "C bos_since_pullback": lambda row: row.bos_since_pullback,
        "D reference_age_bars": lambda row: row.reference_age_bars,
        "E pivots_since_reference": lambda row: row.pivots_since_reference,
        "  reference_to_extreme_atr": lambda row: row.reference_to_extreme_atr,
    }
    correlations = {}
    for name, getter in features.items():
        pairs = [
            (float(value), float(row.choch_lag_bars))
            for row in all_rows
            if (value := getter(row)) is not None and row.choch_lag_bars is not None
        ]
        rho = spearman(pairs)
        correlations[name.strip()] = {"n": len(pairs), "rho": rho}
        print(f"  {name:<26} n={len(pairs):>5} rho={num(rho, 3)}")
    report["correlations"] = correlations

    # B acrescenta algo sobre D? spearman de B dentro de tercis de D.
    print("\n  B dentro de tercis de D (o teste de 'acrescenta sobre reference_age'):")
    usable = [
        row for row in all_rows
        if row.reference_age_bars is not None
        and row.bars_since_pullback is not None
        and row.choch_lag_bars is not None
    ]
    strata = {}
    if len(usable) >= 30:
        ages = sorted(row.reference_age_bars for row in usable)
        low = ages[len(ages) // 3]
        high = ages[2 * len(ages) // 3]
        for label, group in (
            ("D baixo", [r for r in usable if r.reference_age_bars <= low]),
            ("D medio", [r for r in usable if low < r.reference_age_bars <= high]),
            ("D alto", [r for r in usable if r.reference_age_bars > high]),
        ):
            rho = spearman(
                [(float(r.bars_since_pullback), float(r.choch_lag_bars)) for r in group]
            )
            strata[label] = {"n": len(group), "rho": rho}
            print(f"    {label:<9} n={len(group):>5} rho(B, lag)={num(rho, 3)}")
    report["strata"] = strata

    # ---- 6/8/9/10/13/14. as variantes ----
    cuts = cut_timestamps(all_rows)
    report["cuts"] = cuts

    def summarize(split: str) -> dict:
        out = {}
        for variant in variants:
            summaries = []
            for _symbol, timeframe, base_rows, rows in per_variant[variant.label]:
                cut = cuts.get(timeframe, "")
                summaries.append(
                    variant_summary(
                        _keep(base_rows, split, cut), _keep(rows, split, cut)
                    )
                )
            aggregated = aggregate(summaries)
            if aggregated:
                out[variant.label] = aggregated
        return out

    for split in ("painel", "discovery", "holdout"):
        section = summarize(split)
        report[f"variants_{split}"] = section
        title = {
            "painel": "6/9. PAINEL AMPLO",
            "discovery": "13/14. DISCOVERY (70%)",
            "holdout": "14. HOLDOUT (30%, por timeframe)",
        }[split]
        print(f"\n== {title} ==")
        header = (
            f"{'variante':<16} {'CHoCH':>6} {'extra':>6} {'perd':>5} {'adiant':>7} "
            f"{'lead':>5} {'lag':>5} {'mov%':>6} {'ok':>6} {'✕':>6} {'volta20':>8}"
        )
        print(header)
        print("-" * len(header))
        for label, summary in section.items():
            print(
                f"{label:<16} {summary['n_variant']:>6} {summary['extra']:>6} "
                f"{summary['perdidos']:>5} {summary['adiantados']:>7} "
                f"{num(summary['lead_mediano'], 0):>5} "
                f"{num(summary['lag_mediano_variant'], 0):>5} "
                f"{num(summary['move_pct_variant']):>6} "
                f"{summary['extra_confirmado']:>5.1f}% {summary['extra_choch_failed']:>5.1f}% "
                f"{summary['extra_voltou_20']:>7.1f}%"
            )

    # ---- 11/12. expansao longa sem pullback: distingue ou dispara nos dois? ----
    print(f"\n== 11/12. EXPANSOES LONGAS SEM PULLBACK ({len(all_episodes)} episodios) ==")
    episode_report = {}
    for label, group in (
        ("producao", all_episodes),
        ("pullback x1.00", episodes_variant.get("pullback x1.00", [])),
    ):
        if not group:
            continue
        target = [e for e in group if e.reversed_after]
        control = [e for e in group if not e.reversed_after]
        fired_target = sum(1 for e in target if e.reanchors_during > 0)
        fired_control = sum(1 for e in control if e.reanchors_during > 0)
        episode_report[label] = {
            "n_alvo": len(target),
            "n_controle": len(control),
            "disparou_alvo_pct": round(fired_target / len(target) * 100, 1) if target else None,
            "disparou_controle_pct": (
                round(fired_control / len(control) * 100, 1) if control else None
            ),
        }
        row = episode_report[label]
        print(
            f"  {label:<15} reverteu {fired_target}/{len(target)} "
            f"({num(row['disparou_alvo_pct'])}%) | continuou {fired_control}/{len(control)} "
            f"({num(row['disparou_controle_pct'])}%)"
        )
    report["episodes"] = episode_report

    # ---- 14. holdout dos episodios ----
    discovery, holdout = split_by_timeframe(all_episodes, key=lambda e: e.start_timestamp)
    report["episode_holdout"] = {"discovery": len(discovery), "holdout": len(holdout)}
    return report


def print_zec(detail: dict) -> None:
    if "erro" in detail:
        print(f"  ! {detail['erro']}")
        return
    print(
        f"  CHoCH bearish oficial: {detail['choch_timestamp'][:10]} "
        f"(indice {detail['choch_index']})"
    )
    print("  BOS bullish da perna:")
    for bos in detail["bos"]:
        print(f"    {bos['timestamp'][:10]} nivel {bos['price_level']:>8.2f}")
    print("\n  4. quando o contador POR PULLBACK cruzaria cada limiar:")
    for crossing in detail["crossings"]:
        if crossing["timestamp"] is None:
            print(f"    N={crossing['threshold']:>3}  nunca cruzou nesta perna")
            continue
        refs = ", ".join(
            f"{name}={price:.2f}" for name, price in crossing["referencias"].items()
        ) or "nenhuma"
        print(
            f"    N={crossing['threshold']:>3}  {crossing['timestamp'][:10]} "
            f"({crossing['velas_antes_do_choch']:>3} velas antes do CHoCH; "
            f"contador atual ali = {crossing['bars_since_advance_ali']})"
        )
        print(f"           referencias disponiveis: {refs}")
    print("\n  5. o que o contrafactual tentou de fato:")
    for label, data in detail["variantes"].items():
        print(f"    [{label}]")
        if not data["tentativas"]:
            print("      nenhuma tentativa de re-anchor na janela -> NO_EFFECT")
        for attempt in data["tentativas"]:
            print(
                f"      {attempt['timestamp'][:10]} nivel {attempt['level']:>8.2f} "
                f"preco {attempt['current_price']:>8.2f} "
                f"{'MOVEU' if attempt['moved'] else 'recusado (NO_EFFECT)'}"
            )
        for row in data["choch"]:
            print(
                f"      -> CHoCH {row['timestamp'][:10]} {row['direction']:<7} "
                f"ref {num(row['reference_price'], 2)} lag {row['lag_bars']} "
                f"mov {num(row['move_completed_pct'])}%"
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

    report = build(symbols, args.windows, VARIANTS)

    print("\n== 4/5. ZEC D1 (diagnostico, nunca treinamento -- secao 15) ==")
    detail = zec_reconstruction()
    report["zec"] = detail
    print_zec(detail)

    if args.json:
        args.json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\n-> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
