"""Etapa 0.6: "structural stall" vela a vela -- a perna parou, sem dizer para onde vai.

A Etapa 0.5 achou o episodio (`research/expansion_stall.py`): depois de uma
expansao forte a perna deixa de avancar, o preco devolve 8-13 ATR e o ultimo BOS
segue sendo a estrutura vigente por 80-230 velas. Os pares N=60/K=8 de la foram
lidos em valores TERMINAIS do episodio -- olham o futuro, e por isso nao valem
como regra. Aqui a condicao e avaliada vela a vela, causalmente, e a varredura
(N, K) e feita para procurar uma REGIAO estavel, nao um otimo.

O que STALE quer dizer (e o que nao quer)
=========================================

"A perna estrutural vigente nao esta mais avancando e ja devolveu o bastante
para nao ser considerada ativa." Nao e bearish, nao e bullish, nao e reversao,
nao e neutro. Portanto a regra NAO e avaliada pela capacidade de prever a
proxima direcao: um trigger seguido de retomada nao e um erro de direcao, e um
erro de *momento* -- a perna ainda estava viva. E isso que `quick_resume_*`
mede.

Definicoes causais
==================

**Advance**: exatamente a semantica do pipeline --
`app.dashboard_data._advance_boundaries`, os BOS/CHoCH/`CHOCH_FAILED`
nao-provisionais que sobreviveram aos passes de composicao (o que o grafico
desenha). Nada de definicao nova.

**Perna**: comeca num advance que e BOS e vai ate o PROXIMO advance de qualquer
tipo -- um CHoCH encerra a perna por definicao da propria maquina. O trigger so
pode ocorrer dentro dessa janela; o desfecho e lido depois dela.

**Retracao** (`counter_retracement_atr`), bullish (bearish espelhado):

    (extremo_corrente - preco_corrente) / preco_de_entrada / ATR

`extremo_corrente` e o extremo da perna ATE aquela vela (nunca o extremo final
do episodio, que seria lookahead). Duas leituras de preco, ambas medidas:

- `wick`  -- extremo = maxima corrida, preco = minima da vela. E a geometria
             que o detector usa em todo lugar (BOS/CHoCH olham pavio para
             quebra e close para confirmacao), e dispara mais cedo.
- `close` -- extremo = maior *fechamento*, preco = fechamento. Conservadora:
             ignora o pavio de stop-hunt, que e justamente o que nao deveria
             encerrar uma perna.

**ATR**: a mesma formula do detector (`mean true range / close`), mas em media
EXPANSIVA causal -- so velas ate o ponto em questao (o `mean_tr_trailing` do
detector). Duas variantes, porque o usuario levantou a questao do denominador:

- `frozen` -- congelado no advance que abriu a perna. O denominador nao se mexe
              durante a consolidacao: 6 ATR de retracao querem dizer a mesma
              coisa na vela 10 e na vela 200.
- `moving` -- recalculado a cada vela. Durante uma consolidacao o TR medio cai,
              entao o mesmo movimento em preco vale MAIS ATR: a regra fica
              progressivamente mais facil de disparar.

Desfecho (so para AVALIAR o trigger, nunca para dispara-lo)
===========================================================

Do trigger em diante, o primeiro que acontecer:

- `resumed`  -- BOS nao-provisional da direcao da perna;
- `reversed` -- BOS nao-provisional da direcao oposta;
- `open`     -- a janela acabou antes.

`quick_resume_5/10/20` = retomada dentro de 5/10/20 velas do trigger;
`invalidated_early` e o quick_resume_5: a perna nunca tinha morrido.

Controle
========

Perna pos-expansao (>= `MIN_BOS` BOS, >= `MIN_ATR` de deslocamento, os criterios
da Etapa 0.5, importados) vs perna pos-BOS comum. Se STALE disparar igual nas
duas, ele nao esta capturando a condicao pos-expansao -- so estrutura lenta.

Uso:

    poetry run python -m research.structural_stall_validation
    poetry run python -m research.structural_stall_validation --btc-case
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    TimeFrame,
)
from research.expansion_stall import MIN_ATR, MIN_BOS, _runs
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "AAVEUSDT"]
TIMEFRAMES = [TimeFrame.M15, TimeFrame.H1, TimeFrame.H4]

N_GRID = (10, 20, 30, 40, 50, 60, 80, 100)
K_GRID = (2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0)
PRICE_MODES = ("wick", "close")
ATR_MODES = ("frozen", "moving")
QUICK = (5, 10, 20)


def trailing_atr(candles: Sequence[Candle]) -> list[float]:
    """Media EXPANSIVA do true-range% ate cada vela (o `mean_tr_trailing`).

    `out[i]` usa apenas velas <= i, entao consultar em `i` e causal. `out[0]`
    repete `out[1]` (uma vela nao tem TR).
    """
    trs = [
        max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)) / c.close
        for p, c in zip(candles, candles[1:], strict=False)
    ]
    out: list[float] = []
    running = 0.0
    for count, value in enumerate(trs, start=1):
        running += value
        out.append(running / count)
    return [out[0] if out else 0.0, *out]


@dataclass
class Leg:
    """Uma perna estrutural vigente e o caminho causal de STALE dentro dela."""

    symbol: str
    timeframe: str
    kind: str                    # expansion | control
    direction: str
    start_timestamp: str
    start_index: int
    start_price: float
    end_index: int               # proximo advance (ou fim da janela)
    run_bos: int
    run_displacement_atr: float
    atr_frozen: float
    # Por deslocamento em velas dentro da perna: (bars, retr_wick, retr_close)
    # com ATR congelado; as variantes `moving` sao derivadas com o ATR da vela.
    path: list[tuple[int, float, float, float, float]] = field(default_factory=list)
    events_in_leg: int = 0


@dataclass
class Trigger:
    """Um disparo de STALE e o que aconteceu depois (avaliacao, nao decisao)."""

    symbol: str
    timeframe: str
    kind: str
    direction: str
    n: int
    k: float
    price_mode: str
    atr_mode: str
    leg_start: str
    timestamp: str
    price: float
    bars_since_advance: int
    retracement_atr: float
    distance_from_extreme_atr: float
    events_before: int
    outcome: str                  # resumed | reversed | open
    bars_to_outcome: int | None
    events_after: int


def _legs_of_run(
    events: Sequence[MarketStructure], candles: Sequence[Candle]
) -> list[Leg]:
    """Pernas (BOS -> proximo advance) com o caminho causal de cada vela."""
    advances = dd._advance_boundaries(list(events), list(candles))
    if not advances:
        return []
    atr_path = trailing_atr(candles)
    index_by_ts = {c.timestamp: i for i, c in enumerate(candles)}
    advance_indices = [i for i, _ in advances]

    expansion_last: dict[int, tuple[int, float]] = {}
    for run in _runs(events):
        prices = [e.price_level for e in run]
        bullish = run[0].direction is MarketDirection.BULLISH
        extreme = max(prices) if bullish else min(prices)
        atr = atr_path[index_by_ts.get(run[0].timestamp, 0)] or 1.0
        displacement = abs(extreme - run[0].price_level) / run[0].price_level / atr
        if len(run) >= MIN_BOS and displacement >= MIN_ATR:
            idx = index_by_ts.get(run[-1].timestamp)
            if idx is not None:
                expansion_last[idx] = (len(run), displacement)

    legs: list[Leg] = []
    for position, (index, _trend) in enumerate(advances):
        opener = next(
            (
                e
                for e in events
                if index_by_ts.get(e.timestamp) == index
                and e.event is StructureEvent.BREAK_OF_STRUCTURE
                and not e.provisional
            ),
            None,
        )
        if opener is None:
            continue
        end = (
            advance_indices[position + 1]
            if position + 1 < len(advance_indices)
            else len(candles) - 1
        )
        bullish = opener.direction is MarketDirection.BULLISH
        entry = opener.price_level
        atr_frozen = atr_path[index] or 1.0
        run_bos, displacement = expansion_last.get(index, (1, 0.0))

        extreme_wick = candles[index].high if bullish else candles[index].low
        extreme_close = candles[index].close
        path: list[tuple[int, float, float, float, float]] = []
        for i in range(index, end + 1):
            candle = candles[i]
            if bullish:
                extreme_wick = max(extreme_wick, candle.high)
                extreme_close = max(extreme_close, candle.close)
                give_wick = extreme_wick - candle.low
                give_close = extreme_close - candle.close
            else:
                extreme_wick = min(extreme_wick, candle.low)
                extreme_close = min(extreme_close, candle.close)
                give_wick = candle.high - extreme_wick
                give_close = candle.close - extreme_close
            atr_now = atr_path[i] or atr_frozen
            path.append(
                (
                    i - index,
                    give_wick / entry / atr_frozen,
                    give_close / entry / atr_frozen,
                    give_wick / entry / atr_now,
                    give_close / entry / atr_now,
                )
            )
        legs.append(
            Leg(
                symbol=opener.symbol,
                timeframe=opener.timeframe.value,
                kind="expansion" if index in expansion_last else "control",
                direction=opener.direction.value,
                start_timestamp=str(opener.timestamp),
                start_index=index,
                start_price=entry,
                end_index=end,
                run_bos=run_bos,
                run_displacement_atr=round(displacement, 1),
                atr_frozen=atr_frozen,
                path=path,
                events_in_leg=sum(
                    1
                    for e in events
                    if index < index_by_ts.get(e.timestamp, -1) <= end
                ),
            )
        )
    return legs


def _column(price_mode: str, atr_mode: str) -> int:
    return {("wick", "frozen"): 1, ("close", "frozen"): 2, ("wick", "moving"): 3}.get(
        (price_mode, atr_mode), 4
    )


def triggers_of_leg(
    leg: Leg,
    events: Sequence[MarketStructure],
    candles: Sequence[Candle],
    event_indices: Sequence[int] | None = None,
    *,
    n: int,
    k: float,
    price_mode: str,
    atr_mode: str,
) -> Trigger | None:
    """O PRIMEIRO candle da perna em que as duas condicoes valem -- ou nada."""
    column = _column(price_mode, atr_mode)
    if event_indices is None:
        event_indices = [_idx(e, candles, -1) for e in events]
    hit = next(
        (row for row in leg.path if row[0] >= n and row[column] >= k),
        None,
    )
    if hit is None:
        return None
    offset = hit[0]
    index = leg.start_index + offset
    candle = candles[index]
    bullish = leg.direction == MarketDirection.BULLISH.value

    outcome, bars_to = "open", None
    for event, event_index in zip(events, event_indices, strict=False):
        if event_index <= index or event.provisional:
            continue
        if event.event is not StructureEvent.BREAK_OF_STRUCTURE:
            continue
        same = (event.direction is MarketDirection.BULLISH) == bullish
        outcome = "resumed" if same else "reversed"
        bars_to = event_index - index
        break

    events_before = sum(1 for i in event_indices if leg.start_index < i <= index)
    horizon = index + bars_to if bars_to else len(candles)
    events_after = sum(1 for i in event_indices if index < i <= horizon)
    extreme_gap = hit[column]
    return Trigger(
        symbol=leg.symbol,
        timeframe=leg.timeframe,
        kind=leg.kind,
        direction=leg.direction,
        n=n,
        k=k,
        price_mode=price_mode,
        atr_mode=atr_mode,
        leg_start=leg.start_timestamp,
        timestamp=str(candle.timestamp),
        price=candle.close,
        bars_since_advance=offset,
        retracement_atr=round(hit[column], 1),
        distance_from_extreme_atr=round(extreme_gap, 1),
        events_before=events_before,
        outcome=outcome,
        bars_to_outcome=bars_to,
        events_after=events_after,
    )


def _idx(event: MarketStructure, candles: Sequence[Candle], default: int) -> int:
    for i, candle in enumerate(candles):
        if candle.timestamp == event.timestamp:
            return i
    return default


@dataclass
class RunData:
    legs: list[Leg]
    events: list[MarketStructure]
    candles: list[Candle]
    #: Indice de candle de cada evento, resolvido uma vez por janela (a
    #: varredura reavalia as mesmas pernas centenas de vezes).
    event_indices: list[int] = field(default_factory=list)


def collect(
    symbols: Sequence[str], timeframes: Sequence[TimeFrame], windows: int, limit: int
) -> list[RunData]:
    out: list[RunData] = []
    for symbol in symbols:
        for timeframe in timeframes:
            if not (CACHE_DIR / f"{symbol}_{timeframe.value}.json").exists():
                print(f"! sem cache: {symbol} {timeframe.value}")
                continue
            series = load_series(symbol, timeframe)
            for w in range(windows):
                end = len(series) - w * limit
                start = end - limit - BUFFER
                if start < 0:
                    break
                run = dd._run_internal_structure(
                    provider=SliceProvider(series[start:end]),
                    symbol=symbol,
                    timeframe=timeframe,
                    limit=limit,
                    confluence_filter=True,
                )
                index_by_ts = {c.timestamp: i for i, c in enumerate(run.candles)}
                out.append(
                    RunData(
                        legs=_legs_of_run(run.events, run.candles),
                        events=list(run.events),
                        candles=list(run.candles),
                        event_indices=[
                            index_by_ts.get(e.timestamp, -1) for e in run.events
                        ],
                    )
                )
    return out


def evaluate(
    runs: Sequence[RunData], *, n: int, k: float, price_mode: str, atr_mode: str
) -> list[Trigger]:
    out: list[Trigger] = []
    for run in runs:
        for leg in run.legs:
            trigger = triggers_of_leg(
                leg,
                run.events,
                run.candles,
                run.event_indices,
                n=n,
                k=k,
                price_mode=price_mode,
                atr_mode=atr_mode,
            )
            if trigger is not None:
                out.append(trigger)
    return out


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def summarize(triggers: Sequence[Trigger], legs: int) -> dict[str, Any]:
    n = len(triggers)
    if n == 0:
        return {"legs": legs, "triggers": 0, "coverage": 0.0}
    resumed = [t for t in triggers if t.outcome == "resumed"]
    reversed_ = [t for t in triggers if t.outcome == "reversed"]
    out: dict[str, Any] = {
        "legs": legs,
        "triggers": n,
        "coverage": n / legs if legs else 0.0,
        "resumed": len(resumed) / n,
        "reversed": len(reversed_) / n,
        "open": sum(1 for t in triggers if t.outcome == "open") / n,
        "lead_bars": _median([t.bars_since_advance for t in triggers]),
        "retr_atr": _median([t.retracement_atr for t in triggers]),
        "bars_to_resume": _median([t.bars_to_outcome or 0 for t in resumed]),
        "bars_to_reverse": _median([t.bars_to_outcome or 0 for t in reversed_]),
        "events_before": _median([t.events_before for t in triggers]),
        "events_after": _median([t.events_after for t in triggers]),
    }
    for horizon in QUICK:
        out[f"quick_resume_{horizon}"] = (
            sum(
                1
                for t in resumed
                if t.bars_to_outcome is not None and t.bars_to_outcome <= horizon
            )
            / n
        )
    return out


def _print_grid(
    runs: Sequence[RunData], kind: str, price_mode: str, atr_mode: str, top: int
) -> list[tuple[int, float, dict[str, Any]]]:
    legs = sum(1 for r in runs for leg in r.legs if leg.kind == kind)
    rows: list[tuple[int, float, dict[str, Any]]] = []
    for n in N_GRID:
        for k in K_GRID:
            triggers = [
                t
                for t in evaluate(runs, n=n, k=k, price_mode=price_mode, atr_mode=atr_mode)
                if t.kind == kind
            ]
            rows.append((n, k, summarize(triggers, legs)))
    header = (
        f"{'N':>4} {'K':>5} {'cobertura':>10} {'quick5':>7} {'quick10':>8} {'quick20':>8} "
        f"{'resumed':>8} {'reversed':>9} {'open':>6} {'lead':>6} {'retr':>6} {'->saida':>8}"
    )
    print(f"\n-- grade {kind} | preco={price_mode} atr={atr_mode} | {legs} pernas --")
    print(header)
    for n, k, s in rows:
        if not s["triggers"]:
            continue
        print(
            f"{n:>4} {k:>5.0f} {s['coverage'] * 100:>9.0f}% "
            f"{s['quick_resume_5'] * 100:>6.0f}% {s['quick_resume_10'] * 100:>7.0f}% "
            f"{s['quick_resume_20'] * 100:>7.0f}% {s['resumed'] * 100:>7.0f}% "
            f"{s['reversed'] * 100:>8.0f}% {s['open'] * 100:>5.0f}% "
            f"{s['lead_bars']:>6.0f} {s['retr_atr']:>6.1f} "
            f"{(s['bars_to_resume'] or 0):>8.0f}"
        )
    del top
    return rows


def btc_case(runs: Sequence[RunData], candidates: Sequence[tuple[int, float]]) -> None:
    """O caso de referencia BTC H1 (ultimo BOS 2026-08-25 02:00 @81270.5)."""
    target = "2026-08-25 02:00:00+00:00"
    for run in runs:
        leg = next(
            (
                leg
                for leg in run.legs
                if leg.symbol == "BTCUSDT"
                and leg.timeframe == "1h"
                and leg.start_timestamp == target
            ),
            None,
        )
        if leg is None:
            continue
        choch = next(
            (
                e
                for e in run.events
                if e.event is StructureEvent.CHANGE_OF_CHARACTER
                and not e.provisional
                and str(e.timestamp) > target
            ),
            None,
        )
        resume = next(
            (
                e
                for e in run.events
                if e.event is StructureEvent.BREAK_OF_STRUCTURE
                and not e.provisional
                and e.direction is MarketDirection.BULLISH
                and str(e.timestamp) > target
            ),
            None,
        )
        choch_i = _idx(choch, run.candles, -1) if choch else None
        resume_i = _idx(resume, run.candles, -1) if resume else None
        print(f"\n== BTC H1 | perna bullish de {target} @{leg.start_price} ==")
        print(f"fim da perna (proximo advance): {run.candles[leg.end_index].timestamp}")
        if choch:
            print(f"CHoCH bearish: {choch.timestamp} @{choch.price_level}")
        if resume:
            print(f"BOS bullish de retomada: {resume.timestamp} @{resume.price_level}")
        print(
            f"{'N':>4} {'K':>4} {'preco':<6} {'atr':<7} {'STALE em':<26} {'preco':>10} "
            f"{'retr':>6} {'apos adv':>9} {'antes CHoCH':>12} {'antes retomada':>15}"
        )
        for n, k in candidates:
            for price_mode in PRICE_MODES:
                for atr_mode in ATR_MODES:
                    trigger = triggers_of_leg(
                        leg,
                        run.events,
                        run.candles,
                        n=n,
                        k=k,
                        price_mode=price_mode,
                        atr_mode=atr_mode,
                    )
                    if trigger is None:
                        print(
                            f"{n:>4} {k:>4.0f} {price_mode:<6} {atr_mode:<7} "
                            f"{'(nao dispara)':<26}"
                        )
                        continue
                    index = leg.start_index + trigger.bars_since_advance
                    print(
                        f"{n:>4} {k:>4.0f} {price_mode:<6} {atr_mode:<7} "
                        f"{trigger.timestamp:<26} {trigger.price:>10} "
                        f"{trigger.retracement_atr:>6.1f} "
                        f"{trigger.bars_since_advance:>9} "
                        f"{(choch_i - index if choch_i else 0):>12} "
                        f"{(resume_i - index if resume_i else 0):>15}"
                    )
        return
    print("\n! perna BTC H1 de referencia nao encontrada nas janelas rodadas")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS)
    parser.add_argument("--timeframes", nargs="+", default=[t.value for t in TIMEFRAMES])
    parser.add_argument("--windows", type=int, default=6)
    parser.add_argument("--limit", type=int, default=LIMIT)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    runs = collect(
        args.symbols, [TimeFrame(t) for t in args.timeframes], args.windows, args.limit
    )
    legs = [leg for r in runs for leg in r.legs]
    print(
        f"\n{len(runs)} janelas | {len(legs)} pernas "
        f"({sum(1 for leg in legs if leg.kind == 'expansion')} pos-expansao, "
        f"{sum(1 for leg in legs if leg.kind == 'control')} controle)"
    )

    grids: dict[str, list[tuple[int, float, dict[str, Any]]]] = {}
    for kind in ("expansion", "control"):
        for price_mode in PRICE_MODES:
            for atr_mode in ATR_MODES:
                key = f"{kind}|{price_mode}|{atr_mode}"
                grids[key] = _print_grid(runs, kind, price_mode, atr_mode, args.top)

    print("\n== por timeframe (preco=close, atr=frozen) ==")
    print(f"{'tf':<5} {'N':>4} {'K':>4} {'pernas':>7} {'cobertura':>10} {'quick10':>8} {'lead':>6}")
    for timeframe in args.timeframes:
        for n, k in ((30, 4.0), (40, 5.0), (50, 6.0), (60, 6.0)):
            triggers = [
                t
                for t in evaluate(runs, n=n, k=k, price_mode="close", atr_mode="frozen")
                if t.kind == "expansion" and t.timeframe == timeframe
            ]
            total = sum(
                1
                for leg in legs
                if leg.kind == "expansion" and leg.timeframe == timeframe
            )
            s = summarize(triggers, total)
            if not s["triggers"]:
                print(f"{timeframe:<5} {n:>4} {k:>4.0f} {total:>7} {'0%':>10}")
                continue
            print(
                f"{timeframe:<5} {n:>4} {k:>4.0f} {total:>7} {s['coverage'] * 100:>9.0f}% "
                f"{s['quick_resume_10'] * 100:>7.0f}% {s['lead_bars']:>6.0f}"
            )

    btc_case(runs, [(30, 4.0), (40, 5.0), (50, 6.0), (60, 6.0), (60, 8.0)])

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "params": {
                        "symbols": args.symbols,
                        "timeframes": args.timeframes,
                        "windows": args.windows,
                        "limit": args.limit,
                        "n_grid": list(N_GRID),
                        "k_grid": list(K_GRID),
                        "price_modes": list(PRICE_MODES),
                        "atr_modes": list(ATR_MODES),
                        "min_bos": MIN_BOS,
                        "min_atr": MIN_ATR,
                    },
                    "grids": {
                        key: [{"n": n, "k": k, **s} for n, k, s in rows]
                        for key, rows in grids.items()
                    },
                    "triggers_close_frozen": [
                        asdict(t)
                        for n, k in ((40, 5.0), (50, 6.0), (60, 6.0))
                        for t in evaluate(
                            runs, n=n, k=k, price_mode="close", atr_mode="frozen"
                        )
                    ],
                },
                indent=2,
            )
        )
        print(f"\n-> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
