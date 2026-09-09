"""Etapa 3.9: o guard do stall perde alguma perna por causa do `CHOCH_FAILED`?

`detect_structural_stall` le a perna vigente pelo ULTIMO advance e exige que
ele seja um `BREAK_OF_STRUCTURE`:

    opener, advance_index = advance
    if opener.event is not StructureEvent.BREAK_OF_STRUCTURE:
        return None

Como `_last_advance` conta `CHOCH_FAILED` entre os advances (mesma semantica de
`app.dashboard_data._advance_boundaries`), existe um buraco teorico: um `✕` sem
BOS de continuacao depois dele fica sendo o ultimo advance, e a perna reafirmada
nunca pode ficar STALE por mais tempo que passe parada.

No BTC H1 ao vivo o buraco nao aparece -- o `✕` de 2026-09-03 12:00 e seguido
do BOS bullish de 15:00, que vira o opener. Este script mede se isso e a REGRA
ou a excecao, e, mais importante, se a excecao chega a custar algum STALE.

O que e medido
==============

Para cada `CHOCH_FAILED` nao-provisional do stream final (o que o grafico
desenha), o proximo advance decide a classe:

- **A** -- vem um BOS na direcao REAFIRMADA (o oposto da direcao do `✕`, que e
  a do CHoCH que falhou). E o caso do BTC H1: o guard nunca chega a morder.
- **B** -- vem outro advance qualquer (CHoCH, `✕`, ou BOS na direcao do CHoCH
  que falhou). O guard tambem para de morder, mas a perna reafirmada nao ganhou
  opener proprio.
- **C** -- nao vem advance nenhum ate o fim da janela: o `✕` FICA sendo
  `_last_advance`.

Contar C nao basta, e por isso a medida principal nao e a classe. Entre o `✕` e
o proximo advance (ou o fim da janela) existe um intervalo -- o **gap** -- em
que `_last_advance` E o `✕` e o guard esta ativo. A pergunta que decide e:
dentro desse gap, alguma perna cumpriria N=50 velas sem advance E K=6 ATR de
retracao, ou seja, teria sido STALE se o opener fosse aceito?

Contrafactual (so pesquisa, nada e implementado)
================================================

Opener contrafactual = o ultimo BOS nao-provisional ANTERIOR ao `✕` cuja
direcao e a reafirmada. E o que `_last_advance` devolveria se o `✕` nao contasse
como advance. A partir dai a aritmetica e a de `detect_structural_stall`, sem um
caractere de diferenca de semantica:

- extremo corrido em CLOSE desde o candle do opener (nunca o extremo final --
  isso seria lookahead);
- retracao = give_back / `opener.price_level` / ATR;
- ATR = `frozen_atr_pct` no candle do opener (media expansiva causal);
- gatilho no PRIMEIRO candle com `bars >= 50` e `retracao >= 6.0`.

Causalidade: o gatilho so olha candles ate ele mesmo. A classificacao A/B/C e a
leitura do desfecho (resumed/reversed/open) sao retrospectivas de proposito --
avaliam o gatilho, nunca o disparam.

Um gatilho contrafactual so conta como **perna perdida** quando cai DENTRO do
gap. Antes do `✕` o `_last_advance` era outro evento e o guard nao tem culpa
nenhuma pelo que aconteceu la.

Truncamento de janela
=====================

Uma janela tem `LIMIT` velas: um `✕` a 10 velas do fim entra em C sem que isso
diga nada sobre o detector -- nao havia horizonte para um advance aparecer. Todo
caso C carrega `bars_to_window_end`, e o relatorio separa os C com horizonte
suficiente (>= N velas) dos que sao artefato de borda.

Uso
===

    poetry run python -m research.choch_failed_stall_audit
    poetry run python -m research.choch_failed_stall_audit --json \
        research/choch_failed_stall_audit_baseline.json
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
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
from liquidity_hunter.liquidity.structural_stall import (
    DEFAULT_STALL_BARS,
    DEFAULT_STALL_RETRACEMENT_ATR,
    frozen_atr_pct,
)
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series

#: A mesma matriz de `research/structural_stall_validation.py`.
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "AAVEUSDT"]
TIMEFRAMES = [TimeFrame.M15, TimeFrame.H1, TimeFrame.H4]

N = DEFAULT_STALL_BARS
K = DEFAULT_STALL_RETRACEMENT_ATR

#: O caso que originou a auditoria, conferido como sanity check (classe A).
BTC_CASE = ("BTCUSDT", "1h", "2026-09-03 12:00:00+00:00")


def reaffirmed(direction: MarketDirection) -> MarketDirection:
    """A tendencia que um `CHOCH_FAILED` devolve.

    `direction` de um `✕` e a do CHoCH que FALHOU (`enums.StructureEvent`), logo
    a estrutura retomada e a oposta -- a mesma inversao que
    `_advance_boundaries` aplica.
    """
    return (
        MarketDirection.BEARISH
        if direction is MarketDirection.BULLISH
        else MarketDirection.BULLISH
    )


@dataclass
class Case:
    """Um `CHOCH_FAILED` do stream final, seu gap, e o contrafactual dentro dele."""

    symbol: str
    timeframe: str
    window: int
    timestamp: str
    index: int
    #: Direcao do CHoCH que falhou.
    direction: str
    #: Tendencia reafirmada pela falha (o oposto).
    reaffirmed: str
    klass: str  # A | B | C

    # --- proximo advance (o que fecha o gap) ---
    next_event: str | None = None
    next_timestamp: str | None = None
    next_direction: str | None = None
    next_bars: int | None = None
    next_reference_price_level: float | None = None
    next_provisional: bool | None = None
    #: Sobreviveu aos passes de composicao? Por construcao sim -- a lista lida
    #: E a pos-composicao (`_run_internal_structure.events`). Registrado
    #: explicitamente para que o baseline mostre o fato em vez de supo-lo.
    next_survived_passes: bool | None = None

    # --- gap: onde `_last_advance` == CHOCH_FAILED ---
    gap_bars: int = 0
    bars_to_window_end: int = 0
    #: C com horizonte < N e artefato de borda, nao um buraco do detector.
    truncated: bool = False

    # --- contrafactual ---
    opener_timestamp: str | None = None
    opener_price: float | None = None
    opener_bars_before: int | None = None
    cf_trigger_timestamp: str | None = None
    cf_trigger_index: int | None = None
    cf_bars_since_advance: int | None = None
    cf_retracement_atr: float | None = None
    #: O gatilho caiu dentro do gap? So esses sao pernas perdidas pelo guard.
    cf_in_gap: bool = False
    cf_outcome: str | None = None  # resumed | reversed | open
    cf_bars_to_outcome: int | None = None


def _counterfactual(
    candles: Sequence[Candle],
    opener: MarketStructure,
    opener_index: int,
    *,
    n: int,
    k: float,
) -> tuple[int, int, float] | None:
    """`detect_structural_stall` com um opener imposto: (indice, bars, retracao).

    Copia deliberada da aritmetica de producao -- extremo em close desde o
    opener, retracao normalizada pelo `price_level` do opener e pelo
    `frozen_atr_pct` do candle dele, primeiro candle com as duas condicoes.
    Copiada e nao importada porque a de producao decide o opener sozinha, que e
    exatamente a parte que este contrafactual substitui.
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


