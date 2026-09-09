"""Latencia estrutural: quantos candles/ATR o SMC fica atras do preco.

O caso que originou a medicao e o ZECUSDT 1D atual, onde a leitura
retrospectiva parece certa mas as viradas chegam tarde:

- ciclo bullish ate 2026-06-03 (topo 644.67);
- queda praticamente vertical em 2026-06-04/05 (621 -> 250, dois candles);
- fundo e recuperacao a partir de 2026-06-28 (367.77);
- `CHoCH` bullish so em 2026-08-19.

Isso e um problema DIFERENTE do `StructuralStall`. O stall pergunta "a perna
vigente parou de avancar?"; aqui a pergunta e "quando a maquina CONSEGUE
declarar a perna nova?". Uma e sobre a perna velha morrer, a outra e sobre a
perna nova nascer, e a auditoria existe justamente para separar as duas.

Decomposicao
============

Todo evento estrutural passa por fases datavaeis, e o atraso total e a soma
delas. Para cada `CHoCH`/`BOS` relevante o modulo data:

1. `move_start`   -- o extremo da perna anterior (onde o movimento comecou);
2. `ref_formed`   -- o candle do pivo que virou `reference_price_level`;
3. `ref_confirmed`-- `ref_formed + swing_lookback` (o pivo so existe depois de
   `lookback` candles a direita -- em D1 isso ja e uma semana);
4. `first_touch`  -- primeiro candle que atravessa o nivel com pavio;
5. `close_break`  -- primeiro candle que FECHA alem do nivel;
6. `event_ts`     -- o candle a que o detector atribui o evento;
7. `emitted`      -- o primeiro prefixo em que o evento EXISTE (replay
   incremental de `research/_replay.py`); e a unica data acionavel, porque as
   anteriores so sao conhecidas depois.

`emitted - move_start` e o atraso que o usuario ve no grafico. As diferencas
entre fases dizem ONDE ele nasce: confirmacao de pivo (3-2), espera de
rompimento (5-3), maturacao interna do detector (7-6).

Metricas
========

`STRUCTURAL_LAG_BARS` e `STRUCTURAL_LAG_ATR` sao medidos do inicio do
movimento ate a emissao, com DUAS definicoes de inicio documentadas -- nenhuma
e obviamente certa e as duas sao reportadas:

- `extreme`  -- o extremo da perna anterior (o topo/fundo real);
- `shock`    -- o primeiro displacement significativo contra a tendencia
  vigente (>= `SHOCK_ATR` em ate `SHOCK_N` candles), que e o primeiro instante
  em que um humano diria "mudou o comportamento".

`move_done_pct` completa a leitura: quanto do movimento (extremo -> extremo)
ja tinha acontecido quando o evento ficou visivel.

O que NAO e medido aqui
=======================

Nada e corrigido. Nenhum evento novo e proposto, nenhum threshold e mexido. O
`displacement shock` (secao 12 do pedido) e contado como FENOMENO, para testar
a correlacao com o atraso -- nao como estado a implementar.

Uso
===

    poetry run python -m research.zec_d1_structure_lag
    poetry run python -m research.zec_d1_structure_lag --json \
        research/zec_d1_structure_lag_baseline.json
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.liquidity.structural_stall import (
    DEFAULT_STALL_BARS,
    DEFAULT_STALL_RETRACEMENT_ATR,
    detect_structural_stall,
    frozen_atr_pct,
)
from research._replay import scan_first_emissions
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series

#: O caso principal.
CASE_SYMBOL = "ZECUSDT"
CASE_TIMEFRAME = TimeFrame.D1

#: O candle da queda vertical (621 -> 459, low 442) e o do fundo do movimento
#: seguinte. Ancoras dos snapshots -- escolhidos pelo PRECO (maior candle
#: bearish e minima local), nao por onde os eventos caem.
CRASH_CANDLE = datetime.fromisoformat("2026-06-04T00:00:00+00:00")
RECOVERY_CANDLE = datetime.fromisoformat("2026-06-28T00:00:00+00:00")

#: Painel D1 para a pergunta "N=50 faz sentido no diario?".
D1_SYMBOLS = ["ZECUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "AAVEUSDT"]

#: `_INTERNAL_STRUCTURE_PARAMS[D1]` -- lido, nunca alterado.
SWING_LOOKBACK = dd._INTERNAL_STRUCTURE_PARAMS[TimeFrame.D1][0]

N = DEFAULT_STALL_BARS
K = DEFAULT_STALL_RETRACEMENT_ATR

#: Os advances: o que move a leitura de tendencia (mesmo conjunto de
#: `_advance_boundaries` e de `_last_advance`).
ADVANCES = (
    StructureEvent.BREAK_OF_STRUCTURE,
    StructureEvent.CHANGE_OF_CHARACTER,
    StructureEvent.CHOCH_FAILED,
)

#: Definicao de `shock` usada como inicio-de-movimento alternativo.
SHOCK_N = 3
SHOCK_ATR = 3.0
#: Grade explorada na secao de displacement shock.
SHOCK_NS = (1, 2, 3, 5)
SHOCK_THRESHOLDS = (3.0, 5.0, 8.0, 10.0)

#: Quantos candles finais entram no replay incremental. O replay custa uma
#: passada da pipeline por candle (~20ms em D1); 400 cobre com folga o periodo
#: sob analise (2025-08 em diante) sem replayar tres anos que ninguem le.
REPLAY_TAIL = 400
#: Um evento datado ANTES do inicio do replay tem `first_seen` no primeiro
#: prefixo por construcao -- nao e emissao, e o horizonte da varredura. Esses
#: sao marcados e excluidos das estatisticas de emissao.
#: (E o mesmo artefato que produz "lag=1170" quando a ancora estrutural desloca
#: e traz historia profunda de volta para a janela.)


# --------------------------------------------------------------------------
# 1. reproducao da producao
# --------------------------------------------------------------------------


def production_run(symbol: str, timeframe: TimeFrame, limit: int = LIMIT):
    """A MESMA pipeline que desenha o grafico, sobre o cache ja baixado."""
    series = load_series(symbol, timeframe)
    window = series[-(limit + BUFFER) :]
    return dd._run_internal_structure(
        provider=SliceProvider(window),
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
        confluence_filter=True,
    )


def raw_detector_events(run) -> list[MarketStructure]:
    """Os eventos do detector CRU, antes de qualquer pass de composicao.

    Reconstroi o detector com a mesma fatia ancorada. Serve so para o diff da
    secao 9 -- nada aqui volta para a producao.
    """
    detector = dd._build_internal_detector(TimeFrame.D1, confluence_filter=True)
    return detector.detect(run.internal_candles, range_resets=[])


def index_by_timestamp(candles: Sequence[Candle]) -> dict[datetime, int]:
    return {c.timestamp: i for i, c in enumerate(candles)}


def trend_after(events: Sequence[MarketStructure]) -> MarketDirection | None:
    """A tendencia que o stream deixa, com a semantica do frontend.

    `structureTrendByCandle` (`frontend/src/utils/tideRibbon.ts`): BOS e CHoCH
    valem a propria direcao, `CHOCH_FAILED` vale a INVERSA (a direcao do `✕` e
    a do CHoCH que falhou). Provisionais nao contam.
    """
    trend: MarketDirection | None = None
    for event in events:
        if event.provisional or event.event not in ADVANCES:
            continue
        if event.event is StructureEvent.CHOCH_FAILED:
            trend = (
                MarketDirection.BEARISH
                if event.direction is MarketDirection.BULLISH
                else MarketDirection.BULLISH
            )
        else:
            trend = event.direction
    return trend


# --------------------------------------------------------------------------
# 2. timeline
# --------------------------------------------------------------------------


@dataclass
class EventRow:
    timestamp: str
    index: int | None
    event: str
    direction: str
    price_level: float
    reference_price_level: float | None
    reference_timestamp: str | None
    reference_structural: bool | None
    provisional: bool
    trend_before: str | None
    trend_after: str | None
    #: Indice do primeiro prefixo em que o evento existe (replay).
    emitted_index: int | None = None
    emitted_timestamp: str | None = None
    emission_lag_bars: int | None = None
    #: `first_seen` no primeiro prefixo varrido: horizonte, nao emissao.
    emission_truncated: bool = False


def timeline(
    events: Sequence[MarketStructure],
    candles: Sequence[Candle],
    first_seen: dict | None = None,
    replay_start: int | None = None,
    since: datetime | None = None,
) -> list[EventRow]:
    by_ts = index_by_timestamp(candles)
    rows: list[EventRow] = []
    for position, event in enumerate(events):
        if since is not None and event.timestamp < since:
            continue
        prefix = events[: position + 1]
        index = by_ts.get(event.timestamp)
        row = EventRow(
            timestamp=event.timestamp.isoformat(),
            index=index,
            event=event.event.value,
            direction=event.direction.value,
            price_level=event.price_level,
            reference_price_level=event.reference_price_level,
            reference_timestamp=(
                event.reference_timestamp.isoformat() if event.reference_timestamp else None
            ),
            reference_structural=event.reference_structural,
            provisional=event.provisional,
            trend_before=(t.value if (t := trend_after(events[:position])) else None),
            trend_after=(t.value if (t := trend_after(prefix)) else None),
        )
        if first_seen is not None and index is not None:
            key = (event.timestamp, event.event, event.direction)
            cut = first_seen.get(key)
            if cut is not None:
                row.emitted_index = cut
                row.emitted_timestamp = candles[cut].timestamp.isoformat()
                row.emission_lag_bars = cut - index
                row.emission_truncated = replay_start is not None and cut <= replay_start
        rows.append(row)
    return rows


# --------------------------------------------------------------------------
# 3/4/6/14. decomposicao do atraso
# --------------------------------------------------------------------------


def true_range(candles: Sequence[Candle], index: int) -> float:
    candle = candles[index]
    if index == 0:
        return candle.high - candle.low
    prev = candles[index - 1].close
    return max(candle.high - candle.low, abs(candle.high - prev), abs(candle.low - prev))


def atr_at(candles: Sequence[Candle], index: int, window: int = 14) -> float:
    """ATR absoluto causal (so candles ate `index`), para converter barras em ATR."""
    start = max(1, index - window + 1)
    values = [true_range(candles, i) for i in range(start, index + 1)]
    return sum(values) / len(values) if values else 0.0


def leg_extreme_before(
    candles: Sequence[Candle], end_index: int, bullish_move: bool, lookback: int = 120
) -> tuple[int, float]:
    """O extremo de onde o movimento partiu: topo antes de queda, fundo antes de alta."""
    start = max(0, end_index - lookback)
    if bullish_move:
        best = min(range(start, end_index + 1), key=lambda i: candles[i].low)
        return best, candles[best].low
    best = max(range(start, end_index + 1), key=lambda i: candles[i].high)
    return best, candles[best].high


def first_shock_against(
    candles: Sequence[Candle],
    start_index: int,
    end_index: int,
    bearish_move: bool,
    *,
    n: int = SHOCK_N,
    k: float = SHOCK_ATR,
) -> int | None:
    """Primeiro candle em [start, end] com >= `k` ATR de deslocamento no sentido do movimento.

    Causal: o ATR usado e o do candle de referencia (`index - n`), nunca o final.
    """
    for index in range(max(start_index, n), end_index + 1):
        atr = atr_at(candles, index - n)
        if atr <= 0:
            continue
        move = candles[index].close - candles[index - n].close
        if bearish_move:
            move = -move
        if move / atr >= k:
            return index
    return None


@dataclass
class Decomposition:
    """Todas as fases de um evento, e as diferencas entre elas."""

    timestamp: str
    event: str
    direction: str
    #: --- fases (indice na janela visivel) ---
    move_start_index: int | None = None
    move_start_timestamp: str | None = None
    move_start_price: float | None = None
    ref_formed_index: int | None = None
    ref_formed_timestamp: str | None = None
    ref_confirmed_index: int | None = None
    ref_confirmed_timestamp: str | None = None
    first_touch_index: int | None = None
    first_touch_timestamp: str | None = None
    close_break_index: int | None = None
    close_break_timestamp: str | None = None
    event_index: int | None = None
    emitted_index: int | None = None
    emitted_timestamp: str | None = None
    #: --- diferencas em candles ---
    pivot_confirmation_lag: int | None = None   # ref_formed -> ref_confirmed
    break_wait_lag: int | None = None           # ref_confirmed -> close_break
    touch_to_close_lag: int | None = None       # first_touch -> close_break
    dating_lag: int | None = None               # close_break -> event_index
    emission_lag: int | None = None             # event_index -> emitted
    #: --- metrica agregada ---
    structural_lag_bars_extreme: int | None = None
    structural_lag_atr_extreme: float | None = None
    shock_index: int | None = None
    shock_timestamp: str | None = None
    structural_lag_bars_shock: int | None = None
    structural_lag_atr_shock: float | None = None
    #: Quanto do movimento (extremo -> extremo do periodo) ja tinha ocorrido.
    move_done_pct_at_event: float | None = None
    move_done_pct_at_emission: float | None = None
    move_total_pct: float | None = None


def decompose(
    event: MarketStructure,
    candles: Sequence[Candle],
    by_ts: dict[datetime, int],
    emitted_index: int | None,
    *,
    lookback: int = SWING_LOOKBACK,
) -> Decomposition:
    bearish = event.direction is MarketDirection.BEARISH
    event_index = by_ts.get(event.timestamp)
    out = Decomposition(
        timestamp=event.timestamp.isoformat(),
        event=event.event.value,
        direction=event.direction.value,
        event_index=event_index,
        emitted_index=emitted_index,
        emitted_timestamp=(
            candles[emitted_index].timestamp.isoformat() if emitted_index is not None else None
        ),
    )
    level = event.reference_price_level
    ref_index = by_ts.get(event.reference_timestamp) if event.reference_timestamp else None
    if ref_index is not None:
        out.ref_formed_index = ref_index
        out.ref_formed_timestamp = candles[ref_index].timestamp.isoformat()
        confirmed = min(ref_index + lookback, len(candles) - 1)
        out.ref_confirmed_index = confirmed
        out.ref_confirmed_timestamp = candles[confirmed].timestamp.isoformat()
        out.pivot_confirmation_lag = confirmed - ref_index
    if level is not None and ref_index is not None:
        for index in range(ref_index + 1, len(candles)):
            hit = candles[index].low < level if bearish else candles[index].high > level
            if hit and out.first_touch_index is None:
                out.first_touch_index = index
                out.first_touch_timestamp = candles[index].timestamp.isoformat()
            closed = candles[index].close < level if bearish else candles[index].close > level
            if closed:
                out.close_break_index = index
                out.close_break_timestamp = candles[index].timestamp.isoformat()
                break
        if out.close_break_index is not None:
            if out.ref_confirmed_index is not None:
                out.break_wait_lag = out.close_break_index - out.ref_confirmed_index
            if out.first_touch_index is not None:
                out.touch_to_close_lag = out.close_break_index - out.first_touch_index
            if event_index is not None:
                out.dating_lag = event_index - out.close_break_index
    if event_index is not None and emitted_index is not None:
        out.emission_lag = emitted_index - event_index

    # --- inicio do movimento e a metrica agregada ---
    if event_index is None:
        return out
    start_index, start_price = leg_extreme_before(candles, event_index, bullish_move=not bearish)
    out.move_start_index = start_index
    out.move_start_timestamp = candles[start_index].timestamp.isoformat()
    out.move_start_price = start_price
    atr = atr_at(candles, start_index)
    reference = emitted_index if emitted_index is not None else event_index
    out.structural_lag_bars_extreme = reference - start_index
    if atr > 0:
        out.structural_lag_atr_extreme = abs(candles[reference].close - start_price) / atr
    # A busca do shock NAO para no evento: se o deslocamento so chega depois,
    # o lag sai negativo, e a leitura correta e "o detector chegou ANTES do
    # movimento virar shock" -- nao "nao houve shock".
    shock = first_shock_against(
        candles, start_index, min(len(candles) - 1, event_index + 30), bearish_move=bearish
    )
    if shock is not None:
        out.shock_index = shock
        out.shock_timestamp = candles[shock].timestamp.isoformat()
        out.structural_lag_bars_shock = reference - shock
        shock_atr = atr_at(candles, shock)
        if shock_atr > 0:
            out.structural_lag_atr_shock = (
                abs(candles[reference].close - candles[shock].close) / shock_atr
            )

    # --- percentual do movimento ja percorrido ---
    end = min(len(candles) - 1, event_index + 60)
    segment = range(start_index, end + 1)
    extreme = (
        min(candles[i].low for i in segment) if bearish else max(candles[i].high for i in segment)
    )
    span = abs(extreme - start_price)
    out.move_total_pct = span / start_price * 100 if start_price else None
    if span > 0:
        out.move_done_pct_at_event = (
            abs(candles[event_index].close - start_price) / span * 100
        )
        if emitted_index is not None:
            out.move_done_pct_at_emission = (
                abs(candles[emitted_index].close - start_price) / span * 100
            )
    return out


# --------------------------------------------------------------------------
# 5. snapshots ao redor da queda vertical
# --------------------------------------------------------------------------


@dataclass
class Snapshot:
    offset: int
    timestamp: str
    close: float
    trend: str
    events_in_window: int
    last_advance: str | None = None
    last_advance_timestamp: str | None = None
    last_bos: str | None = None
    last_choch: str | None = None
    last_reference_price_level: float | None = None
    stall: dict | None = None


def snapshots(
    symbol: str,
    timeframe: TimeFrame,
    series: Sequence[Candle],
    pivot_timestamp: datetime,
    offsets: Sequence[int] = (-1, 0, 1, 3, 5, 10),
    limit: int = LIMIT,
) -> list[Snapshot]:
    """O que a maquina enxergava em cada prefixo ao redor de um candle.

    Cada offset e uma rodada COMPLETA da pipeline sobre a serie truncada ali --
    e o unico jeito honesto de perguntar "o que era conhecido naquele dia".
    """
    base = next(i for i, c in enumerate(series) if c.timestamp == pivot_timestamp)
    out: list[Snapshot] = []
    for offset in offsets:
        cut = base + offset
        window = series[max(0, cut + 1 - (limit + BUFFER)) : cut + 1]
        run = dd._run_internal_structure(
            provider=SliceProvider(list(window)),
            symbol=symbol,
            timeframe=timeframe,
            limit=min(limit, len(window)),
            confluence_filter=True,
        )
        advances = [e for e in run.events if not e.provisional and e.event in ADVANCES]
        bos = [e for e in advances if e.event is StructureEvent.BREAK_OF_STRUCTURE]
        choch = [e for e in advances if e.event is StructureEvent.CHANGE_OF_CHARACTER]
        stall = detect_structural_stall(run.candles, run.events)
        out.append(
            Snapshot(
                offset=offset,
                timestamp=series[cut].timestamp.isoformat(),
                close=series[cut].close,
                trend=run.trend.value,
                events_in_window=len(run.events),
                last_advance=(
                    f"{advances[-1].event.value} {advances[-1].direction.value}"
                    if advances
                    else None
                ),
                last_advance_timestamp=(
                    advances[-1].timestamp.isoformat() if advances else None
                ),
                last_bos=(
                    f"{bos[-1].timestamp.date()} {bos[-1].direction.value} @{bos[-1].price_level}"
                    if bos
                    else None
                ),
                last_choch=(
                    f"{choch[-1].timestamp.date()} {choch[-1].direction.value} "
                    f"@{choch[-1].price_level} ref={choch[-1].reference_price_level}"
                    if choch
                    else None
                ),
                last_reference_price_level=(
                    advances[-1].reference_price_level if advances else None
                ),
                stall=(
                    {
                        "stale_since": stall.stale_since.isoformat(),
                        "direction": stall.direction.value,
                        "bars_since_advance": stall.bars_since_advance,
                        "retracement_atr": stall.retracement_atr,
                    }
                    if stall
                    else None
                ),
            )
        )
    return out


# --------------------------------------------------------------------------
# 11. StructuralStall no D1
# --------------------------------------------------------------------------


@dataclass
class StallCase:
    symbol: str
    timeframe: str
    opener_timestamp: str
    opener_direction: str
    #: `BREAK_OF_STRUCTURE` (o unico opener que a producao aceita) ou
    #: `CHANGE_OF_CHARACTER` (contrafactual: o guard de
    #: `detect_structural_stall` recusa, e a secao 11 mede o que se perde).
    opener_event: str
    bars_to_stall: int | None
    stale_since: str | None
    retracement_atr: float | None
    #: Barras entre o opener e o proximo advance: se for < bars_to_stall, a
    #: perna foi substituida antes de poder ficar STALE.
    bars_to_next_advance: int | None
    #: % do movimento posterior ja percorrido quando o STALE dispararia.
    move_done_pct: float | None


def counterfactual_stall(
    candles: Sequence[Candle],
    opener: MarketStructure,
    opener_index: int,
    *,
    n: int = N,
    k: float = K,
) -> tuple[int, int, float] | None:
    """A aritmetica de `detect_structural_stall` com o opener imposto.

    Mesma copia deliberada de `research/choch_failed_stall_audit._counterfactual`
    (fechada contra a producao pelos testes daquele modulo): extremo em CLOSE
    desde o opener, retracao normalizada por `price_level` e por
    `frozen_atr_pct` congelado no opener, gatilho no primeiro candle com as
    duas condicoes. Aqui ela existe para responder "QUANDO teria disparado" --
    a producao so responde "esta em stall agora?".
    """
    atr = frozen_atr_pct(candles, opener_index)
    entry = opener.price_level
    if atr <= 0 or entry <= 0:
        return None
    bullish = opener.direction is MarketDirection.BULLISH
    extreme = candles[opener_index].close
    for index in range(opener_index, len(candles)):
        close = candles[index].close
        if bullish:
            extreme = max(extreme, close)
            give_back = extreme - close
        else:
            extreme = min(extreme, close)
            give_back = close - extreme
        bars = index - opener_index
        retracement = give_back / entry / atr
        if bars >= n and retracement >= k:
            return index, bars, retracement
    return None


def stall_scan(
    symbol: str, timeframe: TimeFrame = TimeFrame.D1, *, openers: Sequence[StructureEvent] = ()
) -> list[StallCase]:
    """Para cada advance do stream final: quando a perna teria ficado STALE.

    `openers` default = so `BREAK_OF_STRUCTURE`, que e o que a producao aceita.
    Passar `CHANGE_OF_CHARACTER` junto mede o contrafactual da secao 11: uma
    perna ABERTA por um CHoCH e que nunca mais avanca e invisivel para
    `detect_structural_stall`, por construcao do guard do opener.
    """
    openers = openers or (StructureEvent.BREAK_OF_STRUCTURE,)
    run = production_run(symbol, timeframe)
    candles = run.candles
    by_ts = index_by_timestamp(candles)
    advances = [
        (e, by_ts[e.timestamp])
        for e in run.events
        if not e.provisional and e.event in ADVANCES and e.timestamp in by_ts
    ]
    out: list[StallCase] = []
    for position, (event, index) in enumerate(advances):
        if event.event not in openers:
            continue
        next_index = advances[position + 1][1] if position + 1 < len(advances) else None
        trigger = counterfactual_stall(candles, event, index)
        move_done = None
        if trigger is not None:
            bullish = event.direction is MarketDirection.BULLISH
            segment = range(index, trigger[0] + 1)
            extreme = (
                max(candles[i].high for i in segment)
                if bullish
                else min(candles[i].low for i in segment)
            )
            span = abs(extreme - candles[index].close)
            if span > 0:
                move_done = (
                    abs(candles[trigger[0]].close - candles[index].close) / span * 100
                )
        out.append(
            StallCase(
                symbol=symbol,
                timeframe=timeframe.value,
                opener_timestamp=event.timestamp.isoformat(),
                opener_direction=event.direction.value,
                opener_event=event.event.value,
                bars_to_stall=trigger[1] if trigger else None,
                stale_since=(candles[trigger[0]].timestamp.isoformat() if trigger else None),
                retracement_atr=trigger[2] if trigger else None,
                bars_to_next_advance=(next_index - index if next_index is not None else None),
                move_done_pct=move_done,
            )
        )
    return out


# --------------------------------------------------------------------------
# 12. displacement shock
# --------------------------------------------------------------------------


@dataclass
class Shock:
    timestamp: str
    index: int
    n: int
    threshold: float
    atr_moved: float
    direction: str
    close_from: float
    close_to: float


def displacement_shocks(
    candles: Sequence[Candle],
    ns: Sequence[int] = SHOCK_NS,
    thresholds: Sequence[float] = SHOCK_THRESHOLDS,
    since_index: int = 0,
) -> list[Shock]:
    """Episodios `abs(close_t - close_{t-n}) / ATR >= threshold`.

    O ATR e o do candle `t-n` (causal: o deslocamento e medido contra a
    volatilidade que existia ANTES dele, nao contra a que ele mesmo criou).
    Um episodio e contado uma vez por (n, threshold).
    """
    out: list[Shock] = []
    for n in ns:
        for threshold in thresholds:
            for index in range(max(since_index, n), len(candles)):
                atr = atr_at(candles, index - n)
                if atr <= 0:
                    continue
                move = candles[index].close - candles[index - n].close
                if abs(move) / atr >= threshold:
                    out.append(
                        Shock(
                            timestamp=candles[index].timestamp.isoformat(),
                            index=index,
                            n=n,
                            threshold=threshold,
                            atr_moved=abs(move) / atr,
                            direction=("bullish" if move > 0 else "bearish"),
                            close_from=candles[index - n].close,
                            close_to=candles[index].close,
                        )
                    )
    return out


# --------------------------------------------------------------------------
# 9. passes
# --------------------------------------------------------------------------


def pass_diff(run, since: datetime | None = None) -> dict:
    """`detector.detect` cru vs stream pos-passes vs stream visivel."""
    raw = raw_detector_events(run)

    def keys(events: Sequence[MarketStructure]) -> set[tuple]:
        return {
            (e.timestamp.isoformat(), e.event.value, e.direction.value)
            for e in events
            if (since is None or e.timestamp >= since) and e.event in ADVANCES
        }

    raw_keys, final_keys = keys(raw), keys(run.events)
    raw_by_key = {
        (e.timestamp.isoformat(), e.event.value, e.direction.value): e for e in raw
    }
    final_by_key = {
        (e.timestamp.isoformat(), e.event.value, e.direction.value): e for e in run.events
    }
    retimed = []
    for key in raw_keys & final_keys:
        a, b = raw_by_key[key], final_by_key[key]
        if a.reference_price_level != b.reference_price_level or a.provisional != b.provisional:
            retimed.append(
                {
                    "key": key,
                    "raw_reference": a.reference_price_level,
                    "final_reference": b.reference_price_level,
                    "raw_provisional": a.provisional,
                    "final_provisional": b.provisional,
                }
            )
    return {
        "raw_count": len(raw_keys),
        "final_count": len(final_keys),
        "dropped_by_passes": sorted(raw_keys - final_keys),
        "added_by_passes": sorted(final_keys - raw_keys),
        "rewritten": retimed,
    }


# --------------------------------------------------------------------------
# relatorio
# --------------------------------------------------------------------------


@dataclass
class Report:
    symbol: str
    timeframe: str
    window_start: str
    window_end: str
    structural_anchor: str
    final_trend: str
    swing_lookback: int
    timeline: list[dict] = field(default_factory=list)
    decompositions: list[dict] = field(default_factory=list)
    crash_snapshots: list[dict] = field(default_factory=list)
    recovery_snapshots: list[dict] = field(default_factory=list)
    passes: dict = field(default_factory=dict)
    shocks: list[dict] = field(default_factory=list)
    stall_d1: dict = field(default_factory=dict)


def build(
    symbol: str = CASE_SYMBOL,
    timeframe: TimeFrame = CASE_TIMEFRAME,
    since: datetime | None = None,
    replay_tail: int = REPLAY_TAIL,
) -> Report:
    run = production_run(symbol, timeframe)
    candles = run.candles
    by_ts = index_by_timestamp(candles)
    if since is None:
        since = candles[-replay_tail].timestamp

    replay_start = max(0, len(run.buffered_candles) - replay_tail)
    first_seen = scan_first_emissions(
        list(run.buffered_candles),
        symbol=symbol,
        timeframe=timeframe,
        limit=LIMIT,
        warmup=replay_start,
        confluence_filter=True,
    )
    # `first_seen` indexa a serie BUFFERIZADA; a janela visivel e o rabo dela.
    offset = len(run.buffered_candles) - len(candles)
    visible_first_seen = {k: v - offset for k, v in first_seen.items() if v - offset >= 0}
    visible_replay_start = replay_start - offset

    rows = timeline(run.events, candles, visible_first_seen, visible_replay_start, since)

    decompositions = [
        decompose(
            event,
            candles,
            by_ts,
            visible_first_seen.get((event.timestamp, event.event, event.direction)),
        )
        for event in run.events
        if event.timestamp >= since and event.event in ADVANCES and not event.provisional
    ]

    since_index = by_ts.get(since, 0)
    report = Report(
        symbol=symbol,
        timeframe=timeframe.value,
        window_start=candles[0].timestamp.isoformat(),
        window_end=candles[-1].timestamp.isoformat(),
        structural_anchor=run.structural_anchor.isoformat(),
        final_trend=run.trend.value,
        swing_lookback=SWING_LOOKBACK,
        timeline=[asdict(r) for r in rows],
        decompositions=[asdict(d) for d in decompositions],
        passes=pass_diff(run, since),
        shocks=[asdict(s) for s in displacement_shocks(candles, since_index=since_index)],
    )
    series = load_series(symbol, timeframe)
    if symbol == CASE_SYMBOL:
        report.crash_snapshots = [
            asdict(s) for s in snapshots(symbol, timeframe, series, CRASH_CANDLE)
        ]
        report.recovery_snapshots = [
            asdict(s) for s in snapshots(symbol, timeframe, series, RECOVERY_CANDLE)
        ]
    report.stall_d1 = {
        sym: [
            asdict(c)
            for c in stall_scan(
                sym,
                openers=(StructureEvent.BREAK_OF_STRUCTURE, StructureEvent.CHANGE_OF_CHARACTER),
            )
        ]
        for sym in D1_SYMBOLS
        if (CACHE_DIR / f"{sym}_{timeframe.value}.json").exists()
    }
    return report


# --------------------------------------------------------------------------
# 12/15. shock x atraso, na populacao
# --------------------------------------------------------------------------


@dataclass
class PopulationCase:
    """Um CHoCH e o atraso com que ele chegou, para o teste de correlacao."""

    symbol: str
    timestamp: str
    direction: str
    lag_bars: int | None
    move_done_pct_at_emission: float | None
    move_total_pct: float | None
    pivot_confirmation_lag: int | None
    break_wait_lag: int | None
    dating_lag: int | None
    emission_lag: int | None
    #: Maior deslocamento em ATR (n=3) entre o inicio do movimento e o evento.
    peak_shock_atr: float | None
    #: Velocidade media do movimento, em ATR por vela.
    speed_atr_per_bar: float | None
    #: Quantos pivos o detector teve para trabalhar entre o extremo e o evento.
    pivots_in_move: int | None


def population(
    symbols: Sequence[str] = D1_SYMBOLS,
    timeframe: TimeFrame = TimeFrame.D1,
    replay_tail: int = REPLAY_TAIL,
) -> list[PopulationCase]:
    """Todos os CHoCH recentes dos simbolos D1, com atraso e velocidade.

    E o unico teste honesto de "shock causa atraso": nove eventos de um
    simbolo nao separam mecanismo de coincidencia.
    """
    out: list[PopulationCase] = []
    for symbol in symbols:
        if not (CACHE_DIR / f"{symbol}_{timeframe.value}.json").exists():
            continue
        run = production_run(symbol, timeframe)
        candles = run.candles
        by_ts = index_by_timestamp(candles)
        replay_start = max(0, len(run.buffered_candles) - replay_tail)
        first_seen = scan_first_emissions(
            list(run.buffered_candles),
            symbol=symbol,
            timeframe=timeframe,
            limit=LIMIT,
            warmup=replay_start,
            confluence_filter=True,
        )
        offset = len(run.buffered_candles) - len(candles)
        seen = {k: v - offset for k, v in first_seen.items() if v - offset > replay_start - offset}
        since = candles[-replay_tail].timestamp
        pivots = [
            e
            for e in run.events
            if e.event
            in (
                StructureEvent.HIGHER_HIGH,
                StructureEvent.HIGHER_LOW,
                StructureEvent.LOWER_HIGH,
                StructureEvent.LOWER_LOW,
            )
        ]
        for event in run.events:
            if (
                event.provisional
                or event.event is not StructureEvent.CHANGE_OF_CHARACTER
                or event.timestamp < since
            ):
                continue
            emitted = seen.get((event.timestamp, event.event, event.direction))
            d = decompose(event, candles, by_ts, emitted)
            start, end = d.move_start_index, d.event_index
            peak = speed = None
            if start is not None and end is not None and end > start:
                bearish = event.direction is MarketDirection.BEARISH
                values = []
                for index in range(start + 3, end + 1):
                    atr = atr_at(candles, index - 3)
                    if atr <= 0:
                        continue
                    move = candles[index].close - candles[index - 3].close
                    values.append((-move if bearish else move) / atr)
                peak = max(values) if values else None
                span_atr = atr_at(candles, start)
                if span_atr > 0:
                    speed = (
                        abs(candles[end].close - d.move_start_price) / span_atr / (end - start)
                    )
            out.append(
                PopulationCase(
                    symbol=symbol,
                    timestamp=event.timestamp.isoformat(),
                    direction=event.direction.value,
                    lag_bars=d.structural_lag_bars_extreme,
                    move_done_pct_at_emission=d.move_done_pct_at_emission,
                    move_total_pct=d.move_total_pct,
                    pivot_confirmation_lag=d.pivot_confirmation_lag,
                    break_wait_lag=d.break_wait_lag,
                    dating_lag=d.dating_lag,
                    emission_lag=d.emission_lag,
                    peak_shock_atr=peak,
                    speed_atr_per_bar=speed,
                    pivots_in_move=(
                        sum(1 for p in pivots if start is not None and end is not None
                            and start <= by_ts.get(p.timestamp, -1) <= end)
                        if start is not None and end is not None
                        else None
                    ),
                )
            )
    return out


def day(value: str | None) -> str:
    """`2026-06-04T00:00:00+00:00` -> `2026-06-04`, e `None` -> `-`."""
    return value[:10] if value else "-"


def num(value: float | None, digits: int = 1) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def median(values: Sequence[float]) -> float | None:
    ordered = sorted(v for v in values if v is not None)
    return ordered[len(ordered) // 2] if ordered else None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default=CASE_SYMBOL)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    report = build(args.symbol)
    print(f"{report.symbol} {report.timeframe}  janela {report.window_start[:10]} "
          f"-> {report.window_end[:10]}  ancora {report.structural_anchor[:10]}  "
          f"trend {report.final_trend}  swing_lookback={report.swing_lookback}")

    print("\n== TIMELINE (advances) ==")
    for row in report.timeline:
        if row["event"] not in {e.value for e in ADVANCES}:
            continue
        emitted = row["emitted_timestamp"]
        tag = " [horizonte]" if row["emission_truncated"] else ""
        print(
            f"  {row['timestamp'][:10]} {row['event']:20s} {row['direction']:8s} "
            f"@{row['price_level']:9.2f} ref={row['reference_price_level']} "
            f"trend {row['trend_before']}->{row['trend_after']} "
            f"emitido={emitted[:10] if emitted else '?'} (+{row['emission_lag_bars']}){tag}"
        )

    print("\n== DECOMPOSICAO ==")
    for d in report.decompositions:
        print(f"  {day(d['timestamp'])} {d['event']} {d['direction']}")
        print(
            f"    movimento comecou {day(d['move_start_timestamp'])} "
            f"@{d['move_start_price']}  |  shock {day(d['shock_timestamp'])}"
        )
        print(
            f"    ref formada {day(d['ref_formed_timestamp'])} -> confirmada "
            f"{day(d['ref_confirmed_timestamp'])} (+{d['pivot_confirmation_lag']})"
        )
        print(
            f"    close-break {day(d['close_break_timestamp'])} "
            f"(espera +{d['break_wait_lag']}) -> datado {day(d['timestamp'])} "
            f"(+{d['dating_lag']}) -> emitido {day(d['emitted_timestamp'])} "
            f"(+{d['emission_lag']})"
        )
        print(
            f"    LAG: {d['structural_lag_bars_extreme']} velas / "
            f"{num(d['structural_lag_atr_extreme'])} ATR do extremo | "
            f"{d['structural_lag_bars_shock']} velas / "
            f"{num(d['structural_lag_atr_shock'])} ATR do shock"
        )
        print(
            f"    movimento total {num(d['move_total_pct'])}% | ja percorrido: "
            f"no evento {num(d['move_done_pct_at_event'])}% | "
            f"na emissao {num(d['move_done_pct_at_emission'])}%"
        )

    print("\n== PASSES ==")
    print(f"  cru={report.passes['raw_count']} final={report.passes['final_count']}")
    for key in report.passes["dropped_by_passes"]:
        print(f"  - descartado pelos passes: {key}")
    for key in report.passes["added_by_passes"]:
        print(f"  + adicionado pelos passes: {key}")
    for item in report.passes["rewritten"]:
        print(f"  ~ reescrito: {item}")

    print("\n== DISPLACEMENT SHOCKS (n=1..5, 3..10 ATR) ==")
    for shock in report.shocks:
        print(
            f"  {shock['timestamp'][:10]} n={shock['n']} >={shock['threshold']:.0f}ATR "
            f"({shock['atr_moved']:.1f}) {shock['direction']} "
            f"{shock['close_from']:.2f} -> {shock['close_to']:.2f}"
        )

    if report.crash_snapshots:
        print("\n== SNAPSHOTS: QUEDA VERTICAL (2026-06-04) ==")
        for s_ in report.crash_snapshots:
            print(
                f"  {s_['offset']:+3d} {s_['timestamp'][:10]} close={s_['close']:8.2f} "
                f"trend={s_['trend']:8s} ultimo_advance={s_['last_advance']} "
                f"({s_['last_advance_timestamp'] and s_['last_advance_timestamp'][:10]}) "
                f"ref={s_['last_reference_price_level']} stall={bool(s_['stall'])}"
            )
            print(f"       BOS: {s_['last_bos']}\n       CHoCH: {s_['last_choch']}")
        print("\n== SNAPSHOTS: FUNDO / RECUPERACAO (2026-06-28) ==")
        for s_ in report.recovery_snapshots:
            print(
                f"  {s_['offset']:+3d} {s_['timestamp'][:10]} close={s_['close']:8.2f} "
                f"trend={s_['trend']:8s} ultimo_advance={s_['last_advance']} "
                f"({s_['last_advance_timestamp'] and s_['last_advance_timestamp'][:10]}) "
                f"ref={s_['last_reference_price_level']} stall={bool(s_['stall'])}"
            )
            print(f"       BOS: {s_['last_bos']}\n       CHoCH: {s_['last_choch']}")

    print("\n== STRUCTURAL STALL NO D1 (N=50, K=6) ==")
    for sym, cases in report.stall_d1.items():
        bos_cases = [c for c in cases if c["opener_event"] == "break_of_structure"]
        choch_cases = [c for c in cases if c["opener_event"] == "change_of_character"]
        cases = bos_cases
        fired = [c for c in cases if c["bars_to_stall"] is not None]
        alive = [
            c for c in fired
            if c["bars_to_next_advance"] is None
            or c["bars_to_stall"] < c["bars_to_next_advance"]
        ]
        bars = sorted(c["bars_to_stall"] for c in fired)
        mid = bars[len(bars) // 2] if bars else None
        print(
            f"  {sym:9s} BOS={len(cases):3d} com gatilho={len(fired):3d} "
            f"antes do proximo advance={len(alive):3d} mediana bars_to_stall={mid}"
        )
        for c in alive:
            print(
                f"      opener {c['opener_timestamp'][:10]} {c['opener_direction']:8s} "
                f"STALE em {c['stale_since'][:10]} (+{c['bars_to_stall']} dias, "
                f"{c['retracement_atr']:.1f} ATR, "
                f"{num(c['move_done_pct'], 0)}% do movimento ja feito)"
            )
        invisible = [
            c
            for c in choch_cases
            if c["bars_to_stall"] is not None
            and (
                c["bars_to_next_advance"] is None
                or c["bars_to_stall"] < c["bars_to_next_advance"]
            )
        ]
        for c in invisible:
            print(
                f"      [invisivel ao guard] perna aberta por CHoCH "
                f"{c['opener_timestamp'][:10]} {c['opener_direction']:8s} ficaria STALE em "
                f"{c['stale_since'][:10]} (+{c['bars_to_stall']} dias, "
                f"{c['retracement_atr']:.1f} ATR)"
            )

    print("\n== POPULACAO D1: SHOCK x ATRASO (CHoCH) ==")
    cases = population()
    graded = [c for c in cases if c.peak_shock_atr is not None]
    with_shock = [c for c in graded if c.peak_shock_atr >= SHOCK_ATR]
    without = [c for c in graded if c.peak_shock_atr < SHOCK_ATR]
    for label, group in (("com shock >=3ATR", with_shock), ("sem shock", without)):
        print(
            f"  {label:18s} n={len(group):3d}  "
            f"mediana movimento_ja_feito_na_emissao="
            f"{num(median([c.move_done_pct_at_emission for c in group]))}%  "
            f"lag={median([c.lag_bars for c in group])} velas  "
            f"pivos_no_movimento={median([c.pivots_in_move for c in group])}"
        )
    print("  fases (mediana, todas as CHoCH):")
    print(
        f"    confirmacao de pivo={median([c.pivot_confirmation_lag for c in cases])}  "
        f"espera de rompimento={median([c.break_wait_lag for c in cases])}  "
        f"datacao={median([c.dating_lag for c in cases])}  "
        f"emissao={median([c.emission_lag for c in cases])}"
    )
    report_cases = [asdict(c) for c in cases]

    if args.json:
        payload = asdict(report)
        payload["population"] = report_cases
        args.json.write_text(json.dumps(payload, indent=1))
        print(f"\n-> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
