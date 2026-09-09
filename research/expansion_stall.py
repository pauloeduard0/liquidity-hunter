"""Etapa 0.5: a "estrutura perdida" depois de uma expansao forte -- existe como padrao?

A queixa do usuario (2026-09-08) nao e "o CHoCH esta errado". E: depois de uma
expansao forte o grafico fica com BOS la em cima, CHoCH duvidosos no meio e
oscilacao nova embaixo, e nao da para dizer qual estrutura esta vigente.

Este modulo NAO muda nada e NAO propoe regime/range. Ele procura o *episodio*
que corresponde a essa descricao e mede o que acontece dentro dele.

Episodio
========

1. **Expansao**: uma corrida de >= `MIN_BOS` BOS nao-provisionais da MESMA
   direcao, sem CHoCH nao-provisional no meio (um CHoCH encerra a perna), cujo
   deslocamento do primeiro BOS ate o extremo da corrida vale pelo menos
   `MIN_ATR` unidades de volatilidade (mean true-range% da serie, a mesma
   formula do detector -- em ATR, nao em %, senao o limiar significa coisas
   diferentes em cada ativo/TF).

2. **Parada**: a janela que comeca no ULTIMO BOS da corrida e vai ate a
   estrutura se restabelecer, o que acontece no primeiro de:

   - `resumed`  -- um BOS nao-provisional da mesma direcao (a perna original
                   voltou a andar);
   - `reversed` -- um BOS nao-provisional da direcao oposta (a reversao virou
                   estrutura de verdade);
   - `open`     -- acabou a janela sem nenhum dos dois.

   Tudo que aparece no meio -- CHoCH contrario, `CHOCH_FAILED`, fizzle, sweep,
   marcas provisionais -- e o "ruido" que o usuario ve, e e o que se conta.

Densidade
=========

Por episodio: eventos totais, BOS, CHoCH, confirmados, invalidados, velas desde
o ultimo BOS, excursao maxima em ATR desde o ultimo BOS (para cada lado) e
numero de ALTERNANCIAS de direcao na sequencia de eventos -- a metrica mais
proxima de "conflitante": um episodio limpo alterna 0 ou 1 vez.

Forma
=====

`shape` e a assinatura literal que o usuario descreveu:

- `counter_failed_resumed`  BOS bull -> CHoCH bear -> falha/fizzle -> BOS bull
- `counter_failed_open`     idem, mas a perna nunca retoma dentro da janela
- `counter_confirmed`       o CHoCH contrario virou estrutura (reversao legitima)
- `quiet_resumed` / `quiet_open`  nenhum evento contrario -- a expansao so pausou

Controle
========

Sem controle, "N eventos depois de uma expansao" nao quer dizer nada. Cada BOS
nao-provisional que **nao** encerra uma expansao gera um episodio de controle
medido exatamente igual. A pergunta so tem resposta na comparacao.

Offline: reusa `SliceProvider`/`load_series` de `research/range_choch.py`
(mesmo cache, mesmo carregador -- nao ha um segundo mecanismo) e roda a
pipeline de producao (`_run_internal_structure`) em janelas disjuntas.

Uso:

    poetry run python -m research.expansion_stall                      # BTC H1
    poetry run python -m research.expansion_stall --symbols BTCUSDT ETHUSDT \
        --timeframes 1h 4h --windows 6 --json out.json
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
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series

#: BOS da mesma direcao necessarios para chamar de expansao.
MIN_BOS = 3
#: Deslocamento minimo da corrida, em unidades de mean true-range%.
MIN_ATR = 15.0

ADVANCES = (StructureEvent.BREAK_OF_STRUCTURE, StructureEvent.CHANGE_OF_CHARACTER)


def mean_tr_pct(candles: Sequence[Candle]) -> float:
    """A mesma formula do detector, sobre a serie que lhe for dada."""
    return statistics.fmean(
        max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)) / c.close
        for p, c in zip(candles, candles[1:], strict=False)
    )


@dataclass
class Episode:
    """Uma parada estrutural: do ultimo BOS ate a estrutura se restabelecer."""

    symbol: str
    timeframe: str
    kind: str                       # expansion | control
    direction: str                  # direcao da perna que parou
    # --- a expansao
    run_bos: int
    run_start: str | None
    run_displacement_atr: float
    last_bos_timestamp: str
    last_bos_price: float
    last_bos_reference: float | None
    expansion_extreme: float
    expansion_extreme_timestamp: str | None
    # --- o contra-ataque
    first_counter_choch: str | None
    counter_choch_reference: float | None
    counter_choch_structural: bool | None
    counter_choch_confirmed: bool
    counter_choch_invalidated: str | None   # failed | fizzled | None
    # --- desfecho
    outcome: str                    # resumed | reversed | open
    outcome_timestamp: str | None
    shape: str
    # --- densidade
    candles_since_bos: int
    events: int
    bos: int
    choch: int
    confirmed: int
    invalidated: int
    alternations: int
    excursion_with_atr: float
    excursion_against_atr: float
    timeline: list[dict[str, Any]] = field(default_factory=list)


def _timeline_row(
    event: MarketStructure, standing: MarketDirection, verdict: str
) -> dict[str, Any]:
    return {
        "timestamp": str(event.timestamp),
        "price": event.price_level,
        "event": event.event.value + ("?" if event.provisional else ""),
        "direction": event.direction.value,
        "scope": event.scope.value,
        "reference": event.reference_price_level,
        "reference_structural": event.reference_structural,
        "verdict": verdict,
        "standing_trend": standing.value,
    }


def _runs(events: Sequence[MarketStructure]) -> list[list[MarketStructure]]:
    """Corridas maximas de BOS nao-provisionais da mesma direcao.

    Um CHoCH nao-provisional encerra a corrida (a perna acabou, por definicao
    da propria maquina); um BOS da direcao oposta abre outra.
    """
    runs: list[list[MarketStructure]] = []
    current: list[MarketStructure] = []
    for event in events:
        if event.provisional:
            continue
        if event.event is StructureEvent.CHANGE_OF_CHARACTER:
            if current:
                runs.append(current)
            current = []
            continue
        if event.event is not StructureEvent.BREAK_OF_STRUCTURE:
            continue
        if current and current[-1].direction is not event.direction:
            runs.append(current)
            current = []
        current.append(event)
    if current:
        runs.append(current)
    return runs


def _episode(
    last_bos: MarketStructure,
    run: Sequence[MarketStructure],
    events: Sequence[MarketStructure],
    candles: Sequence[Candle],
    atr: float,
    kind: str,
) -> Episode:
    """Mede a janela que comeca em `last_bos` e vai ate a estrutura voltar."""
    index_by_ts = {c.timestamp: i for i, c in enumerate(candles)}
    start_i = index_by_ts[last_bos.timestamp]
    bullish = last_bos.direction is MarketDirection.BULLISH

    later = [e for e in events if e.timestamp > last_bos.timestamp]
    standing = last_bos.direction
    timeline: list[dict[str, Any]] = []
    outcome, outcome_ts = "open", None
    counter_ts: str | None = None
    counter_ref: float | None = None
    counter_structural: bool | None = None
    counter_confirmed = False
    counter_invalidated: str | None = None
    bos = choch = confirmed = invalidated = 0
    directions: list[MarketDirection] = []

    for event in later:
        verdict = ""
        if event.event is StructureEvent.BREAK_OF_STRUCTURE and not event.provisional:
            bos += 1
            confirmed += 1
            directions.append(event.direction)
            if event.direction is last_bos.direction:
                verdict = "retomada"
                timeline.append(_timeline_row(event, standing, verdict))
                outcome, outcome_ts = "resumed", str(event.timestamp)
                if counter_ts is not None and counter_invalidated is None:
                    # A perna original voltou a andar sem que o CHoCH contrario
                    # tenha sido formalmente invalidado.
                    counter_confirmed = False
                break
            standing = event.direction
            verdict = "nova estrutura"
            timeline.append(_timeline_row(event, standing, verdict))
            if counter_ts is not None:
                counter_confirmed = True
            outcome, outcome_ts = "reversed", str(event.timestamp)
            break
        if event.event is StructureEvent.CHANGE_OF_CHARACTER:
            choch += 1
            directions.append(event.direction)
            if not event.provisional:
                confirmed += 1
                standing = event.direction
            if event.direction is not last_bos.direction and counter_ts is None:
                counter_ts = str(event.timestamp)
                counter_ref = event.reference_price_level
                counter_structural = event.reference_structural
            verdict = "provisional" if event.provisional else "flip de estado"
        elif event.event is StructureEvent.CHOCH_FAILED:
            invalidated += 1
            verdict = "fizzle" if event.provisional else "invalidado"
            if counter_ts is not None and counter_invalidated is None:
                if event.direction is not last_bos.direction:
                    counter_invalidated = "fizzled" if event.provisional else "failed"
            if not event.provisional:
                standing = last_bos.direction
        elif event.event is StructureEvent.BREAK_OF_STRUCTURE:
            bos += 1
            verdict = "provisional"
            directions.append(event.direction)
        else:
            verdict = event.event.value
        timeline.append(_timeline_row(event, standing, verdict))

    end_ts = next((e.timestamp for e in later if str(e.timestamp) == outcome_ts), None)
    end_i = len(candles) - 1 if end_ts is None else index_by_ts.get(end_ts, len(candles) - 1)
    window = candles[start_i : end_i + 1]
    entry = last_bos.price_level
    highs = max(c.high for c in window)
    lows = min(c.low for c in window)
    with_atr = ((highs - entry) if bullish else (entry - lows)) / entry / atr
    against_atr = ((entry - lows) if bullish else (highs - entry)) / entry / atr

    alternations = sum(1 for a, b in zip(directions, directions[1:], strict=False) if a is not b)

    if counter_ts is None:
        shape = "quiet_resumed" if outcome == "resumed" else f"quiet_{outcome}"
    elif counter_confirmed or outcome == "reversed":
        shape = "counter_confirmed"
    elif counter_invalidated is not None:
        shape = "counter_failed_resumed" if outcome == "resumed" else "counter_failed_open"
    else:
        shape = f"counter_unresolved_{outcome}"

    run_prices = [e.price_level for e in run]
    run_extreme = max(run_prices) if bullish else min(run_prices)
    first = run[0]
    displacement = abs(run_extreme - first.price_level) / first.price_level / atr

    return Episode(
        symbol=last_bos.symbol,
        timeframe=last_bos.timeframe.value,
        kind=kind,
        direction=last_bos.direction.value,
        run_bos=len(run),
        run_start=str(first.timestamp),
        run_displacement_atr=round(displacement, 1),
        last_bos_timestamp=str(last_bos.timestamp),
        last_bos_price=last_bos.price_level,
        last_bos_reference=last_bos.reference_price_level,
        expansion_extreme=run_extreme,
        expansion_extreme_timestamp=str(
            next(e.timestamp for e in run if e.price_level == run_extreme)
        ),
        first_counter_choch=counter_ts,
        counter_choch_reference=counter_ref,
        counter_choch_structural=counter_structural,
        counter_choch_confirmed=counter_confirmed,
        counter_choch_invalidated=counter_invalidated,
        outcome=outcome,
        outcome_timestamp=outcome_ts,
        shape=shape,
        candles_since_bos=end_i - start_i,
        events=len(timeline),
        bos=bos,
        choch=choch,
        confirmed=confirmed,
        invalidated=invalidated,
        alternations=alternations,
        excursion_with_atr=round(with_atr, 1),
        excursion_against_atr=round(against_atr, 1),
        timeline=timeline,
    )


def episodes_of_run(
    events: Sequence[MarketStructure],
    candles: Sequence[Candle],
    *,
    min_bos: int = MIN_BOS,
    min_atr: float = MIN_ATR,
) -> list[Episode]:
    """Episodios de expansao + os de controle, de uma rodada de producao."""
    if len(candles) < 2:
        return []
    atr = mean_tr_pct(candles)
    if atr <= 0:
        return []
    out: list[Episode] = []
    expansion_bos: set[str] = set()
    for run in _runs(events):
        prices = [e.price_level for e in run]
        bullish = run[0].direction is MarketDirection.BULLISH
        extreme = max(prices) if bullish else min(prices)
        displacement = abs(extreme - run[0].price_level) / run[0].price_level / atr
        if len(run) >= min_bos and displacement >= min_atr:
            expansion_bos.add(str(run[-1].timestamp))
            out.append(_episode(run[-1], run, events, candles, atr, "expansion"))
    for event in events:
        if (
            event.event is StructureEvent.BREAK_OF_STRUCTURE
            and not event.provisional
            and str(event.timestamp) not in expansion_bos
        ):
            out.append(_episode(event, [event], events, candles, atr, "control"))
    return out


def collect(
    symbols: Sequence[str],
    timeframes: Sequence[TimeFrame],
    windows: int,
    limit: int,
    *,
    min_bos: int = MIN_BOS,
    min_atr: float = MIN_ATR,
) -> list[Episode]:
    out: list[Episode] = []
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
                out.extend(
                    episodes_of_run(
                        run.events, run.candles, min_bos=min_bos, min_atr=min_atr
                    )
                )
    return out


def _median(values: Sequence[float]) -> float:
    return statistics.median(values) if values else 0.0


def _stats(episodes: Sequence[Episode]) -> dict[str, Any]:
    return {
        "n": len(episodes),
        "eventos_med": _median([e.events for e in episodes]),
        "velas_med": _median([e.candles_since_bos for e in episodes]),
        "alternancias_med": _median([e.alternations for e in episodes]),
        "invalidados_med": _median([e.invalidated for e in episodes]),
        "contra_atr_med": _median([e.excursion_against_atr for e in episodes]),
        "com_contra_choch": (
            sum(1 for e in episodes if e.first_counter_choch) / max(len(episodes), 1)
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT"])
    parser.add_argument("--timeframes", nargs="+", default=["1h"])
    parser.add_argument("--windows", type=int, default=6)
    parser.add_argument("--limit", type=int, default=LIMIT)
    parser.add_argument("--cases", type=int, default=3, help="quantas timelines imprimir")
    parser.add_argument("--min-bos", type=int, default=MIN_BOS)
    parser.add_argument("--min-atr", type=float, default=MIN_ATR)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    episodes = collect(
        args.symbols,
        [TimeFrame(t) for t in args.timeframes],
        args.windows,
        args.limit,
        min_bos=args.min_bos,
        min_atr=args.min_atr,
    )
    expansions = [e for e in episodes if e.kind == "expansion"]
    controls = [e for e in episodes if e.kind == "control"]

    print(f"\n== {len(expansions)} expansoes (>= {args.min_bos} BOS, >= {args.min_atr} ATR) "
          f"vs {len(controls)} BOS de controle ==")
    header = (
        f"{'':<12} {'n':>4} {'eventos':>8} {'velas':>6} {'altern':>7} "
        f"{'invalid':>8} {'contra_ATR':>11} {'%c/CHoCH':>9}"
    )
    print(header)
    print("-" * len(header))
    for label, group in (("expansao", expansions), ("controle", controls)):
        s = _stats(group)
        print(
            f"{label:<12} {s['n']:>4} {s['eventos_med']:>8.1f} {s['velas_med']:>6.0f} "
            f"{s['alternancias_med']:>7.1f} {s['invalidados_med']:>8.1f} "
            f"{s['contra_atr_med']:>11.1f} {s['com_contra_choch'] * 100:>8.0f}%"
        )

    print("\n== formas ==")
    for label, group in (("expansao", expansions), ("controle", controls)):
        shapes: dict[str, int] = {}
        for e in group:
            shapes[e.shape] = shapes.get(e.shape, 0) + 1
        total = max(len(group), 1)
        rendered = ", ".join(
            f"{k}={v} ({v / total * 100:.0f}%)"
            for k, v in sorted(shapes.items(), key=lambda kv: -kv[1])
        )
        print(f"{label:<10} {rendered or 'nenhuma'}")

    worst = sorted(expansions, key=lambda e: (-e.alternations, -e.events))[: args.cases]
    for case in worst:
        print(
            f"\n=== {case.symbol} {case.timeframe} | expansao {case.run_bos} BOS "
            f"{case.direction} de {case.run_start}, {case.run_displacement_atr} ATR ==="
        )
        print(
            f"ultimo BOS da expansao : {case.last_bos_timestamp} @{case.last_bos_price} "
            f"(ref {case.last_bos_reference})"
        )
        print(
            f"extremo da expansao    : {case.expansion_extreme} "
            f"em {case.expansion_extreme_timestamp}"
        )
        print(
            f"1o CHoCH contrario     : {case.first_counter_choch} "
            f"ref={case.counter_choch_reference} "
            f"structural={case.counter_choch_structural} "
            f"confirmado={case.counter_choch_confirmed} "
            f"invalidado={case.counter_choch_invalidated}"
        )
        print(
            f"desfecho               : {case.outcome} em {case.outcome_timestamp} | "
            f"forma={case.shape} | {case.events} eventos, "
            f"{case.alternations} alternancias, {case.candles_since_bos} velas, "
            f"excursao a favor {case.excursion_with_atr} ATR "
            f"/ contra {case.excursion_against_atr} ATR"
        )
        print(
            f"{'timestamp':<26} {'preco':>10} {'evento':<22} {'dir':<8} "
            f"{'scope':<9} {'ref':>10} {'veredito':<14} vigente"
        )
        for row in case.timeline:
            print(
                f"{row['timestamp']:<26} {row['price']:>10} {row['event']:<22} "
                f"{row['direction']:<8} {row['scope']:<9} "
                f"{(row['reference'] if row['reference'] is not None else '-'):>10} "
                f"{row['verdict']:<14} {row['standing_trend']}"
            )

    if args.json:
        args.json.write_text(json.dumps([asdict(e) for e in episodes], indent=2))
        print(f"\n{len(episodes)} episodios -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
