"""Etapa 4.3: o CHoCH conseguiu estabelecer a nova tendencia?

A Etapa 4.1 mediu "todo CHoCH confirmado abre perna de StructuralStall" e
**reprovou**: os 142 stalls novos do painel amplo tinham `quick_resume_40` de
22% contra 8% do controle de BOS -- a perna ainda estava viva quando o STALE
dizia que nao. A Etapa 4.2 mediu o eixo temporal e mostrou que N=50 ja e
~1,7 intervalos estruturais tipicos em todo timeframe: nao ha escala errada
para corrigir.

Sobra a hipotese desta etapa, que e sobre a PERNA e nao sobre o relogio:

    nem toda perna aberta por CHoCH deveria ser elegivel para STALE.

Um CHoCH faz duas coisas ao mesmo tempo -- fecha a perna anterior e abre uma
nova. Quando a nova direcao produz estrutura propria (um BOS de continuacao),
a perna existe de verdade e chamar de "parada" cedo demais e o erro que a 4.1
mediu. Quando a nova direcao nunca produz estrutura nenhuma, o que ficou em
vigor foi um bias sem perna -- e e esse caso, e so ele, que o ZEC D1 exibe.

Alvo (retrospectivo, NUNCA feature)
===================================

- ESTABLISHED    -- o proximo advance da perna e um BOS nao-provisional da
                    MESMA direcao do CHoCH;
- UNESTABLISHED  -- a perna e encerrada por outra coisa (CHoCH oposto,
                    `CHOCH_FAILED`, BOS oposto) ou a janela acaba antes.

Como qualquer advance encerra a perna (`_advance_boundaries`), "produzir um
BOS de continuacao" e exatamente "o proximo advance e um BOS meu". Os alvos
auxiliares `failed_to_establish_40/80` sao a mesma pergunta com prazo.

Features (todas causais)
========================

Toda feature e calculada sobre `candles[:cut + 1]` e sobre os eventos com
timestamp <= `candles[cut]`, onde `cut` e o candle de decisao. Nenhuma le uma
vela alem dele. Ha dois `cut` na medicao, e eles respondem perguntas
diferentes:

- `cut = opener + H` (H = 20, 40): o retrato do ciclo de vida. Restrito as
  pernas que em H ainda NAO provaram nada -- porque so essas admitem gate.
- `cut = stale_since`: o ponto onde o gate da secao 14 seria consultado. Ali
  `bars_since_advance >= 50` por construcao, entao *nenhuma* perna provou
  nada, e o alvo vira literalmente o `quick_resume` que condenou a 4.1.

Secao 8 do pedido: espera sozinha e tautologia (`no_bos_for >= M` e o proprio
N do stall). Nenhuma regra aqui usa tempo isolado -- o tempo entra so como o
N=50 que ja existe, e a condicao nova tem de ser sobre a QUALIDADE da perna.

Nao toca producao. Nao altera `detect_structural_stall`, CHoCH, N, K nem o
frontend: le o mesmo stream composto e mede.

    poetry run python -m research.choch_establishment
    poetry run python -m research.choch_establishment --expanded --json \
        research/choch_establishment_baseline.json
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
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
from liquidity_hunter.liquidity.structural_stall import frozen_atr_pct
from research.choch_leg_opener import (
    BOS_ONLY,
    BOS_OR_CHOCH,
    DISCOVERY_FRACTION,
    QUICK,
    SYMBOLS,
    TIMEFRAMES,
    WINDOWS,
    advance_indices,
    detect_stall,
    expanded_symbols,
)
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series

#: Horizontes de decisao para o retrato do ciclo de vida (secao 9). O de 10
#: existe para a secao 11: uma perna que produz BOS em 14 velas (a p25) nunca
#: chega a h20, entao comparar "cedo" com "nunca" em h20 compara amostra
#: nenhuma -- e foi exatamente o que aconteceu na primeira rodada.
HORIZONS = (10, 20, 40, 80)


def swing_lookback_of(timeframe: TimeFrame) -> int:
    """O `swing_lookback` que a producao usa naquele timeframe."""
    return dd._INTERNAL_STRUCTURE_PARAMS[timeframe][0]
#: Pivos de estrutura menor -- nao sao advances, mas contam como "a nova
#: direcao esta produzindo alguma coisa".
PIVOTS = frozenset(
    {
        StructureEvent.HIGHER_HIGH,
        StructureEvent.HIGHER_LOW,
        StructureEvent.LOWER_HIGH,
        StructureEvent.LOWER_LOW,
    }
)
#: Os pivos coerentes com cada direcao.
COHERENT = {
    MarketDirection.BULLISH: frozenset(
        {StructureEvent.HIGHER_HIGH, StructureEvent.HIGHER_LOW}
    ),
    MarketDirection.BEARISH: frozenset(
        {StructureEvent.LOWER_HIGH, StructureEvent.LOWER_LOW}
    ),
}

#: O caso obrigatorio da secao 10.
ZEC_CASE = ("ZECUSDT", TimeFrame.D1, "2026-06-04T00:00:00+00:00")


# --------------------------------------------------------------------------
# features causais
# --------------------------------------------------------------------------


@dataclass
class Features:
    """O que da para saber sobre a perna nova em `cut`, sem olhar adiante."""

    bars: int
    #: MFE/MAE em ATR congelado no opener, medidos em CLOSE (mesma base do
    #: `detect_structural_stall`, que le closes justamente para que um pavio
    #: de caca nao decida nada).
    mfe_atr: float
    mae_atr: float
    mfe_mae: float | None
    #: Deslocamento liquido (close do cut contra o close do opener), em ATR.
    net_atr: float
    #: Deslocamento liquido por candle -- "eficiencia".
    efficiency_atr: float
    #: Quanto do MFE ja foi devolvido, em fracao do proprio MFE.
    giveback_of_mfe: float | None
    #: Pivos formados depois do CHoCH, no total e os coerentes com a direcao.
    pivots: int
    coherent_pivots: int
    incoherent_pivots: int
    sweeps: int
    #: Distancia do close atual ao nivel que precisaria romper para o primeiro
    #: BOS de continuacao (o ultimo pivo coerente do lado do avanco), em ATR.
    #: `None` quando nenhum pivo desses existe ainda -- que ja e informacao.
    distance_to_bos_atr: float | None
    #: Extensao alem do proprio nivel do CHoCH, em ATR (negativo = o preco
    #: voltou para dentro do nivel que o CHoCH rompeu).
    extension_atr: float
    #: Primeiro impulso: candles consecutivos fechando na direcao, e o que
    #: eles deslocaram.
    impulse_bars: int
    impulse_atr: float
    #: Primeiro pullback depois do primeiro extremo: profundidade e duracao.
    pullback_atr: float | None
    pullback_bars: int | None
    #: O pullback rompeu a origem do CHoCH (o swing de onde a perna saiu)?
    pullback_broke_origin: bool | None
    #: O preco voltou a superar o extremo pre-pullback depois dele?
    resumed_after_pullback: bool | None


def swing_level(
    candles: Sequence[Candle],
    opener_index: int,
    cut: int,
    lookback: int,
    direction: MarketDirection,
) -> float | None:
    """Ultimo swing CONFIRMADO depois do CHoCH -- o nivel do proximo BOS.

    Uma perna bearish so produz BOS quando rompe um fundo; uma bullish, um
    topo. Esses niveis NAO estao no stream de eventos: o detector emite
    `higher_low` e `lower_high` (os pivos de pullback) e nunca `lower_low` /
    `higher_high` -- verificado no proprio stream. Entao o nivel e derivado
    aqui, com o mesmo `swing_lookback` da producao e a mesma regra de
    confirmacao (um pivo em `i` so existe depois de `i + lookback`), o que
    mantem a leitura causal: em `cut` so contam pivos ja confirmados ali.
    """
    if lookback <= 0:
        return None
    bearish = direction is MarketDirection.BEARISH
    level = None
    for index in range(opener_index + lookback, cut - lookback + 1):
        window = candles[index - lookback : index + lookback + 1]
        if bearish:
            if candles[index].low == min(candle.low for candle in window):
                level = candles[index].low
        elif candles[index].high == max(candle.high for candle in window):
            level = candles[index].high
    return level


def features_at(
    candles: Sequence[Candle],
    events: Sequence[MarketStructure],
    opener: MarketStructure,
    opener_index: int,
    cut: int,
    atr: float,
    lookback: int,
) -> Features | None:
    """Tudo o que a secao 3-7 pede, sobre `candles[opener_index:cut + 1]`."""
    if cut <= opener_index or atr <= 0 or opener.price_level <= 0:
        return None
    entry = opener.price_level
    bullish = opener.direction is MarketDirection.BULLISH
    sign = 1.0 if bullish else -1.0
    base = candles[opener_index].close
    unit = entry * atr

    window = candles[opener_index : cut + 1]
    closes = [candle.close for candle in window]
    favourable = max((sign * (close - base) / unit for close in closes), default=0.0)
    adverse = max((-sign * (close - base) / unit for close in closes), default=0.0)
    mfe = max(favourable, 0.0)
    mae = max(adverse, 0.0)
    net = sign * (closes[-1] - base) / unit

    # Primeiro impulso: candles consecutivos, a partir do opener, cujo close
    # anda na direcao do CHoCH.
    impulse_bars = 0
    for index in range(opener_index + 1, cut + 1):
        if sign * (candles[index].close - candles[index - 1].close) > 0:
            impulse_bars += 1
        else:
            break
    impulse_end = opener_index + impulse_bars
    impulse = sign * (candles[impulse_end].close - base) / unit

    # Primeiro pullback: do extremo do impulso ate o candle em que o preco
    # volta a superar aquele extremo (ou o fim da janela).
    pullback_atr: float | None = None
    pullback_bars: int | None = None
    pullback_broke_origin: bool | None = None
    resumed_after: bool | None = None
    if impulse_bars > 0 and impulse_end < cut:
        peak = candles[impulse_end].close
        depth = 0.0
        end = cut
        resumed_after = False
        for index in range(impulse_end + 1, cut + 1):
            close = candles[index].close
            depth = max(depth, -sign * (close - peak) / unit)
            if sign * (close - peak) > 0:
                end = index
                resumed_after = True
                break
        pullback_atr = depth
        pullback_bars = end - impulse_end
        origin = opener.origin_price_level
        if origin is not None and origin > 0:
            lows = [candle.low for candle in candles[impulse_end : end + 1]]
            highs = [candle.high for candle in candles[impulse_end : end + 1]]
            pullback_broke_origin = (
                max(highs) > origin if bullish else min(lows) < origin
            )

    upto = candles[cut].timestamp
    since = opener.timestamp
    pivots = coherent = incoherent = sweeps = 0
    wanted = COHERENT[opener.direction]
    for event in events:
        if event.provisional or not (since < event.timestamp <= upto):
            continue
        if event.event is StructureEvent.LIQUIDITY_SWEEP:
            sweeps += 1
        elif event.event in PIVOTS:
            pivots += 1
            if event.event in wanted:
                coherent += 1
            else:
                incoherent += 1

    level = swing_level(candles, opener_index, cut, lookback, opener.direction)
    distance = None if level is None else abs(closes[-1] - level) / unit

    return Features(
        bars=cut - opener_index,
        mfe_atr=round(mfe, 3),
        mae_atr=round(mae, 3),
        mfe_mae=round(mfe / mae, 3) if mae > 0 else None,
        net_atr=round(net, 3),
        efficiency_atr=round(net / (cut - opener_index), 4),
        giveback_of_mfe=round((mfe - net) / mfe, 3) if mfe > 0 else None,
        pivots=pivots,
        coherent_pivots=coherent,
        incoherent_pivots=incoherent,
        sweeps=sweeps,
        distance_to_bos_atr=None if distance is None else round(distance, 3),
        extension_atr=round(sign * (closes[-1] - entry) / unit, 3),
        impulse_bars=impulse_bars,
        impulse_atr=round(impulse, 3),
        pullback_atr=None if pullback_atr is None else round(pullback_atr, 3),
        pullback_bars=pullback_bars,
        pullback_broke_origin=pullback_broke_origin,
        resumed_after_pullback=resumed_after,
    )


# --------------------------------------------------------------------------
# a perna
# --------------------------------------------------------------------------


@dataclass
class ChochLeg:
    """Uma perna aberta por CHoCH: alvo retrospectivo + features causais."""

    symbol: str
    timeframe: str
    window: int
    opener_timestamp: str
    opener_index: int
    direction: str
    reference_structural: bool | None
    leg_bars: int
    closed_by: str | None
    closed_by_direction: str | None
    bars_to_close: int | None
    frozen_atr: float
    # --- alvos (retrospectivos) ---
    established: bool
    bars_to_first_bos: int | None
    displacement_to_first_bos_atr: float | None
    failed_40: bool
    failed_80: bool
    # --- features por horizonte, e no candle do STALE ---
    at: dict[str, dict] = field(default_factory=dict)
    # --- o STALE que a 4.1 emitiria nesta perna ---
    stale_since: str | None = None
    stale_bars: int | None = None
    stale_retracement_atr: float | None = None
    outcome: str | None = None
    bars_to_outcome: int | None = None


def choch_legs(run, symbol: str, timeframe: TimeFrame, window: int) -> list[ChochLeg]:
    """Toda perna aberta por CHoCH nao-provisional na janela."""
    candles = run.candles
    by_ts = {candle.timestamp: index for index, candle in enumerate(candles)}
    advances = advance_indices(run.events, by_ts)
    out: list[ChochLeg] = []

    for position, (opener, index) in enumerate(advances):
        if opener.event is not StructureEvent.CHANGE_OF_CHARACTER:
            continue
        following = advances[position + 1] if position + 1 < len(advances) else None
        # Mesma janela da Etapa 4.1: a perna termina na vela ANTERIOR ao
        # proximo advance, que e onde `_last_advance` ainda devolve o opener.
        end = following[1] - 1 if following else len(candles) - 1
        if end <= index:
            continue
        atr = frozen_atr_pct(candles, index)
        if atr <= 0 or opener.price_level <= 0:
            continue
        lookback = swing_lookback_of(timeframe)

        established = False
        bars_to_bos: int | None = None
        displacement: float | None = None
        if following is not None:
            nxt, nxt_index = following
            if (
                nxt.event is StructureEvent.BREAK_OF_STRUCTURE
                and nxt.direction is opener.direction
            ):
                established = True
                bars_to_bos = nxt_index - index
                displacement = abs(
                    candles[nxt_index].close - candles[index].close
                ) / opener.price_level / atr

        leg = ChochLeg(
            symbol=symbol,
            timeframe=timeframe.value,
            window=window,
            opener_timestamp=opener.timestamp.isoformat(),
            opener_index=index,
            direction=opener.direction.value,
            reference_structural=opener.reference_structural,
            leg_bars=end - index,
            closed_by=following[0].event.value if following else None,
            closed_by_direction=following[0].direction.value if following else None,
            bars_to_close=following[1] - index if following else None,
            frozen_atr=round(atr, 6),
            established=established,
            bars_to_first_bos=bars_to_bos,
            displacement_to_first_bos_atr=(
                None if displacement is None else round(displacement, 3)
            ),
            failed_40=not (established and bars_to_bos is not None and bars_to_bos <= 40),
            failed_80=not (established and bars_to_bos is not None and bars_to_bos <= 80),
        )

        # Features nos horizontes fixos. So existem quando a janela chega la;
        # `censored` marca que a perna acabou antes -- nunca e preenchido com
        # um valor inventado.
        for horizon in HORIZONS:
            cut = index + horizon
            if cut > end:
                continue
            snap = features_at(candles, run.events, opener, index, cut, atr, lookback)
            if snap is not None:
                leg.at[f"h{horizon}"] = asdict(snap)

        stall = detect_stall(
            candles[: end + 1],
            [e for e in run.events if e.timestamp <= candles[end].timestamp],
            openers=BOS_OR_CHOCH,
        )
        if stall is not None and stall.last_advance_timestamp == opener.timestamp:
            trigger = by_ts[stall.stale_since]
            leg.stale_since = stall.stale_since.isoformat()
            leg.stale_bars = stall.bars_since_advance
            leg.stale_retracement_atr = round(stall.retracement_atr, 3)
            snap = features_at(candles, run.events, opener, index, trigger, atr, lookback)
            if snap is not None:
                leg.at["stale"] = asdict(snap)
            leg.outcome, leg.bars_to_outcome = _outcome(
                run.events, by_ts, trigger, opener.direction
            )
        out.append(leg)
    return out


def _outcome(
    events: Sequence[MarketStructure],
    by_ts: dict[datetime, int],
    trigger: int,
    direction: MarketDirection,
) -> tuple[str, int | None]:
    """Desfecho do STALE: primeiro BOS depois do gatilho (avaliacao)."""
    for event in events:
        if event.provisional or event.event is not StructureEvent.BREAK_OF_STRUCTURE:
            continue
        index = by_ts.get(event.timestamp)
        if index is None or index <= trigger:
            continue
        return ("resumed" if event.direction is direction else "reversed"), index - trigger
    return "open", None


# --------------------------------------------------------------------------
# coleta
# --------------------------------------------------------------------------


def collect(
    symbols: Sequence[str] = SYMBOLS,
    timeframes: Sequence[TimeFrame] = TIMEFRAMES,
    windows: int = WINDOWS,
    limit: int = LIMIT,
) -> tuple[list[ChochLeg], list[dict]]:
    """As pernas de CHoCH, e o controle de BOS (secao 14, populacao A)."""
    legs: list[ChochLeg] = []
    bos_stalls: list[dict] = []
    for symbol in symbols:
        for timeframe in timeframes:
            if not (CACHE_DIR / f"{symbol}_{timeframe.value}.json").exists():
                continue
            try:
                series = load_series(symbol, timeframe)
            except Exception as error:  # noqa: BLE001 - vela corrompida no cache
                print(f"! cache invalido: {symbol} {timeframe.value}: {error}")
                continue
            for window in range(windows):
                end = len(series) - window * limit
                start = end - limit - BUFFER
                if start < 0:
                    break
                try:
                    run = dd._run_internal_structure(
                        provider=SliceProvider(series[start:end]),
                        symbol=symbol,
                        timeframe=timeframe,
                        limit=limit,
                        confluence_filter=True,
                    )
                except Exception as error:  # noqa: BLE001 - feed morto
                    print(f"! janela invalida: {symbol} {timeframe.value} w{window}: {error}")
                    continue
                legs.extend(choch_legs(run, symbol, timeframe, window))
                bos_stalls.extend(_bos_control(run, symbol, timeframe, window))
    return legs, bos_stalls


def _bos_control(run, symbol: str, timeframe: TimeFrame, window: int) -> list[dict]:
    """Os STALE de hoje (guard BOS-only), so para a comparacao da secao 14."""
    candles = run.candles
    by_ts = {candle.timestamp: index for index, candle in enumerate(candles)}
    advances = advance_indices(run.events, by_ts)
    out = []
    for position, (opener, index) in enumerate(advances):
        if opener.event is not StructureEvent.BREAK_OF_STRUCTURE:
            continue
        following = advances[position + 1] if position + 1 < len(advances) else None
        end = following[1] - 1 if following else len(candles) - 1
        if end <= index:
            continue
        stall = detect_stall(
            candles[: end + 1],
            [e for e in run.events if e.timestamp <= candles[end].timestamp],
            openers=BOS_ONLY,
        )
        if stall is None or stall.last_advance_timestamp != opener.timestamp:
            continue
        trigger = by_ts[stall.stale_since]
        outcome, bars = _outcome(run.events, by_ts, trigger, opener.direction)
        out.append(
            {
                "symbol": symbol,
                "timeframe": timeframe.value,
                "window": window,
                "opener_timestamp": opener.timestamp.isoformat(),
                "outcome": outcome,
                "bars_to_outcome": bars,
            }
        )
    return out


# --------------------------------------------------------------------------
# estatistica
# --------------------------------------------------------------------------


def auc(positive: Sequence[float], negative: Sequence[float]) -> float | None:
    """AUC de Mann-Whitney: P(x_pos > x_neg) + 0.5 P(empate).

    0.5 = a feature nao separa nada. Reportada sem correcao de multiplos
    testes de proposito -- ela nao decide sozinha; o holdout decide.
    """
    if not positive or not negative:
        return None
    values = sorted([(v, 1) for v in positive] + [(v, 0) for v in negative])
    ranks: list[float] = [0.0] * len(values)
    index = 0
    while index < len(values):
        stop = index
        while stop + 1 < len(values) and values[stop + 1][0] == values[index][0]:
            stop += 1
        mean_rank = (index + stop) / 2 + 1
        for position in range(index, stop + 1):
            ranks[position] = mean_rank
        index = stop + 1
    rank_sum = sum(rank for rank, (_, label) in zip(ranks, values, strict=False) if label)
    n_pos, n_neg = len(positive), len(negative)
    return (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def quantiles(values: Sequence[float]) -> tuple[float, float, float]:
    """p25 / mediana / p75, tolerante a amostra minuscula."""
    if not values:
        return (math.nan, math.nan, math.nan)
    ordered = sorted(values)
    if len(ordered) < 4:
        median = statistics.median(ordered)
        return (ordered[0], median, ordered[-1])
    return (
        statistics.quantiles(ordered, n=4)[0],
        statistics.median(ordered),
        statistics.quantiles(ordered, n=4)[2],
    )


NUMERIC = (
    "mfe_atr",
    "mae_atr",
    "mfe_mae",
    "net_atr",
    "efficiency_atr",
    "giveback_of_mfe",
    "pivots",
    "coherent_pivots",
    "incoherent_pivots",
    "sweeps",
    "distance_to_bos_atr",
    "extension_atr",
    "impulse_bars",
    "impulse_atr",
    "pullback_atr",
    "pullback_bars",
)


def univariate(legs: Sequence[ChochLeg], slot: str, target: str) -> list[dict]:
    """Secao 12: mediana / p25 / p75 / AUC / overlap por feature.

    `target` e o nome do atributo booleano do alvo. A convencao: positivo =
    o caso que queremos DETECTAR (a perna que nao se estabeleceu).
    """
    rows = []
    usable = [leg for leg in legs if slot in leg.at]
    for name in NUMERIC:
        positive = [
            leg.at[slot][name] for leg in usable
            if getattr(leg, target) and leg.at[slot][name] is not None
        ]
        negative = [
            leg.at[slot][name] for leg in usable
            if not getattr(leg, target) and leg.at[slot][name] is not None
        ]
        if len(positive) < 10 or len(negative) < 10:
            continue
        p_lo, p_mid, p_hi = quantiles(positive)
        n_lo, n_mid, n_hi = quantiles(negative)
        # Overlap: fracao das duas amostras dentro da interseccao dos IQR.
        low, high = max(p_lo, n_lo), min(p_hi, n_hi)
        overlap = 0.0
        if high >= low:
            inside = sum(1 for v in positive + negative if low <= v <= high)
            overlap = inside / (len(positive) + len(negative))
        rows.append(
            {
                "feature": name,
                "n_pos": len(positive),
                "n_neg": len(negative),
                "pos_p25": round(p_lo, 3),
                "pos_median": round(p_mid, 3),
                "pos_p75": round(p_hi, 3),
                "neg_p25": round(n_lo, 3),
                "neg_median": round(n_mid, 3),
                "neg_p75": round(n_hi, 3),
                "auc": round(auc(positive, negative) or 0.5, 3),
                "overlap": round(overlap, 3),
            }
        )
    rows.sort(key=lambda row: abs(row["auc"] - 0.5), reverse=True)
    return rows


# --------------------------------------------------------------------------
# secao 14: as tres populacoes de STALE
# --------------------------------------------------------------------------


def quality(rows: Sequence[dict]) -> dict:
    """qr5..qr80 + mix de desfecho, na definicao da 4.1."""
    total = len(rows)
    if not total:
        return {"n": 0}
    out: dict = {"n": total}
    for name in ("resumed", "reversed", "open"):
        out[name] = round(
            sum(1 for row in rows if row["outcome"] == name) / total * 100, 1
        )
    for horizon in QUICK:
        out[f"qr{horizon}"] = round(
            sum(
                1
                for row in rows
                if row["outcome"] == "resumed"
                and row["bars_to_outcome"] is not None
                and row["bars_to_outcome"] <= horizon
            )
            / total
            * 100,
            1,
        )
    return out


def as_row(leg: ChochLeg) -> dict:
    return {
        "symbol": leg.symbol,
        "timeframe": leg.timeframe,
        "opener_timestamp": leg.opener_timestamp,
        "outcome": leg.outcome,
        "bars_to_outcome": leg.bars_to_outcome,
    }


def gate_passes(leg: ChochLeg, rule: dict) -> bool:
    """A regra da secao 13, avaliada nas features do candle do STALE.

    Uma condicao cujo valor e `None` (a feature nao existe naquele ponto --
    p.ex. nenhum pivo coerente formado ainda) REPROVA a condicao de `<=` e
    APROVA a de `>=` apenas quando o pedido diz explicitamente; aqui a
    convencao e conservadora: `None` nunca satisfaz uma condicao. Feature
    ausente nao vira default silencioso (licao da Etapa 4.2).
    """
    snap = leg.at.get("stale")
    if snap is None:
        return False
    for name, (op, threshold) in rule.items():
        value = snap.get(name)
        if value is None:
            return False
        if op == "<=" and not value <= threshold:
            return False
        if op == ">=" and not value >= threshold:
            return False
    return True


def split_holdout(legs: Sequence[ChochLeg]) -> tuple[list[ChochLeg], list[ChochLeg]]:
    """70% mais antigo / 30% mais recente, POR TIMEFRAME (secao 15).

    O corte tem de ser por timeframe: as janelas de D1 cobrem anos e as de M15
    cobrem semanas, entao um corte global por timestamp joga o diario inteiro
    no discovery e deixa o holdout so com intraday (o erro corrigido na 4.2).
    """
    discovery: list[ChochLeg] = []
    holdout: list[ChochLeg] = []
    by_tf: dict[str, list[ChochLeg]] = {}
    for leg in legs:
        by_tf.setdefault(leg.timeframe, []).append(leg)
    for group in by_tf.values():
        group.sort(key=lambda leg: leg.opener_timestamp)
        cut = int(len(group) * DISCOVERY_FRACTION)
        discovery.extend(group[:cut])
        holdout.extend(group[cut:])
    return discovery, holdout


# --------------------------------------------------------------------------
# secao 10: ZEC D1
# --------------------------------------------------------------------------


def zec_timeline(legs: Sequence[ChochLeg]) -> dict:
    """A perna bearish do ZEC D1 aberta em 2026-06-04, vela a vela nas features."""
    symbol, timeframe, timestamp = ZEC_CASE
    for leg in legs:
        if (
            leg.symbol == symbol
            and leg.timeframe == timeframe.value
            and leg.opener_timestamp == timestamp
        ):
            return asdict(leg)
    return {}


# --------------------------------------------------------------------------
# secao 13: busca de regra (discovery apenas)
# --------------------------------------------------------------------------

#: A grade e deliberadamente pequena: no maximo duas condicoes, sobre as
#: features que a univariada elegeu, com limiares redondos. Uma grade grande
#: sobre 100-odd stalls encontra qualquer coisa (`feedback_measurement_discipline`).
GRIDS = {
    "mfe_atr": ("<=", (2.0, 3.0, 4.0, 6.0)),
    "coherent_pivots": ("<=", (0, 1, 2)),
    "giveback_of_mfe": (">=", (0.8, 1.0, 1.2)),
    "distance_to_bos_atr": (">=", (3.0, 5.0, 8.0)),
    "efficiency_atr": ("<=", (0.0, 0.02, 0.05)),
    "net_atr": ("<=", (0.0, 1.0, 2.0)),
}


def rule_candidates() -> list[dict]:
    """Toda regra de 1 ou 2 condicoes sobre a grade acima."""
    singles = [
        {name: (op, value)}
        for name, (op, values) in GRIDS.items()
        for value in values
    ]
    out = list(singles)
    names = list(GRIDS)
    for i, first in enumerate(names):
        for second in names[i + 1 :]:
            op_a, values_a = GRIDS[first]
            op_b, values_b = GRIDS[second]
            for a in values_a:
                for b in values_b:
                    out.append({first: (op_a, a), second: (op_b, b)})
    return out


def score_rule(legs: Sequence[ChochLeg], rule: dict) -> dict:
    """O que a regra deixa passar, e a qualidade do que passa."""
    stalls = [leg for leg in legs if leg.stale_since]
    kept = [leg for leg in stalls if gate_passes(leg, rule)]
    result = quality([as_row(leg) for leg in kept])
    result["kept_of"] = len(stalls)
    return result


def label(rule: dict) -> str:
    return " AND ".join(f"{name} {op} {value}" for name, (op, value) in rule.items())


# --------------------------------------------------------------------------
# relatorio
# --------------------------------------------------------------------------


def num(value, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def build(symbols: Sequence[str], windows: int) -> dict:
    legs, bos_stalls = collect(symbols=symbols, windows=windows)
    print(
        f"simbolos: {len(symbols)}  pernas de CHoCH: {len(legs)}  "
        f"STALE de BOS: {len(bos_stalls)}"
    )

    report: dict = {"symbols": list(symbols), "n_choch_legs": len(legs)}

    # -- 1/2/3: quantos se estabelecem, e em quanto tempo --
    print("\n== 1/2/3. ESTABLISHED vs UNESTABLISHED ==")
    print(
        f"{'tf':>5} {'pernas':>7} {'estab':>7} {'%':>6} "
        f"{'bars->BOS p25/med/p75':>26} {'fail40':>7} {'fail80':>7}"
    )
    per_tf = {}
    for timeframe in [t.value for t in TIMEFRAMES] + ["TOTAL"]:
        group = legs if timeframe == "TOTAL" else [x for x in legs if x.timeframe == timeframe]
        if not group:
            continue
        established = [x for x in group if x.established]
        bars = [x.bars_to_first_bos for x in established if x.bars_to_first_bos is not None]
        lo, mid, hi = quantiles(bars) if bars else (math.nan,) * 3
        row = {
            "legs": len(group),
            "established": len(established),
            "established_pct": round(len(established) / len(group) * 100, 1),
            "bars_to_bos_p25": None if not bars else round(lo, 1),
            "bars_to_bos_median": None if not bars else round(mid, 1),
            "bars_to_bos_p75": None if not bars else round(hi, 1),
            "failed_40_pct": round(sum(x.failed_40 for x in group) / len(group) * 100, 1),
            "failed_80_pct": round(sum(x.failed_80 for x in group) / len(group) * 100, 1),
        }
        per_tf[timeframe] = row
        print(
            f"{timeframe:>5} {row['legs']:>7} {row['established']:>7} "
            f"{row['established_pct']:>5.1f}% {num(lo,0):>8}/{num(mid,0):>7}/{num(hi,0):>7} "
            f"{row['failed_40_pct']:>6.1f}% {row['failed_80_pct']:>6.1f}%"
        )
    report["establishment"] = per_tf

    # -- 12: univariada --
    report["univariate"] = {}
    for slot in ("h10", "h20", "h40", "stale"):
        target = "established"
        rows = univariate(legs, slot, target)
        if not rows:
            continue
        # positivo = UNESTABLISHED: inverte a AUC, que foi medida com
        # `established` como positivo.
        for row in rows:
            row["auc"] = round(1 - row["auc"], 3)
        rows.sort(key=lambda r: abs(r["auc"] - 0.5), reverse=True)
        report["univariate"][slot] = rows
        print(f"\n== 12. UNIVARIADA em {slot} (positivo = UNESTABLISHED) ==")
        print(
            f"{'feature':>22} {'n+':>5} {'n-':>5} {'med+':>8} "
            f"{'med-':>8} {'AUC':>6} {'overlap':>8}"
        )
        for row in rows[:8]:
            print(
                f"{row['feature']:>22} {row['n_pos']:>5} {row['n_neg']:>5} "
                f"{row['pos_median']:>8.2f} {row['neg_median']:>8.2f} "
                f"{row['auc']:>6.3f} {row['overlap']:>7.0%}"
            )

    # -- 15: holdout + 13: regras --
    discovery, holdout = split_holdout(legs)
    report["split"] = {"discovery": len(discovery), "holdout": len(holdout)}
    raw_disc = quality([as_row(x) for x in discovery if x.stale_since])
    raw_hold = quality([as_row(x) for x in holdout if x.stale_since])
    print("\n== 13/15. REGRAS (discovery) ==")
    print(f"  4.1 bruto discovery: {raw_disc}")
    print(f"  4.1 bruto holdout  : {raw_hold}")

    scored = []
    for rule in rule_candidates():
        result = score_rule(discovery, rule)
        if result["n"] < 15:
            continue
        scored.append((label(rule), rule, result))
    scored.sort(key=lambda item: (item[2]["qr40"], -item[2]["n"]))
    print(f"{'regra':>58} {'n':>4} {'qr20':>6} {'qr40':>6} {'qr80':>6}")
    for name, _, result in scored[:10]:
        print(
            f"{name:>58} {result['n']:>4} {result['qr20']:>5.0f}% "
            f"{result['qr40']:>5.0f}% {result['qr80']:>5.0f}%"
        )
    report["rules_discovery"] = [
        {"rule": name, "result": result} for name, _, result in scored[:20]
    ]
    report["raw_41"] = {"discovery": raw_disc, "holdout": raw_hold}

    # -- 14/15: as tres populacoes, com a regra congelada --
    if scored:
        best_label, best_rule, best_disc = scored[0]
        hold = score_rule(holdout, best_rule)
        print(f"\n== 15. REGRA CONGELADA: {best_label} ==")
        print(f"  discovery: {best_disc}")
        print(f"  holdout  : {hold}")
        report["frozen_rule"] = {
            "rule": best_label,
            "discovery": best_disc,
            "holdout": hold,
        }

        print("\n== 14. A) BOS-only  B) CHoCH bruto (4.1)  C) CHoCH + gate ==")
        a = quality(bos_stalls)
        b = quality([as_row(x) for x in legs if x.stale_since])
        c = quality([as_row(x) for x in legs if x.stale_since and gate_passes(x, best_rule)])
        for name, result in (("A BOS-only", a), ("B CHoCH bruto", b), ("C CHoCH+gate", c)):
            print(
                f"  {name:<14} n={result['n']:>4} resumed={result.get('resumed',0):>5.1f}% "
                f"rev={result.get('reversed',0):>5.1f}% open={result.get('open',0):>5.1f}% "
                f"qr5={result.get('qr5',0):>4.1f}% qr10={result.get('qr10',0):>4.1f}% "
                f"qr20={result.get('qr20',0):>4.1f}% qr40={result.get('qr40',0):>4.1f}% "
                f"qr80={result.get('qr80',0):>4.1f}%"
            )
        report["section14"] = {"A_bos_only": a, "B_choch_raw": b, "C_choch_gated": c}

        print("\n== 14. POR TIMEFRAME (B bruto -> C com gate) ==")
        by_tf = {}
        for timeframe in [t.value for t in TIMEFRAMES]:
            group = [x for x in legs if x.timeframe == timeframe and x.stale_since]
            gated = [x for x in group if gate_passes(x, best_rule)]
            control = quality([r for r in bos_stalls if r["timeframe"] == timeframe])
            raw = quality([as_row(x) for x in group])
            keep = quality([as_row(x) for x in gated])
            by_tf[timeframe] = {"A": control, "B": raw, "C": keep}
            print(
                f"  {timeframe:>4}  A n={control['n']:>4} qr40={control.get('qr40',0):>5.1f}% | "
                f"B n={raw['n']:>3} qr40={raw.get('qr40',0):>5.1f}% | "
                f"C n={keep['n']:>3} qr40={keep.get('qr40',0):>5.1f}%"
            )
        report["section14_by_timeframe"] = by_tf

        zec = zec_timeline(legs)
        report["zec"] = zec
        if zec:
            print("\n== 10. ZEC D1, CHoCH bearish 2026-06-04 ==")
            print(
                f"  established={zec['established']} closed_by={zec['closed_by']} "
                f"leg_bars={zec['leg_bars']} stale_since={zec['stale_since']} "
                f"outcome={zec['outcome']}"
            )
            for slot in ("h10", "h20", "h40", "h80", "stale"):
                snap = zec["at"].get(slot)
                if snap:
                    print(
                        f"  {slot:>5}: bars={snap['bars']:>3} mfe={num(snap['mfe_atr'])} "
                        f"mae={num(snap['mae_atr'])} net={num(snap['net_atr'])} "
                        f"giveback={num(snap['giveback_of_mfe'])} "
                        f"pivos={snap['pivots']} coerentes={snap['coherent_pivots']} "
                        f"sweeps={snap['sweeps']} dist_bos={num(snap['distance_to_bos_atr'])} "
                        f"passa_gate={gate_passes_snap(snap, best_rule)}"
                    )

        # -- 11: controles que deram certo --
        print("\n== 11. CONTROLE: CHoCH que se estabeleceu cedo (<=20 velas) ==")
        early = [
            x for x in legs
            if x.established and x.bars_to_first_bos is not None
            and x.bars_to_first_bos <= 20 and "h10" in x.at
        ]
        never = [x for x in legs if not x.established and "h10" in x.at]
        print(f"  cedo n={len(early)}  nunca n={len(never)}")
        for name in ("mfe_atr", "coherent_pivots", "efficiency_atr", "giveback_of_mfe"):
            e = [x.at["h10"][name] for x in early if x.at["h10"][name] is not None]
            u = [x.at["h10"][name] for x in never if x.at["h10"][name] is not None]
            if e and u:
                print(
                    f"    {name:>18}: cedo med={statistics.median(e):>7.2f}  "
                    f"nunca med={statistics.median(u):>7.2f}"
                )
        report["control_early"] = {"n_early": len(early), "n_never": len(never)}
    return report


def gate_passes_snap(snap: dict, rule: dict) -> bool:
    """`gate_passes` sobre um snapshot ja serializado (para o ZEC)."""
    for name, (op, threshold) in rule.items():
        value = snap.get(name)
        if value is None:
            return False
        if op == "<=" and not value <= threshold:
            return False
        if op == ">=" and not value >= threshold:
            return False
    return True


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--expanded", action="store_true")
    parser.add_argument("--windows", type=int, default=WINDOWS)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    symbols = args.symbols or (expanded_symbols() if args.expanded else SYMBOLS)
    report = build(symbols, args.windows)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\n-> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