def audit_run(
    symbol: str,
    timeframe: TimeFrame,
    window: int,
    events: Sequence[MarketStructure],
    candles: Sequence[Candle],
    *,
    n: int = N,
    k: float = K,
) -> list[Case]:
    """Todos os `CHOCH_FAILED` de uma janela, classificados e contrafactualizados."""
    index_by_ts = {c.timestamp: i for i, c in enumerate(candles)}
    advances = [
        (index_by_ts[e.timestamp], e)
        for e in events
        if not e.provisional
        and e.event
        in (
            StructureEvent.BREAK_OF_STRUCTURE,
            StructureEvent.CHANGE_OF_CHARACTER,
            StructureEvent.CHOCH_FAILED,
        )
        and e.timestamp in index_by_ts
    ]
    advances.sort(key=lambda pair: pair[0])

    out: list[Case] = []
    for position, (index, event) in enumerate(advances):
        if event.event is not StructureEvent.CHOCH_FAILED:
            continue
        trend = reaffirmed(event.direction)
        nxt = advances[position + 1] if position + 1 < len(advances) else None
        last = len(candles) - 1

        if nxt is None:
            klass = "C"
            gap_end = last
        else:
            same_bos = (
                nxt[1].event is StructureEvent.BREAK_OF_STRUCTURE
                and nxt[1].direction is trend
            )
            klass = "A" if same_bos else "B"
            gap_end = nxt[0]

        case = Case(
            symbol=symbol,
            timeframe=timeframe.value,
            window=window,
            timestamp=str(event.timestamp),
            index=index,
            direction=event.direction.value,
            reaffirmed=trend.value,
            klass=klass,
            gap_bars=gap_end - index,
            bars_to_window_end=last - index,
            truncated=klass == "C" and (last - index) < n,
        )
        if nxt is not None:
            case.next_event = nxt[1].event.value
            case.next_timestamp = str(nxt[1].timestamp)
            case.next_direction = nxt[1].direction.value
            case.next_bars = nxt[0] - index
            case.next_reference_price_level = nxt[1].reference_price_level
            case.next_provisional = nxt[1].provisional
            case.next_survived_passes = True

        # Opener contrafactual: ultimo BOS anterior na tendencia reafirmada.
        opener = next(
            (
                (i, e)
                for i, e in reversed(advances[:position])
                if e.event is StructureEvent.BREAK_OF_STRUCTURE and e.direction is trend
            ),
            None,
        )
        if opener is not None:
            opener_index, opener_event = opener
            case.opener_timestamp = str(opener_event.timestamp)
            case.opener_price = opener_event.price_level
            case.opener_bars_before = index - opener_index
            hit = _counterfactual(candles, opener_event, opener_index, n=n, k=k)
            if hit is not None:
                trigger_index, bars, retracement = hit
                case.cf_trigger_timestamp = str(candles[trigger_index].timestamp)
                case.cf_trigger_index = trigger_index
                case.cf_bars_since_advance = bars
                case.cf_retracement_atr = round(retracement, 2)
                case.cf_in_gap = index <= trigger_index <= gap_end
                outcome, bars_to = "open", None
                for i, e in advances:
                    if i <= trigger_index or e.event is not StructureEvent.BREAK_OF_STRUCTURE:
                        continue
                    outcome = "resumed" if e.direction is trend else "reversed"
                    bars_to = i - trigger_index
                    break
                case.cf_outcome = outcome
                case.cf_bars_to_outcome = bars_to
        out.append(case)
    return out


