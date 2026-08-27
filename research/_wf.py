"""Motor de walk-forward compartilhado pelos estudos deste diretorio.

Extraido na quarta copia. Os tres scripts anteriores
(`ema9_walkforward`, `target_gate_walkforward`, `vwap_age_walkforward`) trazem
o mesmo corpo inline; ficam como estao para nao mexer no que ja foi medido, e
migram quando forem tocados por outro motivo.

Uma regra e `(nome, predicado, chave do resultado)`. A serie diaria e sempre
**liquida de custo** (`cost_pct / r_pct` por trade): comparar regras que mudam
o numero de operacoes sobre R bruto favorece sistematicamente a que opera mais.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

SKILL = Path(__file__).resolve().parents[1] / ".claude/skills/walk-forward-validation/scripts"
if str(SKILL) not in sys.path:
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

Rule = Callable[[dict[str, Any]], bool]


def daily_matrix(
    trades: Sequence[dict], rules: dict[str, Rule], key: str, cost_pct: float,
    cost_key: str | None = None,
) -> tuple[np.ndarray, list, list[str]]:
    """`cost_key` le o custo em R de cada linha em vez de derivar de um custo
    fixo. E o que o CFD exige: o spread nao e uma constante do mercado, e uma
    propriedade da barra, e num instrumento caro ele muda o SINAL do
    resultado (ver `docs/block_reclaim.md`)."""
    by_rule: dict[str, dict] = {n: defaultdict(list) for n in rules}
    for row in trades:
        if row.get(key) is None:
            continue
        day = datetime.fromisoformat(row["timestamp"]).date()
        net = row[key] - (row[cost_key] if cost_key else cost_pct / row["r_pct"])
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


def run_walkforward(
    trades: Sequence[dict], rules: dict[str, Rule], *, key: str, cost_pct: float,
    cost_key: str | None = None, train_days: int = 60, test_days: int = 20, step_days: int = 20,
    purge_days: int = 1, embargo_days: int = 2, pbo_groups: int = 6,
) -> None:
    """Roda folds + PBO + Sharpe deflacionado e imprime o relatorio."""
    matrix, days, names = daily_matrix(trades, rules, key, cost_pct, cost_key)
    cfg = WalkForwardConfig(
        train_size=train_days, test_size=test_days, step_size=step_days,
        purge_size=purge_days, embargo_size=embargo_days, window_type="rolling",
    )
    picked, oos, ins = [], [], []
    for fold in WalkForwardValidator(cfg).split(len(matrix)):
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

    pbo = probability_of_backtest_overfitting(matrix, n_groups=pbo_groups)
    print(f"\nPBO {pbo.pbo:.3f}  (rank OOS medio {pbo.mean_oos_rank:.3f})")

    counts = {n: sum(1 for r in trades if rules[n](r)) for n in names}
    print(f"\n{'regra':<26}{'R/dia':>9}{'SR anual':>10}{'dias':>7}{'trades':>8}"
          f"{'R total':>10}")
    best_sr, best_name = -9e9, ""
    for i, n in enumerate(names):
        col = matrix[:, i]
        sr = compute_sharpe(col)
        if sr > best_sr:
            best_sr, best_name = sr, n
        print(f"{n:<26}{col.mean():>+9.4f}{sr:>10.2f}{int((col != 0).sum()):>7}"
              f"{counts[n]:>8}{col.sum():>+10.1f}")

    col = matrix[:, names.index(best_name)]
    dsr = deflated_sharpe_ratio(
        observed_sr=best_sr, num_trials=len(names), backtest_length=len(days),
        skewness=float(((col - col.mean()) ** 3).mean() / (col.std() ** 3 + 1e-12)),
        kurtosis=float(((col - col.mean()) ** 4).mean() / (col.std() ** 4 + 1e-12)),
    )
    print(f"\nmelhor: {best_name} (SR {best_sr:.2f})  "
          f"deflated P[SR>0]={dsr.dsr_pvalue:.4f} sobre {len(names)} tentativas")
