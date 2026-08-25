"""Quantas velas a VWAP precisa ter acumulado para o reclaim significar algo?

A producao usa `vwap_candles >= 15` (`acum15`), validado no seu proprio
walk-forward. Uma inspecao visual de um trade stopado (AVAXUSDT, 17/08/2026
21:00 UTC-3) mostrou de onde vinha metade do lixo que esse corte remove: a
VWAP de sessao **reancora a meia-noite UTC**, salta, e cruza o preco sem que o
preco tenha feito nada. O gatilho dispara pelo relogio.

Medido no corte transversal (M15, gate 1.0, alvo 2R): as entradas com a VWAP
de **uma unica vela** sao 15% de toda a populacao e o pior subgrupo do estudo
-- 34,2%/27,0% de acerto, liquido -0,194/-0,477. O defeito e real e replica.

Mas o `acum15` cobra caro por ele. A faixa **8-14 velas** e a melhor de todas
(65,5%/60,9%), e o corte em 15 a joga fora junto com o lixo de 1-3 velas. Este
script pergunta se o limiar deveria ser muito mais baixo.

Regras declaradas antes da rodada, incluindo o proprio defeito (`vwap<=3`) como
controle negativo: se ele nao for o pior de todos, a leitura acima esta errada.

Run:
    poetry run python -m research.vwap_age_walkforward --trades /tmp/grid.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

SKILL = Path(__file__).resolve().parents[1] / ".claude/skills/walk-forward-validation/scripts"
sys.path.insert(0, str(SKILL))

from overfit_detector import (  # noqa: E402
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
)
from walk_forward import (  # noqa: E402
    WalkForwardConfig,
    WalkForwardValidator,
    compute_sharpe,
)

COST_PCT = 0.0010
KEY = "r2_h40"


def _age(row: dict) -> int:
    return int(row.get("vwap_candles") or 0)


def _ema9(row: dict) -> bool:
    v = row.get("ema9_slope_lag1")
    return v is not None and v > 0


RULES: dict[str, object] = {
    "sem filtro": lambda r: True,
    "vwap>=2": lambda r: _age(r) >= 2,
    "vwap>=4": lambda r: _age(r) >= 4,
    "vwap>=6": lambda r: _age(r) >= 6,
    "vwap>=8": lambda r: _age(r) >= 8,
    "vwap>=12": lambda r: _age(r) >= 12,
    "vwap>=15 (atual)": lambda r: _age(r) >= 15,
    "vwap>=20": lambda r: _age(r) >= 20,
    # o defeito, isolado: tem de ser o pior de todos
    "vwap<=3 (o defeito)": lambda r: _age(r) <= 3,
    # cruzamentos com o veto da EMA9
    "ema9": _ema9,
    "ema9+vwap>=4": lambda r: _ema9(r) and _age(r) >= 4,
    "ema9+vwap>=15": lambda r: _ema9(r) and _age(r) >= 15,
}


def daily_matrix(trades, rules):
    by_rule = {n: defaultdict(list) for n in rules}
    for row in trades:
        day = datetime.fromisoformat(row["timestamp"]).date()
        net = row[KEY] - COST_PCT / row["r_pct"]
        for name, keep in rules.items():
            if keep(row):
                by_rule[name][day].append(net)
    days = sorted({datetime.fromisoformat(r["timestamp"]).date() for r in trades})
    names = list(rules)
    matrix = np.array(
        [[float(np.mean(by_rule[n][d])) if by_rule[n][d] else 0.0 for n in names]
         for d in days]
    )
    return matrix, days, names


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trades", required=True)
    p.add_argument("--gate", type=float, default=1.0)
    p.add_argument("--train-days", type=int, default=60)
    p.add_argument("--test-days", type=int, default=20)
    p.add_argument("--step-days", type=int, default=20)
    p.add_argument("--purge-days", type=int, default=1)
    p.add_argument("--embargo-days", type=int, default=2)
    p.add_argument("--pbo-groups", type=int, default=6)
    p.add_argument("--sample", default="all", choices=["all", "search", "holdout"])
    a = p.parse_args()

    rows = [r for r in json.loads(Path(a.trades).read_text())
            if r["r_atr"] <= a.gate and r.get(KEY) is not None]
    if a.sample != "all":
        rows = [r for r in rows if r["sample"] == a.sample]
    span = {datetime.fromisoformat(r["timestamp"]).date() for r in rows}
    print(f"{len(rows)} trades, gate<={a.gate}, amostra={a.sample}, "
          f"{min(span)} .. {max(span)}  |  {len(RULES)} regras declaradas\n")

    matrix, days, names = daily_matrix(rows, RULES)
    cfg = WalkForwardConfig(
        train_size=a.train_days, test_size=a.test_days, step_size=a.step_days,
        purge_size=a.purge_days, embargo_size=a.embargo_days, window_type="rolling",
    )
    validator = WalkForwardValidator(cfg)
    picked, oos, ins = [], [], []
    for fold in validator.split(len(matrix)):
        tr, te = fold.train_indices, fold.test_indices
        train_sr = [compute_sharpe(matrix[tr, j]) for j in range(len(names))]
        best = int(np.argmax(train_sr))
        picked.append(names[best])
        ins.append(train_sr[best])
        oos.append(compute_sharpe(matrix[te, best]))
    print(f"folds {len(oos)}  SR treino {np.mean(ins):.2f} -> teste {np.mean(oos):.2f}"
          f"  degradacao {np.mean(oos) - np.mean(ins):+.2f}"
          f"  folds positivos {sum(1 for s in oos if s > 0)}/{len(oos)}")
    chosen: dict[str, int] = defaultdict(int)
    for n in picked:
        chosen[n] += 1
    print("  escolhida por fold: " + ", ".join(
        f"{k} x{c}" for k, c in sorted(chosen.items(), key=lambda x: -x[1])))

    pbo = probability_of_backtest_overfitting(matrix, n_groups=a.pbo_groups)
    print(f"\nPBO {pbo.pbo:.3f}  (rank OOS medio {pbo.mean_oos_rank:.3f})")

    print(f"\n{'regra':<22}{'R/dia':>9}{'SR anual':>10}{'dias':>7}{'trades':>8}"
          f"{'R total':>10}")
    counts = {n: sum(1 for r in rows if RULES[n](r)) for n in names}
    best = (-9e9, "")
    for i, n in enumerate(names):
        col = matrix[:, i]
        sr = compute_sharpe(col)
        best = max(best, (sr, n))
        print(f"{n:<22}{col.mean():>+9.4f}{sr:>10.2f}{int((col != 0).sum()):>7}"
              f"{counts[n]:>8}{col.sum():>+10.1f}")

    j = names.index(best[1])
    col = matrix[:, j]
    dsr = deflated_sharpe_ratio(
        observed_sr=best[0], num_trials=len(names), backtest_length=len(days),
        skewness=float(((col - col.mean()) ** 3).mean() / (col.std() ** 3 + 1e-12)),
        kurtosis=float(((col - col.mean()) ** 4).mean() / (col.std() ** 4 + 1e-12)),
    )
    print(f"\nmelhor: {best[1]} (SR {best[0]:.2f})  "
          f"deflated P[SR>0]={dsr.dsr_pvalue:.4f} sobre {len(names)} tentativas")


if __name__ == "__main__":
    main()
