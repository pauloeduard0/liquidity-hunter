"""Walk-forward for the two questions the gate x alvo grid raised.

`research/trend_context.py` measured, across disjoint `r_atr` bands, that

1. the **3R target** beats 2.5R and 2R inside the production gate, on the
   mean, the median, the top-5%-winsorized mean and the daily mean, in both
   symbol halves -- which contradicts the earlier exit study, where 2R beat
   eleven competing management rules on M15; and
2. the **1.0-1.5 band**, today discarded by the gate, turns positive in both
   halves once the EMA9 filter is applied (+0.116 search, +0.227 holdout),
   while its unfiltered version sits at zero.

Both were read off the same cross-section they were chosen from, which is
exactly how the EMA9 filter's own "20pp" claim shrank to a wash once time was
the axis instead of symbols. So: rolling folds with purge and embargo, PBO by
combinatorial purged CV, deflated Sharpe over the full trial count.

Every rule is declared below **before** the run, losers included -- the wide
gate without the filter, and the targets that the grid already says lose.
Trimming them would shrink the trial count and inflate the deflation.

Unlike `ema9_walkforward.py`, the daily series here is **net of cost**
(`COST_PCT / r_pct` per trade): comparing targets while ignoring cost would
flatter the wider one, which pays the same fee over a longer move.

Run:
    poetry run python -m research.target_gate_walkforward --trades /tmp/grid.json
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


def _ema9(row: dict) -> bool:
    v = row.get("ema9_slope_lag1")
    return v is not None and v > 0


def _rules() -> dict[str, tuple[object, str]]:
    """`name -> (keep predicate, outcome key)`, declared before the run."""
    out: dict[str, tuple[object, str]] = {}
    for gate in (1.0, 1.5):
        for filt, fname in ((lambda r: True, "todos"), (_ema9, "ema9")):
            for tag, target in (("2", "2R"), ("25", "2.5R"), ("3", "3R")):
                name = f"gate{gate}·{fname}·{target}"
                out[name] = (
                    lambda r, _g=gate, _f=filt: r["r_atr"] <= _g and _f(r),
                    f"r{tag}_h40",
                )
    # A faixa nova SOZINHA (1.0 < r_atr <= 1.5): e ela que precisa se sustentar,
    # nao a uniao com o nucleo que ja se sabe bom.
    for tag, target in (("2", "2R"), ("25", "2.5R"), ("3", "3R")):
        out[f"faixa1.0-1.5·ema9·{target}"] = (
            lambda r: 1.0 < r["r_atr"] <= 1.5 and _ema9(r),
            f"r{tag}_h40",
        )
    return out


RULES = _rules()


def daily_matrix(trades, rules):
    by_rule = {n: defaultdict(list) for n in rules}
    for row in trades:
        day = datetime.fromisoformat(row["timestamp"]).date()
        for name, (keep, key) in rules.items():
            if keep(row) and row.get(key) is not None:
                by_rule[name][day].append(row[key] - COST_PCT / row["r_pct"])
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
    p.add_argument("--train-days", type=int, default=60)
    p.add_argument("--test-days", type=int, default=20)
    p.add_argument("--step-days", type=int, default=20)
    p.add_argument("--purge-days", type=int, default=1)
    p.add_argument("--embargo-days", type=int, default=2)
    p.add_argument("--pbo-groups", type=int, default=6)
    p.add_argument("--sample", default="all", choices=["all", "search", "holdout"])
    a = p.parse_args()

    rows = json.loads(Path(a.trades).read_text())
    rows = [r for r in rows if r["r_atr"] <= 1.5]
    if a.sample != "all":
        rows = [r for r in rows if r["sample"] == a.sample]
    span = {datetime.fromisoformat(r["timestamp"]).date() for r in rows}
    print(f"{len(rows)} trades com r_atr<=1.5, amostra={a.sample}, "
          f"{min(span)} .. {max(span)} ({len(span)} dias com trade)")
    print(f"{len(RULES)} regras declaradas\n")

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
    chosen = defaultdict(int)
    for n in picked:
        chosen[n] += 1
    print("  escolhida por fold: " + ", ".join(
        f"{k} x{c}" for k, c in sorted(chosen.items(), key=lambda x: -x[1])))

    pbo = probability_of_backtest_overfitting(matrix, n_groups=a.pbo_groups)
    print(f"\nPBO {pbo.pbo:.3f}  (rank OOS medio {pbo.mean_oos_rank:.3f})")

    print(f"\n{'regra':<26} {'R/dia':>8} {'SR anual':>9} {'dias c/ trade':>14}")
    pooled = []
    for i, n in enumerate(names):
        col = matrix[:, i]
        active = int((col != 0).sum())
        sr = compute_sharpe(col)
        pooled.append((sr, n, float(col.mean()), active))
        print(f"{n:<26} {col.mean():>+8.4f} {sr:>9.2f} {active:>14}")

    best = max(pooled)
    dsr = deflated_sharpe_ratio(
        observed_sr=best[0], num_trials=len(names),
        backtest_length=len(days),
        skewness=float(((matrix[:, names.index(best[1])] -
                         matrix[:, names.index(best[1])].mean()) ** 3).mean() /
                       (matrix[:, names.index(best[1])].std() ** 3 + 1e-12)),
        kurtosis=float(((matrix[:, names.index(best[1])] -
                         matrix[:, names.index(best[1])].mean()) ** 4).mean() /
                       (matrix[:, names.index(best[1])].std() ** 4 + 1e-12)),
    )
    print(f"\nmelhor: {best[1]} (SR {best[0]:.2f})")
    print(f"deflated Sharpe (P[SR>0] pos-deflacao)={dsr.dsr_pvalue:.4f} "
          f"sobre {len(names)} tentativas")


if __name__ == "__main__":
    main()