def collect(
    symbols: Sequence[str], timeframes: Sequence[TimeFrame], windows: int, limit: int
) -> tuple[list[Case], int]:
    """Varre a matriz e devolve (casos, janelas efetivamente rodadas)."""
    cases: list[Case] = []
    run_count = 0
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
                run_count += 1
                cases.extend(audit_run(symbol, timeframe, w, run.events, run.candles))
    return cases, run_count


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "-"


def summarize(cases: Sequence[Case], runs: int) -> dict[str, Any]:
    total = len(cases)
    by_class = {c: sum(1 for x in cases if x.klass == c) for c in ("A", "B", "C")}
    stuck = [x for x in cases if x.klass == "C"]
    stuck_real = [x for x in stuck if not x.truncated]
    blocked = [x for x in cases if x.cf_in_gap]
    blocked_c = [x for x in blocked if x.klass == "C"]
    no_opener = [x for x in cases if x.opener_timestamp is None]
    outcomes = {
        o: sum(1 for x in blocked if x.cf_outcome == o)
        for o in ("resumed", "reversed", "open")
    }
    return {
        "runs": runs,
        "n": N,
        "k": K,
        "total_choch_failed": total,
        "class_A_bos_reaffirmed": by_class["A"],
        "class_B_other_advance": by_class["B"],
        "class_C_no_advance": by_class["C"],
        "class_C_truncated_by_window": sum(1 for x in stuck if x.truncated),
        "class_C_with_full_horizon": len(stuck_real),
        "no_counterfactual_opener": len(no_opener),
        "blocked_stales": len(blocked),
        "blocked_stales_class_C": len(blocked_c),
        "blocked_outcomes": outcomes,
        "median_gap_bars": (
            sorted(x.gap_bars for x in cases)[total // 2] if total else None
        ),
        "by_timeframe": {
            tf: {
                "total": sum(1 for x in cases if x.timeframe == tf),
                "A": sum(1 for x in cases if x.timeframe == tf and x.klass == "A"),
                "B": sum(1 for x in cases if x.timeframe == tf and x.klass == "B"),
                "C": sum(1 for x in cases if x.timeframe == tf and x.klass == "C"),
                "blocked": sum(1 for x in cases if x.timeframe == tf and x.cf_in_gap),
            }
            for tf in sorted({x.timeframe for x in cases})
        },
    }


def report(cases: Sequence[Case], summary: dict[str, Any]) -> None:
    total = summary["total_choch_failed"]
    print(f"\n{summary['runs']} janelas | {total} CHOCH_FAILED nao-provisionais")
    print("\n== classe do proximo advance ==")
    for key, label in (
        ("class_A_bos_reaffirmed", "A  BOS da tendencia reafirmada"),
        ("class_B_other_advance", "B  outro advance"),
        ("class_C_no_advance", "C  nenhum advance (✕ preso em _last_advance)"),
    ):
        print(f"{label:<48} {summary[key]:>5}  {_pct(summary[key], total):>7}")
    print(
        f"{'   dos quais truncados pela borda da janela':<48} "
        f"{summary['class_C_truncated_by_window']:>5}"
    )
    print(
        f"{'   C com horizonte >= N velas':<48} "
        f"{summary['class_C_with_full_horizon']:>5}"
    )

    print("\n== o guard bloqueia alguma perna? ==")
    print(f"gatilho contrafactual dentro do gap  : {summary['blocked_stales']}")
    print(f"   desses, em casos classe C         : {summary['blocked_stales_class_C']}")
    print(f"sem opener contrafactual (nenhum BOS anterior): {summary['no_counterfactual_opener']}")
    print(f"desfecho dos bloqueados              : {summary['blocked_outcomes']}")

    print("\n== por timeframe ==")
    print(f"{'tf':<6} {'total':>6} {'A':>5} {'B':>5} {'C':>5} {'bloqueados':>11}")
    for tf, row in summary["by_timeframe"].items():
        print(
            f"{tf:<6} {row['total']:>6} {row['A']:>5} {row['B']:>5} {row['C']:>5} "
            f"{row['blocked']:>11}"
        )

    blocked = [x for x in cases if x.cf_in_gap]
    if blocked:
        print("\n== pernas perdidas pelo guard (ate 20) ==")
        for x in blocked[:20]:
            print(
                f"{x.symbol:<9} {x.timeframe:<4} ✕ {x.timestamp} {x.direction:<7} "
                f"classe {x.klass} gap={x.gap_bars:<4} "
                f"stale@{x.cf_trigger_timestamp} bars={x.cf_bars_since_advance} "
                f"retr={x.cf_retracement_atr} -> {x.cf_outcome}"
            )

    btc = [
        x
        for x in cases
        if (x.symbol, x.timeframe, x.timestamp) == BTC_CASE
    ]
    print("\n== sanity check BTC H1 2026-09-03 12:00 ==")
    if not btc:
        print("nao esta no cache desta matriz (cache anterior a essa data)")
    for x in btc:
        print(
            f"classe {x.klass} | proximo: {x.next_event} {x.next_direction} "
            f"{x.next_timestamp} (+{x.next_bars} velas, ref={x.next_reference_price_level}, "
            f"provisional={x.next_provisional})"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS)
    parser.add_argument("--timeframes", nargs="+", default=[t.value for t in TIMEFRAMES])
    parser.add_argument("--windows", type=int, default=6)
    parser.add_argument("--limit", type=int, default=LIMIT)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    cases, runs = collect(
        args.symbols, [TimeFrame(t) for t in args.timeframes], args.windows, args.limit
    )
    summary = summarize(cases, runs)
    report(cases, summary)
    if args.json:
        args.json.write_text(
            json.dumps(
                {"summary": summary, "cases": [asdict(c) for c in cases]},
                indent=2,
                sort_keys=True,
            )
        )
        print(f"\njson: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
