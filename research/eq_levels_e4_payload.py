"""E4.2 payload compatibility check, research-only."""

from __future__ import annotations

import json
from pathlib import Path

from liquidity_hunter.core.domain import Candle
from research.eq_levels_e1 import Replay
from research.eq_levels_e1_2 import validate_zone_payload, zone_payload


def check(path: Path) -> dict:
    source = json.loads(path.read_text())
    run = Replay()
    for row in source["candles"][:-1]:
        run.feed(Candle.model_validate(row))
    payload = zone_payload(run)
    validate_zone_payload(payload)
    return {"symbol": source["symbol"], "timeframe": source["timeframe"], "zones": len(payload)}
