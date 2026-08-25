"""Walk-forward and PBO for the EMA9-slope filter on the block reclaim.

The symbol holdout already answered "does it hold on other assets" (64.5%
against 42.1%). This answers the other question, which is not the same one:
**does it hold in every stretch of time, or only in the one the sample
happened to cover?** That distinction has cost this project a finding before
-- the OB x VWAP confluence replicated across symbols and then turned out to
be a property of the weekly anchor.

Same machinery as `research/vwap_walkforward.py`: rolling folds with purge and
embargo, PBO by combinatorial purged CV, and a deflated Sharpe that discounts
the observed number for how many rules were tried.

**Every rule below is declared here before the run**, losers included. Leaving
out the axes that already failed (EMA50, EMA200, the regime side) would shrink
the trial count and inflate the deflation, which is the cheapest way to fake
this test.

Run:
    poetry run python -m research.ema9_walkforward --trades /tmp/trend_lag.json
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

GATE = 1.0


def _pos(row: dict, field: str, thr: float = 0.0) -> bool:
    v = row.get(field)
    return v is not None and v > thr


#: Declared before the run. The `_lag` variants exclude the trigger candle from
#: the slope; the raw ones include it. Both are here because the pair is the
#: tautology test, and hiding the one that could be circular would be choosing
#: the answer.
RULES: dict[str, object] = {
    "base": lambda r: True,
    # the finding
    "ema9_lag>0": lambda r: _pos(r, "ema9_slope_lag1"),
    "ema9_lag>0.25": lambda r: _pos(r, "ema9_slope_lag1", 0.25),
    "ema9_lag>0.5": lambda r: _pos(r, "ema9_slope_lag1", 0.5),
    # the un-lagged version, which could be the trigger restated
    "ema9_raw>0": lambda r: _pos(r, "ema9_slope"),
    # its complement, which must be bad if the finding is real
    "ema9_lag<=0": lambda r: r.get("ema9_slope_lag1") is not None
    and r["ema9_slope_lag1"] <= 0,
    # the weaker sibling and the combinations
    "vwap_lag>0": lambda r: _pos(r, "vwap_slope_lag1"),
    "ema9+vwap": lambda r: _pos(r, "ema9_slope_lag1") and _pos(r, "vwap_slope_lag1"),
    "ema9+acum15": lambda r: _pos(r, "ema9_slope_lag1")
    and (r.get("vwap_candles") or 0) >= 15,
    # the incumbent production filter: the number to beat
    "acum15": lambda r: (r.get("vwap_candles") or 0) >= 15,
    # the axes that already failed, kept in the trial count
    "ema50>0": lambda r: _pos(r, "ema50_slope"),
    "ema200>0": lambda r: _pos(r, "ema200_slope"),
    "regime_side": lambda r: r.get("regime_side") is True,
}


def daily_matrix(trades, rules):
    by_rule = {n: defaultdict(list) for n in rules}
    for row in trades:
        day = datetime.fromisoformat(row["timestamp"]).date()
        for name, keep in rules.items():
            if keep(row):
                by_rule[name][day].append(row["r2_h40"])
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
    rows = [r for r in rows if r["r_atr"] <= GATE and r.get("r2_h40") is not None]
    if a.sample != "all":
        rows = [r for r in rows if r["sample"] == a.sample]
    days_span = {datetime.fromisoformat(r["timestamp"]).date() for r in rows}
    print(f"{len(rows)} trades no gate r_atr<={GATE}, amostra={a.sample}, "
          f"{min(days_span)} .. {max(days_span)} ({len(days_span)} dias com trade)")

    matrix, days, names = daily_matrix(rows, RULES)
    cfg = WalkForwardConfig(
        train_size=a.train_days, test_size=a.test_days, step_size=a.step_days,
        purge_size=a.purge_days, embargo_size=a.embargo_days, window_type="rolling",
    )
    validator = WalkForwardValidator(cfg)
    picked, oos, ins = [], [], []
    fixed = {n: [] for n in names}
    print(f"\n{'fold':>4} {'treino':>23} {'teste':>23} {'escolhida':>16} "
          f"{'SR tr':>7} {'SR te':>7}")
    for fold in validator.split(len(matrix)):
        tr, te = fold.train_indices, fold.test_indices
        train_sr = [compute_sharpe(matrix[tr, j]) for j in range(len(names))]
        best = int(np.argmax(train_sr))
        picked.append(names[best])
        ins.append(train_sr[best])
        oos.append(compute_sharpe(matrix[te, best]))
        for j, n in enumerate(names):
            fixed[n].extend(matrix[te, j].tolist())
        print(f"{fold.fold_idx:>4} {str(days[tr[0]])}..{str(days[tr[-1]])} "
              f"{str(days[te[0]])}..{str(days[te[-1]])} {names[best]:>16} "
              f"{train_sr[best]:>7.2f} {oos[-1]:>7.2f}")
    print(f"\n  folds {len(oos)}   SR treino {np.mean(ins):.2f}   "
          f"SR teste {np.mean(oos):.2f}   degradacao {np.mean(oos)-np.mean(ins):+.2f}")
    print(f"  escolhidas: {', '.join(sorted(set(picked)))}")
    print(f"  folds com OOS positivo: {sum(1 for s in oos if s > 0)}/{len(oos)}")

    print("\n  out-of-sample agrupado, cada regra FIXA em todos os folds:")
    print(f"    {'regra':>16} {'SR':>7} {'R medio':>9} {'dias':>6}")
    order = sorted(names, key=lambda n: -compute_sharpe(np.array(fixed[n])))
    for n in order:
        arr = np.array(fixed[n])
        print(f"    {n:>16} {compute_sharpe(arr):>7.2f} {arr.mean():>9.4f} {len(arr):>6}")

    pbo = probability_of_backtest_overfitting(matrix, n_groups=a.pbo_groups)
    print(f"\nPBO ({a.pbo_groups} grupos): {pbo.pbo:.3f}   "
          f"({'OVERFIT' if pbo.pbo > 0.5 else 'abaixo da linha de 0.5'})")
    best_name = max(names, key=lambda n: compute_sharpe(np.array(fixed[n])))
    arr = np.array(fixed[best_name])
    from scipy.stats import kurtosis as _kurt
    from scipy.stats import skew as _skew
    dsr = deflated_sharpe_ratio(
        observed_sr=compute_sharpe(arr), num_trials=len(names),
        backtest_length=len(arr), skewness=float(_skew(arr)),
        kurtosis=float(_kurt(arr, fisher=False)),
    )
    veredito = "significativo" if dsr.p_value < 0.05 else "NAO significativo"
    print(f"deflated Sharpe da melhor ({best_name}): SR {dsr.observed_sr:.2f}, "
          f"p={dsr.p_value:.4f}  ({veredito})")


if __name__ == "__main__":
    main()
