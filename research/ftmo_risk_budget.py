"""A % por operacao cabe nos limites da corretora? A pergunta que uma amostra so nao responde.

`ftmo_portfolio.py` reporta o drawdown maximo **do caminho que aconteceu**.
Subir o risco por operacao multiplica esse numero linearmente, e a tentacao e
comparar o resultado com o teto e parar por ai. Isso subestima o problema: o
caminho historico e uma amostra de tamanho **um**, e o maximo de uma amostra
de um nao e um limite superior de nada.

Aqui a serie diaria e reamostrada para estimar a **probabilidade de estourar**
cada limite, que e a grandeza que decide.

Duas reamostragens, de proposito, porque discordam pelo motivo certo:

* **iid** -- sorteia dias soltos. Destroi qualquer agrupamento de perdas e por
  isso **subestima** o drawdown. Serve de piso.
* **blocos** -- sorteia trechos contiguos de 5 dias, preservando o
  agrupamento. Serve de estimativa honesta.

Se as duas concordarem, o agrupamento nao importa nesta serie. Se a de blocos
for muito pior, ele importa e a iid estava mentindo.
"""

from __future__ import annotations

import argparse
import random
import statistics as st
from collections import defaultdict
from datetime import datetime

from research.ftmo_portfolio_walkforward import build_rows

#: Os limites da corretora, como fracao da conta.
MAX_DRAWDOWN = 0.10
MAX_DAILY_LOSS = 0.05

#: Dias por caminho simulado. Um desafio dura cerca disso, e drawdown maximo
#: cresce com o horizonte -- medir 10 anos responderia outra pergunta.
PATH_DAYS = 120
PATHS = 20_000
#: Tamanho do bloco na reamostragem por blocos, em dias.
BLOCK = 5


def daily_series(rows: list[dict]) -> list[float]:
    """R somado por dia de calendario. Dia sem operacao rende zero e existe."""
    by_day: dict[str, float] = defaultdict(float)
    for row in rows:
        by_day[datetime.fromisoformat(row["timestamp"]).date().isoformat()] += row["net"]
    return [by_day[d] for d in sorted(by_day)]


def max_drawdown(path: list[float]) -> float:
    peak = equity = 0.0
    worst = 0.0
    for value in path:
        equity += value
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def simulate(series: list[float], risk: float, *, block: int, paths: int,
             days: int, seed: int = 20260827) -> dict:
    rng = random.Random(seed)
    n = len(series)
    breaches_dd = breaches_day = 0
    drawdowns, finals = [], []
    for _ in range(paths):
        path: list[float] = []
        while len(path) < days:
            start = rng.randrange(n)
            path.extend(series[start:start + block] if block > 1
                        else [series[start]])
            if block > 1 and start + block > n:  # embrulha o fim na cabeca
                path.extend(series[:start + block - n])
        path = path[:days]
        dd = max_drawdown(path) * risk
        worst_day = min(path) * risk
        drawdowns.append(dd)
        finals.append(sum(path) * risk)
        breaches_dd += dd > MAX_DRAWDOWN
        breaches_day += -worst_day > MAX_DAILY_LOSS
    drawdowns.sort()
    return {
        "p_breach_dd": breaches_dd / paths,
        "p_breach_day": breaches_day / paths,
        "dd_median": drawdowns[paths // 2],
        "dd_p95": drawdowns[int(0.95 * paths)],
        "dd_p99": drawdowns[int(0.99 * paths)],
        "final_median": st.median(finals),
        "p_target": sum(1 for f in finals if f >= 0.10) / paths,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--risks", type=float, nargs="*",
                        default=[0.0025, 0.0035, 0.0050])
    parser.add_argument("--paths", type=int, default=PATHS)
    parser.add_argument("--days", type=int, default=PATH_DAYS)
    args = parser.parse_args()

    series = daily_series(build_rows())
    print(f"\n{'=' * 92}\nORCAMENTO DE RISCO   {len(series)} dias medidos   "
          f"{args.paths} caminhos de {args.days} dias\n"
          f"teto de drawdown {MAX_DRAWDOWN:.0%}   teto diario {MAX_DAILY_LOSS:.0%}\n"
          f"{'=' * 92}")
    for label, block in (("iid (sem agrupamento -- PISO)", 1),
                         (f"blocos de {BLOCK} dias (honesto)", BLOCK)):
        print(f"\n  {label}\n")
        print(f"  {'risco':>7}{'dd mediano':>13}{'dd p95':>10}{'dd p99':>10}"
              f"{'P(estoura 10%)':>16}{'P(estoura 5%/dia)':>19}{'P(+10%)':>10}")
        for risk in args.risks:
            r = simulate(series, risk, block=block, paths=args.paths,
                         days=args.days)
            print(f"  {risk:>7.2%}{r['dd_median']:>13.2%}{r['dd_p95']:>10.2%}"
                  f"{r['dd_p99']:>10.2%}{r['p_breach_dd']:>16.1%}"
                  f"{r['p_breach_day']:>19.1%}{r['p_target']:>10.1%}")
    print()


if __name__ == "__main__":
    main()
