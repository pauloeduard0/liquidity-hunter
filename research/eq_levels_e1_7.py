"""E1.7 holdout analysis for the already-frozen E1 baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

HORIZON = "10"
ARMS = ("R", "N", "L")


def holdout_report(baseline: dict, *, holdout_block: str = "3") -> dict:
    """Extract a pre-registered final-quarter holdout without retuning data."""
    output = {
        "schema": 1,
        "holdout_block": holdout_block,
        "charts": len(baseline["charts"]),
        "groups": {},
    }
    for name, summary in baseline["summary"].items():
        quality = summary["quality"]
        rows = {}
        for arm in ARMS:
            metric = quality[arm][HORIZON]
            block = metric["blocks"][holdout_block]
            rows[arm] = {
                "n": block["n"],
                "delta_mean": block["mean"],
                "delta_p50": block["p50"],
                "symbol_median_delta": metric["median_symbol_delta"],
                "positive_symbol_fraction": metric["positive_symbols"],
            }
        density = summary["density"]
        rows["density"] = {arm: density[arm] for arm in ("R", "N", "L")}
        rows["restart"] = {
            "prefix_checks": sum(
                len(c["prefix_checks"])
                for c in baseline["charts"]
                if f"{c['tf']}/{name.split('/')[1]}" == name
            ),
            "cold_start_differences": sum(
                c["cold_left_difference"][name.split("/")[1]]
                for c in baseline["charts"]
                if c["tf"] == name.split("/")[0]
            ),
        }
        output["groups"][name] = rows
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline", type=Path, default=Path("research/eq_levels_e1_baseline.json")
    )
    parser.add_argument("--json", type=Path, default=Path("research/eq_levels_e1_7_baseline.json"))
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text())
    report = holdout_report(baseline)
    args.json.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print(json.dumps(report, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
