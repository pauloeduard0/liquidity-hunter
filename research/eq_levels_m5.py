"""Prepare optional BTC/ETH/SOL M5 diagnostics from existing raw kline caches."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from research.eq_levels_audit import ROOT


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=Path("/tmp/eq_m5_fixtures"))
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        source = ROOT / "research/.klines_cache" / f"{symbol}_5m.json"
        raw = source.read_bytes()
        bars = json.loads(raw)[-1200:]
        candles = [
            dict(
                symbol=symbol,
                timeframe="5m",
                timestamp=datetime.fromtimestamp(int(r[0]) / 1000, UTC).isoformat(),
                open=float(r[1]),
                high=float(r[2]),
                low=float(r[3]),
                close=float(r[4]),
                volume=float(r[5]),
                taker_buy_volume=float(r[9]),
            )
            for r in bars
        ]
        out = dict(
            symbol=symbol,
            timeframe="5m",
            candles=candles,
            source=str(source.relative_to(ROOT)),
            source_sha256=hashlib.sha256(raw).hexdigest(),
        )
        (args.output / f"{symbol}_5m.json").write_text(json.dumps(out))
    print(args.output)


if __name__ == "__main__":
    main()
