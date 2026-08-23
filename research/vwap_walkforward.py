"""Walk-forward validation of the aggression veto.

What this adds over `research/vwap_ob_pinbar.py`
------------------------------------------------
That study answered *is the edge there*, and checked it out of sample by
holding out **symbols**. This one asks the question symbols cannot answer: is
it there in every stretch of **time**, or only in the regime this sample
happened to cover? A rule can hold across forty symbols and still be an
artefact of one quarter, because the symbols are not independent of each other
-- they are the same market.

The aggression axis is computed from candles alone (the same
`sum(volume_delta) / sum(volume)` window `MarketControlAnalyzer` uses), with no
open interest, which is what lets this run over 260 days instead of the ~30 the
OI retention allows.

Design
------
Trades are exported dated from the measurement, bucketed into a daily return
series per candidate rule (mean R of that day's trades, zero on days with
none), and fed through:

* **walk-forward** -- on each fold the best rule is *selected on train* and
  *scored on test*, which validates the whole procedure a researcher follows,
  not just the rule they ended up with;
* **purge + embargo** -- a trade's outcome takes up to 40 candles (10h on M15)
  to resolve, so training days whose labels reach into the test window are
  dropped, and a further gap is left for serial correlation;
* **PBO** (combinatorial purged CV) -- how often the in-sample best rule lands
  below the out-of-sample median. Above 0.5 is more-likely-than-not overfit;
* **deflated Sharpe** -- the observed Sharpe discounted for how many rules were
  tried, which for an effect found by slicing is the honest correction.

Usage
-----
    poetry run python research/vwap_walkforward.py --trades trades_15m.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime
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

#: `MarketControlAnalyzer`'s own directional floor: below it the aggression is
#: not called a side at all.
AGG_FLOOR = 0.06


def _against(row: dict) -> bool:
    """Whether the window's aggression opposed the trade's direction."""
    return row["agg_ratio"] < 0 if row["direction"] == "bullish" else row["agg_ratio"] > 0


def _strong(row: dict) -> bool:
    return abs(row["agg_ratio"]) >= AGG_FLOOR


def _tight(row: dict, limit: float) -> bool:
    """The block and the VWAP within `limit` ATR of each other."""
    return row.get("r_atr") is not None and row["r_atr"] <= limit


#: Symbols by measured spread tercile, from `research/measured_spreads.json`.
#: The liquidity split is declared as rules for the same reason the `r_atr`
#: threshold is: it was a slice someone looked at, and PBO only corrects for
#: the trials it is told about. An objective axis (measured spread) removes the
#: researcher's choice of which name counts as a major, but not the fact that
#: three more rules were tried.
def _terciles() -> dict[str, set[str]]:
    path = Path(__file__).parent / "measured_spreads.json"
    if not path.exists():
        return {}
    spreads = json.loads(path.read_text())
    ordered = sorted(spreads, key=lambda s: spreads[s]["spread"])
    third = max(1, len(ordered) // 3)
    return {
        "tight": set(ordered[:third]),
        "middle": set(ordered[third:2 * third]),
        "wide": set(ordered[2 * third:]),
    }


TERCILES = _terciles()


#: The rules a researcher plausibly considered. PBO is only honest if the
#: discarded candidates are declared alongside the kept one -- the count of
#: trials is what the deflation corrects for.
RULES: dict[str, object] = {
    "all": lambda r: True,
    "agg-against": _against,
    "agg-with": lambda r: not _against(r),
    "agg-against-floor": lambda r: _against(r) and _strong(r),
    "agg-with-floor": lambda r: not _against(r) and _strong(r),
    "vwap|agg-against": lambda r: r["arm"] == "vwap" and _against(r),
    "ob|agg-against": lambda r: r["arm"] == "ob" and _against(r),
    "eql|agg-against": lambda r: r["arm"] == "eql" and _against(r),
    "ob+eql|agg-against": lambda r: r["arm"] in ("ob", "eql") and _against(r),
    # The block family: the arm, and the `r_atr` threshold that was read off a
    # curve. Declared here because PBO corrects for how many rules were tried,
    # and the threshold was one of the things tried -- leaving it out would
    # flatter the very choice this study is defending.
    "ob|r<=0.5": lambda r: r["arm"] == "ob" and _tight(r, 0.5),
    "ob|r<=1.0": lambda r: r["arm"] == "ob" and _tight(r, 1.0),
    "ob|r<=1.5": lambda r: r["arm"] == "ob" and _tight(r, 1.5),
    "ob|r<=2.0": lambda r: r["arm"] == "ob" and _tight(r, 2.0),
    "vwap|r<=1.0": lambda r: r["arm"] == "vwap" and _tight(r, 1.0),
    # The charted rule: the same block, triggered by a pinbar on EITHER shared
    # line once the EMA(9) has crossed the VWAP. Declared whole, and its
    # `both`-route subset declared beside it -- that subset measures far better
    # in the search half and is exactly the kind of selection this procedure
    # exists to price, so leaving it out of the trial count would flatter the
    # rule being defended.
    "ob-either|r<=1.0": lambda r: r["arm"] == "ob-either" and _tight(r, 1.0),
    # The pinbar grades. `ob-pin2` accepts the union of three definitions of the
    # trigger candle -- the legacy one this project has measured, the golden
    # two-thirds tail, and a level-2 body-heavy bar with a capped nose -- and
    # each grade is declared beside the union so the procedure can price the
    # subset as well as the whole.
    "ob-pin2|r<=1.0": lambda r: r["arm"] == "ob-pin2" and _tight(r, 1.0),
    "ob-pin2|r<=1.0|l2only": (
        lambda r: r["arm"] == "ob-pin2" and _tight(r, 1.0)
        and r.get("pinbar_grade") == "l2"
    ),
    "ob-pin2|r<=1.0|hasl1": (
        lambda r: r["arm"] == "ob-pin2" and _tight(r, 1.0)
        and "l1" in (r.get("pinbar_grade") or "")
    ),
    "ob-pin2|r<=1.0|legacy": (
        lambda r: r["arm"] == "ob-pin2" and _tight(r, 1.0)
        and "legacy" in (r.get("pinbar_grade") or "")
    ),
    # The H4 widening question: the r_atr decile diagnostic (2026-08-23)
    # showed H4 net-positive even ungated, so the wider tiers are declared
    # as trials of their own -- announced before this run, not read off it.
    "ob-pin2|r<=1.5": lambda r: r["arm"] == "ob-pin2" and _tight(r, 1.5),
    "ob-pin2|r<=2.0": lambda r: r["arm"] == "ob-pin2" and _tight(r, 2.0),
    "ob-pin2|nogate": lambda r: r["arm"] == "ob-pin2",
    "ob-either|r<=1.0|both": (
        lambda r: r["arm"] == "ob-either" and _tight(r, 1.0)
        and r.get("trigger_line") == "both"
    ),
    "ob-either|r<=1.0|ema": (
        lambda r: r["arm"] == "ob-either" and _tight(r, 1.0)
        and r.get("trigger_line") == "ema"
    ),
    "eql|r<=1.0": lambda r: r["arm"] == "eql" and _tight(r, 1.0),
}
# The position-management family. Same trades, different exit rule, so each is
# a separate trial and PBO has to see them: "you can only win by protecting"
# is exactly the kind of claim that never gets counted against the trial
# budget.
for _v in ("be0.5", "be1.0", "be1.5", "partial", "trail1.0"):
    RULES[f"ob|r<=1.0@{_v}"] = (
        lambda r: r["arm"] == "ob" and _tight(r, 1.0)
    )

for _tier, _members in TERCILES.items():
    RULES[f"ob|r<=1.0|{_tier}"] = (
        lambda r, m=_members: r["arm"] == "ob" and _tight(r, 1.0) and r["symbol"] in m
    )


def daily_matrix(
    trades: list[dict], rules: dict[str, object]
) -> tuple[np.ndarray, list[date], list[str]]:
    """Daily mean R per rule, aligned on one calendar index.

    A day with no qualifying trade contributes zero, not a gap: the rule was
    live that day and simply did not fire, and dropping the day would let a
    selective rule silently skip the stretches it dislikes.
    """
    by_rule: dict[str, dict[date, list[float]]] = {n: defaultdict(list) for n in rules}
    for row in trades:
        day = datetime.fromisoformat(row["timestamp"]).date()
        for name, keep in rules.items():
            if keep(row):  # type: ignore[operator]
                # A rule may name a position-management variant after a `@`;
                # its payoff is that variant's, not the plain 2R one. Declared
                # this way so the management family is inside the trial count
                # rather than measured off to the side.
                variant = name.split("@")[1] if "@" in name else None
                payoff = (row.get("r_manage", {}).get(variant)
                          if variant else row["r_outcome"])
                if payoff is not None:
                    by_rule[name][day].append(payoff)

    days = sorted({datetime.fromisoformat(r["timestamp"]).date() for r in trades})
    names = list(rules)
    matrix = np.array(
        [[float(np.mean(by_rule[n][d])) if by_rule[n][d] else 0.0 for n in names]
         for d in days]
    )
    return matrix, days, names


def walk_forward(
    matrix: np.ndarray, days: list[date], names: list[str], cfg: WalkForwardConfig
) -> None:
    validator = WalkForwardValidator(cfg)
    picked: list[str] = []
    oos: list[float] = []
    in_sample: list[float] = []
    chosen_oos: list[float] = []
    fixed_oos: dict[str, list[float]] = {n: [] for n in names}

    print(f"\n{'fold':>4} {'train':>21} {'test':>21} {'picked':>18} "
          f"{'train SR':>9} {'test SR':>9}")
    for fold in validator.split(len(matrix)):
        tr, te = fold.train_indices, fold.test_indices
        train_sr = [compute_sharpe(matrix[tr, j]) for j in range(len(names))]
        best = int(np.argmax(train_sr))
        test_sr = compute_sharpe(matrix[te, best])
        picked.append(names[best])
        in_sample.append(train_sr[best])
        oos.append(test_sr)
        chosen_oos.extend(matrix[te, best].tolist())
        for j, n in enumerate(names):
            fixed_oos[n].extend(matrix[te, j].tolist())
        print(f"{fold.fold_idx:>4} {str(days[tr[0]]):>10}..{str(days[tr[-1]]):>10} "
              f"{str(days[te[0]]):>10}..{str(days[te[-1]]):>10} "
              f"{names[best]:>18} {train_sr[best]:>9.2f} {test_sr:>9.2f}")

    print(f"\n  folds {len(oos)}   mean train SR {np.mean(in_sample):>6.2f}"
          f"   mean test SR {np.mean(oos):>6.2f}"
          f"   degradation {np.mean(oos) - np.mean(in_sample):>+6.2f}")
    print(f"  picked: {', '.join(sorted(set(picked)))}")
    print(f"  folds with positive OOS Sharpe: {sum(1 for s in oos if s > 0)}/{len(oos)}")

    print("\n  pooled out-of-sample, each rule held fixed across all folds:")
    print(f"    {'rule':>20} {'SR':>7} {'mean R':>8} {'n days':>7}")
    for n in names:
        arr = np.array(fixed_oos[n])
        print(f"    {n:>20} {compute_sharpe(arr):>7.2f} {arr.mean():>8.4f} {len(arr):>7}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trades", required=True)
    p.add_argument("--train-days", type=int, default=60)
    p.add_argument("--test-days", type=int, default=20)
    p.add_argument("--step-days", type=int, default=20)
    p.add_argument("--purge-days", type=int, default=1,
                   help="a trade resolves in <=40 candles; drop train days whose "
                        "outcome reaches into the test window")
    p.add_argument("--embargo-days", type=int, default=2,
                   help="rule of thumb: >= 2x the label horizon")
    p.add_argument("--window", choices=["rolling", "expanding"], default="rolling")
    p.add_argument("--pbo-groups", type=int, default=6)
    args = p.parse_args()

    trades = json.loads(Path(args.trades).read_text())
    matrix, days, names = daily_matrix(trades, RULES)
    print(f"{len(trades)} trades, {len(days)} days "
          f"({days[0]} .. {days[-1]}), {len(names)} candidate rules")

    cfg = WalkForwardConfig(
        train_size=args.train_days, test_size=args.test_days,
        step_size=args.step_days, window_type=args.window,
        purge_size=args.purge_days, embargo_size=args.embargo_days,
    )
    walk_forward(matrix, days, names, cfg)

    pbo = probability_of_backtest_overfitting(matrix, n_groups=args.pbo_groups,
                                              n_test_groups=2)
    print(f"\nPBO ({args.pbo_groups} groups, C(n,2) paths): {pbo.pbo:.3f}"
          f"   ({'OVERFIT' if pbo.pbo > 0.5 else 'below the 0.5 line'})")

    best = max(names, key=lambda n: compute_sharpe(matrix[:, names.index(n)]))
    col = matrix[:, names.index(best)]
    sr = compute_sharpe(col)
    dsr = deflated_sharpe_ratio(
        observed_sr=sr, num_trials=len(names), backtest_length=len(col),
        skewness=float(((col - col.mean()) ** 3).mean() / (col.std() ** 3 + 1e-12)),
        kurtosis=float(((col - col.mean()) ** 4).mean() / (col.std() ** 4 + 1e-12)),
    )
    print(f"in-sample best rule: {best}  SR {sr:.2f}")
    print(
        f"deflated Sharpe: observed SR {dsr.observed_sr:.2f} vs an expected "
        f"max of {dsr.expected_max_sr:.2f} from {dsr.num_trials} trials on "
        f"noise alone -- p={dsr.dsr_pvalue:.4f}, "
        f"{'significant' if dsr.is_significant else 'NOT significant'}"
    )


if __name__ == "__main__":
    main()
