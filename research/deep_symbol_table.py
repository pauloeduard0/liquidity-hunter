"""As tabelas por simbolo do estudo do deep, para a inspecao trade a trade.

Le o JSON que `research.deep_reclaim` grava e reporta **um simbolo por vez**,
sem juntar nada: quantos gatilhos aconteceram, em que janela, e o acerto por
faixa de `r_atr` em cada alvo (2R, 2,5R, 3R).

Duas coisas que este relatorio NAO e, ditas aqui para nao serem lidas como se
fossem. Ele **nao valida**: dois simbolos escolhidos a dedo, sem holdout e sem
walk-forward, e uma lente para levantar hipotese, nao para confirmar nenhuma.
E o acerto sozinho **nao decide**: alvo mais largo acerta menos por
construcao, entao a coluna que compara alvos e o liquido, nunca a de acerto.

O `--csv` sai ordenado por data para os trades serem abertos no grafico, que
e o proposito -- refinar o setup olhando o que ele pegou.

Run:
    poetry run python -m research.deep_symbol_table /tmp/deep_btc_eth.json
    poetry run python -m research.deep_symbol_table /tmp/deep.json --csv /tmp/t.csv
"""

from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Sequence

#: Ida e volta, taker nas duas pontas -- o mesmo de `deep_reclaim`.
COST_PCT = 0.0010
BANDS = ((0.0, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, float("inf")))
TARGETS = (("2", 2.0), ("25", 2.5), ("3", 3.0))
MIN_N = 20


def _stats(rows: Sequence[dict], tag: str, target: float, h: int) -> tuple[float, float]:
    """Acerto e R liquido medio, com o custo cobrado em R sobre o R do trade."""
    key = f"r{tag}_h{h}"
    hit = sum(1 for r in rows if r[key] >= target) / len(rows)
    net = sum(r[key] - COST_PCT / r["r_pct"] for r in rows) / len(rows)
    return hit, net


def report(rows: list[dict], horizon: int) -> None:
    live = [r for r in rows if r["arm"] != "aleatorio"]
    rnd = [r for r in rows if r["arm"] == "aleatorio"]
    for symbol in sorted({r["symbol"] for r in live}):
        mine = sorted((r for r in live if r["symbol"] == symbol),
                      key=lambda r: r["timestamp"])
        ctrl = [r for r in rnd if r["symbol"] == symbol]
        bull = sum(1 for r in mine if r["direction"] == "bullish")
        print(f"\n{'=' * 78}\n{symbol}  ·  {len(mine)} trades  ·  "
              f"{mine[0]['timestamp'][:10]} a {mine[-1]['timestamp'][:10]}")
        print(f"  compra {bull} / venda {len(mine) - bull}"
              f"  ·  horizonte {horizon} velas  ·  custo {COST_PCT:.2%}")
        print(f"\n  {'faixa r_atr':<14}{'n':>6}"
              + "".join(f"{'  ' + t + 'R acerto':>13}{'liquido':>9}" for t, _ in TARGETS))
        for lo, hi in [*BANDS, (0.0, float("inf"))]:
            sub = [r for r in mine if lo <= r["r_atr"] < hi]
            label = "TODOS" if (lo, hi) == (0.0, float("inf")) else (
                f"{lo:g}-{hi:g}" if hi != float("inf") else f"{lo:g}+")
            if not sub:
                continue
            line = f"  {label:<14}{len(sub):>6}"
            for tag, target in TARGETS:
                if len(sub) < MIN_N:
                    line += f"{'poucos':>13}{'':>9}"
                    continue
                hit, net = _stats(sub, tag, target, horizon)
                line += f"{hit:>12.1%}{net:>+9.3f}"
            print(line)
        if len(ctrl) >= MIN_N:
            line = f"  {'(aleatorio)':<14}{len(ctrl):>6}"
            for tag, target in TARGETS:
                hit, net = _stats(ctrl, tag, target, horizon)
                line += f"{hit:>12.1%}{net:>+9.3f}"
            print(line)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("path")
    p.add_argument("--horizon", type=int, default=40)
    p.add_argument("--csv", default=None, help="grava os trades para inspecao")
    a = p.parse_args()
    rows = json.load(open(a.path))
    report(rows, a.horizon)
    if a.csv:
        live = sorted((r for r in rows if r["arm"] != "aleatorio"),
                      key=lambda r: (r["symbol"], r["timestamp"]))
        with open(a.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(live[0]))
            w.writeheader()
            w.writerows(live)
        print(f"\n{len(live)} trades -> {a.csv}")


if __name__ == "__main__":
    main()
