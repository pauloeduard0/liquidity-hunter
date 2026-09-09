"""Etapa 2.5: o gate de expansao reproduz a populacao pos-expansao da 0.6?

`liquidity.structural_stall.is_stall_eligible_leg` e a versao de PRODUCAO da
definicao de expansao da Etapa 0.5 (>= 3 BOS nao-provisionais na mesma direcao,
>= 15 ATR de deslocamento). Ela difere da de research em um ponto, e so nesse:
research divide pelo `mean_tr_pct` da janela inteira -- que nao e causal, uma
vela nova reescreve um veredito antigo -- e a de producao usa o
`frozen_atr_pct` na vela do ultimo BOS da corrida.

Este modulo mede duas coisas, sem ligar nada:

1. **concordancia**: por perna, `leg.kind` da Etapa 0.6 vs o gate de producao
   avaliado CAUSALMENTE (serie e eventos truncados na propria vela que abre a
   perna, para nao dar ao gate nada que ele nao teria ao vivo);
2. **antes/depois**: os disparos de STALE em N=50/K=6 (close/frozen) com e sem
   o gate -- quantos sobrevivem, e se a taxa de retomada cai como a 0.6 previa.

    poetry run python -m research.expansion_gate
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from liquidity_hunter.core.domain import TimeFrame
from liquidity_hunter.liquidity.structural_stall import is_stall_eligible_leg
from research.range_choch import LIMIT
from research.structural_stall_validation import (
    QUICK,
    SYMBOLS,
    TIMEFRAMES,
    Leg,
    RunData,
    collect,
    summarize,
    triggers_of_leg,
)

N, K = 50, 6.0


def gate(run: RunData, leg: Leg) -> bool:
    """O gate como producao o veria NA VELA QUE ABRE A PERNA (sem futuro)."""
    candles = run.candles[: leg.start_index + 1]
    events = [e for e in run.events if e.timestamp <= candles[-1].timestamp]
    return is_stall_eligible_leg(candles, events)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS)
    parser.add_argument("--timeframes", nargs="+", default=[t.value for t in TIMEFRAMES])
    parser.add_argument("--windows", type=int, default=6)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    runs = collect(
        args.symbols, [TimeFrame(t) for t in args.timeframes], args.windows, LIMIT
    )

    matrix = {"TT": 0, "TF": 0, "FT": 0, "FF": 0}
    rows: list[dict[str, Any]] = []
    all_legs: list[tuple[RunData, Leg, bool]] = []
    for run in runs:
        for leg in run.legs:
            eligible = gate(run, leg)
            research = leg.kind == "expansion"
            matrix["TT" if research and eligible else
                   "TF" if research else
                   "FT" if eligible else "FF"] += 1
            all_legs.append((run, leg, eligible))
            if research != eligible:
                rows.append(
                    {
                        "symbol": leg.symbol,
                        "timeframe": leg.timeframe,
                        "leg": leg.start_timestamp,
                        "research": leg.kind,
                        "producao": eligible,
                        "run_bos": leg.run_bos,
                        "deslocamento_research_atr": leg.run_displacement_atr,
                    }
                )

    print(f"\n== concordancia do gate com a Etapa 0.6 ({len(all_legs)} pernas) ==")
    total = max(len(all_legs), 1)
    print(f"  research expansao & gate True  : {matrix['TT']}")
    print(f"  research expansao & gate False : {matrix['TF']}")
    print(f"  research controle & gate True  : {matrix['FT']}")
    print(f"  research controle & gate False : {matrix['FF']}")
    print(f"  concordancia: {(matrix['TT'] + matrix['FF']) / total * 100:.1f}%")

    triggers = []
    gated = []
    legs_all = len(all_legs)
    legs_gated = sum(1 for _, _, e in all_legs if e)
    for run, leg, eligible in all_legs:
        trigger = triggers_of_leg(
            leg, run.events, run.candles, run.event_indices,
            n=N, k=K, price_mode="close", atr_mode="frozen",
        )
        if trigger is None:
            continue
        triggers.append(trigger)
        if eligible:
            gated.append(trigger)

    charts = sum(len(r.candles) for r in runs) / LIMIT
    header = (
        f"{'':<16} {'pernas':>7} {'stalls':>7} {'cobert':>7} {'resumed':>8} "
        f"{'reversed':>9} {'open':>6} {'qr5':>5} {'qr10':>6} {'qr20':>6} {'/1200v':>7}"
    )
    print(f"\n== STALE N={N} K={K} (close/frozen), antes e depois do gate ==")
    print(header)
    print("-" * len(header))
    for label, group, legs in (
        ("sem gate", triggers, legs_all),
        ("com gate", gated, legs_gated),
    ):
        s = summarize(group, legs)
        if not group:
            print(f"{label:<16} {legs:>7} {0:>7}")
            continue
        print(
            f"{label:<16} {legs:>7} {s['triggers']:>7} {s['coverage'] * 100:>6.0f}% "
            f"{s['resumed'] * 100:>7.0f}% {s['reversed'] * 100:>8.0f}% "
            f"{s['open'] * 100:>5.0f}% "
            + " ".join(f"{s[f'quick_resume_{h}'] * 100:>4.0f}%" for h in QUICK)
            + f" {len(group) / max(charts, 1):>7.2f}"
        )
    removed = len(triggers) - len(gated)
    print(
        f"\nremovidos pelo gate: {removed} de {len(triggers)} "
        f"({removed / max(len(triggers), 1) * 100:.1f}%)"
    )
    if rows:
        print(f"\n== {len(rows)} pernas em que research e gate discordam ==")
        for row in rows[:20]:
            print(
                f"  {row['symbol']:<9} {row['timeframe']:<4} {row['leg']:<26} "
                f"research={row['research']:<9} gate={row['producao']!s:<5} "
                f"bos={row['run_bos']} desl_research={row['deslocamento_research_atr']}"
            )
    if args.json:
        args.json.write_text(json.dumps({"matriz": matrix, "divergencias": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
