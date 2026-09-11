"""Run the E2 causal candidate on a fixed BTC/ETH/SOL holdout panel."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from liquidity_hunter.core.domain import Candle
from research.eq_levels_e2 import restart_equivalent, run_candidate, validate_candidate

ROOT = Path(__file__).resolve().parents[1]
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
TIMEFRAMES = ("15m", "1h", "4h")


def run_panel(fixtures_root: Path = ROOT / "frontend/research/fixtures") -> dict:
    rows = []
    for symbol in SYMBOLS:
        for timeframe in TIMEFRAMES:
            path = fixtures_root / f"{symbol}_{timeframe}.json"
            source = json.loads(path.read_text())
            candles = [Candle.model_validate(row) for row in source["candles"][:-1]]
            run = run_candidate(candles)
            checks = validate_candidate(run)
            split = len(candles) // 2
            assert restart_equivalent(candles, split)
            rows.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "candles": len(candles),
                    "published_versions": checks["published_versions"],
                    "stable_strength_versions": checks["stable_strength_versions"],
                    "restart_split": split,
                    "restart_equal": True,
                }
            )
    return {"schema": 1, "symbols": SYMBOLS, "timeframes": TIMEFRAMES, "charts": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=ROOT / "research/eq_levels_e2_baseline.json")
    args = parser.parse_args()
    result = run_panel()
    args.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
