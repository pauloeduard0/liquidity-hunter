"""D0: causal measurement of the existing dominant-liquidity ranking."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from liquidity_hunter.core.domain import Candle, LiquidityZone
from liquidity_hunter.liquidity.detectors import SwingHighDetector, SwingLowDetector
from liquidity_hunter.liquidity.mitigation import mark_swept_zones
from liquidity_hunter.scoring.engine import LiquidityScoringEngine
from research.eq_levels_e1 import FACTORIES

ROOT = Path(__file__).resolve().parents[1]


def midpoint(zone):
    return (zone.price_low + zone.price_high) / 2


def distance(zone, price):
    return abs(midpoint(zone) - price) / price


def atr14(candles):
    values = [
        max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
        for p, c in zip(candles[:-1], candles[1:], strict=False)
    ]
    return sum(values[-14:]) / len(values[-14:])


def first_contact(zone, future):
    # Frozen original band: contact, not sweep, profitability or future rank.
    return next(
        (
            i
            for i, c in enumerate(future, 1)
            if c.high >= zone.price_low and c.low <= zone.price_high
        ),
        None,
    )


def measure(zones, price, atr, future):
    ranked = LiquidityScoringEngine().score([z for z in zones if not z.is_mitigated], price)
    if not ranked:
        return {"active": 0}
    winner = ranked[0]
    nearest = min(ranked, key=lambda r: distance(r.zone, price))
    same_side = min(
        (r for r in ranked if r.zone.side == winner.zone.side),
        key=lambda r: distance(r.zone, price),
    )

    def entry(scored):
        z = scored.zone
        contact = first_contact(z, future)
        return {
            "type": z.zone_type.value,
            "side": z.side.value,
            "midpoint": midpoint(z),
            "formed_at": z.formed_at.isoformat(),
            "distance_pct": distance(z, price) * 100,
            "distance_atr": abs(midpoint(z) - price) / atr if atr else None,
            "strength": z.strength,
            "score": scored.score,
            "distance_score": scored.distance_score,
            "first_contact": contact,
            "contact20": contact is not None and contact <= 20 if len(future) >= 20 else None,
            "contact40": contact is not None and contact <= 40 if len(future) >= 40 else None,
        }

    return {
        "active": len(ranked),
        "winner": entry(winner),
        "nearest": entry(nearest),
        "nearest_same_side": entry(same_side),
        "winner_is_nearest": winner.zone == nearest.zone,
        "top_score_ties": sum(abs(r.score - winner.score) < 1e-10 for r in ranked),
        "all_distance_scores_zero": all(r.distance_score == 0 for r in ranked),
    }


def audit(path, expected):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected["sha256"]:
        raise ValueError(f"fixture changed: {path.name}")
    data = json.loads(raw)
    bars = [Candle.model_validate(c) for c in data["candles"][:-1]]
    rows = []
    for end in range(200, len(bars) - 40 + 1, 100):
        prefix = bars[:end]
        zones = mark_swept_zones(
            [
                *SwingHighDetector().detect(prefix),
                *SwingLowDetector().detect(prefix),
                *FACTORIES["EQH"]().detect(prefix),
                *FACTORIES["EQL"]().detect(prefix),
            ],
            prefix,
        )
        rows.append(
            {
                "end": end,
                "block": (end - 1) * 4 // len(bars),
                "observed_at": prefix[-1].timestamp.isoformat(),
                **measure(zones, prefix[-1].close, atr14(prefix), bars[end : end + 40]),
            }
        )
    saved = [LiquidityZone.model_validate(z) for z in data["liquidity_zones"]]
    snapshot = measure(saved, data["current_price"], atr14(bars), [])
    recalculated = LiquidityScoringEngine().score(
        [z for z in saved if not z.is_mitigated], data["current_price"]
    )
    stored = data["ranked_zones"]
    match = len(stored) == len(recalculated) and all(
        LiquidityZone.model_validate(old["zone"]) == new.zone
        and abs(old["score"] - new.score) < 1e-8
        for old, new in zip(stored, recalculated, strict=False)
    )
    return {
        "symbol": data["symbol"],
        "timeframe": data["timeframe"],
        "sha256": expected["sha256"],
        "stored_ranking_match": match,
        "snapshot": snapshot,
        "rows": rows,
    }


def main():
    manifest_path = ROOT / "research/eq_levels_baseline.json"
    manifest = json.loads(manifest_path.read_text())
    rows = []
    for spec in manifest["charts"]:
        rows.append(audit(ROOT / "frontend/research/fixtures" / spec["file"], spec))
        print(f"{len(rows)}/{len(manifest['charts'])} {spec['file']}", flush=True)
    output = ROOT / "research/.replay_cache/dominant_liquidity_d0.json"
    output.parent.mkdir(exist_ok=True, parents=True)
    output.write_text(
        json.dumps(
            {
                "schema": 1,
                "step": 100,
                "start": 200,
                "horizons": [20, 40],
                "charts": rows,
                "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            },
            indent=2,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
