"""D4: should EQ and swing be reduced to one dominance scale at all?

Research only; production and the frontend are untouched. Nothing here invents
a strength, optimizes a weight, or tries another ATR curve — D1-D3 closed those
questions. This round compares two minimalist architectures that the earlier
findings justify, against the production ranking.

Pre-registration (D4.1/D4.2), fixed before any outcome of this round was read:

- `LEGACY` — the production composite, unchanged.
- `SINGLE` — one winner, chosen by distance under `LINEAR_5_ATR` (the D2
  survivor). Strength never enters the score; it breaks **exact** ties only.
- `DUAL` — no arbitration at all: the best EQ and the best swing, each chosen
  inside its own family by the same rule.

The deterministic order for every non-legacy pick (D4.2):

1. higher `LINEAR_5_ATR` distance score;
2. smaller distance in ATR (this is what decides inside the saturated zone,
   where the curve has flattened every candidate to zero);
3. on an exact distance tie, higher strength **within the same family**;
4. a stable fallback on (formation time, band), so the choice never depends on
   detector emission order.

So strength can never beat a real difference in distance — which is precisely
the D1/D2 pathology — and the window-dependent swing normalization can only
reach the outcome through step 3.
"""

from __future__ import annotations

import hashlib
import json
import statistics as st
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from liquidity_hunter.core.domain import Candle, LiquiditySide
from liquidity_hunter.liquidity.detectors import SwingHighDetector, SwingLowDetector
from liquidity_hunter.liquidity.detectors._common import price_range
from liquidity_hunter.liquidity.mitigation import mark_swept_zones
from liquidity_hunter.scoring.engine import LiquidityScoringEngine
from research.dominant_liquidity_distance_audit import CURVES, reaction
from research.dominant_liquidity_strength_audit import (
    HORIZONS,
    MIN_FUTURE,
    REACTION_BARS,
    START,
    STEP,
    TFS,
    atr14,
    contacted,
    family,
    first_contact,
    midpoint,
    pct,
    rate,
)
from research.eq_levels_e1 import FACTORIES

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "research/.replay_cache/dominant_liquidity_d4_baseline.json"

CURVE = "LINEAR_5_ATR"
#: Shorter loaded window used by the window-dependence probe (D4.15). The
#: production dashboard loads a bounded number of candles, so "how much history
#: is on screen" is a real knob, and swing strength divides by its price range.
ALT_WINDOW = 600


#: Width-neutral "the level gave way" threshold, in ATR past the band midpoint.
#: D1 showed that `through` measured at the band edge cannot compare families —
#: an EQ pool is 0.3-0.5 ATR wide and a swing is a point — so the cross-family
#: reading uses the midpoint plus this fixed clearance for both.
THROUGH_CLEARANCE_ATR = 0.25


def midpoint_reaction(zone, future, contact, atr):
    """`reaction`, but with both families judged from their band midpoint."""
    if contact is None or atr <= 0 or len(future) < contact + REACTION_BARS:
        return None
    window = future[contact : contact + REACTION_BARS]
    level = (zone.price_high + zone.price_low) / 2
    clearance = THROUGH_CLEARANCE_ATR * atr
    if zone.side == LiquiditySide.BUY_SIDE:
        return {
            "closed_through": any(c.close > level + clearance for c in window),
            "rejection_atr": max(0.0, (level - min(c.low for c in window)) / atr),
            "through_atr": max(0.0, (max(c.high for c in window) - level) / atr),
        }
    return {
        "closed_through": any(c.close < level - clearance for c in window),
        "rejection_atr": max(0.0, (max(c.high for c in window) - level) / atr),
        "through_atr": max(0.0, (level - min(c.low for c in window)) / atr),
    }


def distance_score(candidate: dict) -> float:
    return CURVES[CURVE](candidate["d_atr"])


def order_key(candidate: dict, strength_field: str = "strength") -> tuple:
    """The pre-registered D4.2 ordering, as a sort key (higher is better)."""
    return (
        distance_score(candidate),
        -candidate["d_atr"],
        candidate[strength_field],
        candidate["fallback"],
    )


def best(candidates: list[dict], strength_field: str = "strength") -> dict | None:
    if not candidates:
        return None
    return max(candidates, key=lambda c: order_key(c, strength_field))


def legacy_score(candidate: dict, strength_field: str = "strength") -> float:
    """The production composite, recomputable so the window probe can move it."""
    return min(
        100.0,
        max(
            0.0,
            candidate["legacy_distance_score"] * 0.4
            + candidate[strength_field] * 100.0 * 0.4
            + candidate["timeframe_score"] * 0.2,
        ),
    )


def legacy_winner(candidates: list[dict], strength_field: str = "strength") -> dict:
    """Production: stable argmax of the composite, ties keep emission order."""
    keys = [legacy_score(c, strength_field) for c in candidates]
    top = max(keys)
    return candidates[next(i for i, k in enumerate(keys) if abs(k - top) < 1e-10)]


def picks(candidates: list[dict], strength_field: str = "strength") -> dict:
    """Every architecture's answer for one observation."""
    eq = [c for c in candidates if c["family"] == "EQ"]
    swing = [c for c in candidates if c["family"] == "SW"]
    above = [c for c in candidates if c["above"]]
    below = [c for c in candidates if not c["above"]]
    return {
        "legacy": legacy_winner(candidates, strength_field),
        "single": best(candidates, strength_field),
        "best_eq": best(eq, strength_field),
        "best_swing": best(swing, strength_field),
        "best_above": best(above, strength_field),
        "best_below": best(below, strength_field),
    }


def observe(zones, price, atr, future, span, alt_span):
    ranked = LiquidityScoringEngine().score([z for z in zones if not z.is_mitigated], price)
    if not ranked:
        return None
    candidates = []
    for scored in ranked:
        z = scored.zone
        delta = abs(midpoint(z) - price)
        contact = first_contact(z, future)
        fam = family(z)
        # D4.15: the same swing, read under a shorter loaded window. Only the
        # normalization moves (prominence is a property of the pivot), so the
        # rescale is exact except where the original value was already clamped.
        alt = z.strength
        if fam == "SW" and alt_span and alt_span > 0:
            alt = min(1.0, z.strength * span / alt_span)
        candidates.append(
            {
                "family": fam,
                "type": z.zone_type.value,
                "above": midpoint(z) > price,
                "d_atr": delta / atr if atr else None,
                "width_atr": (z.price_high - z.price_low) / atr if atr else None,
                "strength": z.strength,
                "strength_alt": alt,
                "legacy_score": scored.score,
                "legacy_distance_score": scored.distance_score,
                "timeframe_score": scored.timeframe_score,
                "fallback": (z.formed_at.isoformat(), z.price_low, z.price_high),
                "level": f"{z.zone_type.value}@{z.price_low:.10g}-{z.price_high:.10g}",
                "contact": contact,
                "future_bars": len(future),
                "reaction": reaction(z, future, contact, atr),
                "midpoint_reaction": midpoint_reaction(z, future, contact, atr),
            }
        )
    return candidates


def audit(path: Path, expected: dict) -> dict:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected["sha256"]:
        raise ValueError(f"fixture changed: {path.name}")
    data = json.loads(raw)
    bars = [Candle.model_validate(c) for c in data["candles"][:-1]]
    rows = []
    for end in range(START, len(bars) - MIN_FUTURE + 1, STEP):
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
        price = prefix[-1].close
        atr = atr14(prefix)
        span = price_range(prefix)
        alt_span = price_range(prefix[-ALT_WINDOW:]) if len(prefix) > ALT_WINDOW else None
        rows.append(
            {
                "end": end,
                "block": (end - 1) * 4 // len(bars),
                "observed_at": prefix[-1].timestamp.isoformat(),
                "price": price,
                "atr": atr,
                "has_alt_window": alt_span is not None,
                "candidates": observe(
                    zones, price, atr, bars[end : end + max(HORIZONS)], span, alt_span
                )
                or [],
            }
        )
    return {
        "symbol": data["symbol"],
        "timeframe": data["timeframe"],
        "sha256": expected["sha256"],
        "rows": rows,
    }


def _job(spec):
    return audit(ROOT / "frontend/research/fixtures" / spec["file"], spec)


def main() -> None:
    manifest_path = ROOT / "research/eq_levels_baseline.json"
    manifest = json.loads(manifest_path.read_text())
    charts = []
    with ProcessPoolExecutor() as pool:
        for i, chart in enumerate(pool.map(_job, manifest["charts"]), 1):
            charts.append(chart)
            print(f"{i}/{len(manifest['charts'])} {chart['symbol']}", flush=True)
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(
        json.dumps(
            {
                "schema": 1,
                "curve": CURVE,
                "alt_window": ALT_WINDOW,
                "horizons": list(HORIZONS),
                "reaction_bars": REACTION_BARS,
                "charts": charts,
                "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            },
            allow_nan=False,
        )
    )
    print(f"wrote {BASELINE} ({BASELINE.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()


# --------------------------------------------------------------------------
# Stage 2: analysis. Reads the baseline; no fixture is touched again.
# --------------------------------------------------------------------------

CASE_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def load():
    data = json.loads(BASELINE.read_text())
    observations = []
    for chart in data["charts"]:
        for row in chart["rows"]:
            if row["candidates"]:
                observations.append(
                    {**row, "symbol": chart["symbol"], "tf": chart["timeframe"]}
                )
    for obs in observations:
        obs["picks"] = picks(obs["candidates"])
    return data, observations


def median(values):
    kept = [v for v in values if v is not None]
    return st.median(kept) if kept else float("nan")


def quantile(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else float("nan")


def distance_table(label, winners):
    d = [w["d_atr"] for w in winners]
    return (
        f"{label:<22} n={len(d):5d} p50={quantile(d, 0.5):6.2f} p75={quantile(d, 0.75):6.2f} "
        f"p90={quantile(d, 0.90):6.2f} "
        f">1={sum(v > 1 for v in d) / len(d) * 100:5.1f}% "
        f">2={sum(v > 2 for v in d) / len(d) * 100:5.1f}% "
        f">3={sum(v > 3 for v in d) / len(d) * 100:5.1f}% "
        f">5={sum(v > 5 for v in d) / len(d) * 100:5.1f}%"
    )


def reaction_table(label, winners, field="reaction"):
    rx = [w[field] for w in winners if w[field]]
    if len(rx) < 30:
        return f"{label:<22} n={len(rx):5d} (too few to read)"
    ratios = [
        r["rejection_atr"] / r["through_atr"] for r in rx if r["through_atr"] > 1e-9
    ]
    return (
        f"{label:<22} n={len(rx):5d} "
        f"through={sum(r['closed_through'] for r in rx) / len(rx) * 100:5.1f}% "
        f"MFE={median([r['rejection_atr'] for r in rx]):5.3f} "
        f"MAE={median([r['through_atr'] for r in rx]):5.3f} "
        f"MFE/MAE={median(ratios):5.3f}"
    )


def tie_break_rivals(obs: dict) -> int:
    """How many candidates reach step 3 of the D4.2 order (exact distance tie)."""
    winner = best(obs["candidates"])
    return sum(
        abs(distance_score(c) - distance_score(winner)) < 1e-12
        and abs(c["d_atr"] - winner["d_atr"]) < 1e-12
        for c in obs["candidates"]
    )


def report() -> None:  # noqa: C901 - a report is a sequence of tables
    data, observations = load()
    out = print
    out(f"# observations={len(observations)} curve={data['curve']} alt_window={data['alt_window']}")

    out("\n## D4.4/D4.11 SINGLE vs LEGACY")
    for tf in (*TFS, None):
        sel = [o for o in observations if tf is None or o["tf"] == tf]
        for arm in ("legacy", "single"):
            winners = [o["picks"][arm] for o in sel]
            eq = sum(w["family"] == "EQ" for w in winners) / len(winners) * 100
            out(distance_table(f"{arm} {tf or 'all'}", winners) + f" EQ={eq:5.1f}%")
    changed = [o for o in observations if o["picks"]["single"] is not o["picks"]["legacy"]]
    eq_to_sw = sum(
        o["picks"]["legacy"]["family"] == "EQ" and o["picks"]["single"]["family"] == "SW"
        for o in changed
    )
    sw_to_eq = sum(
        o["picks"]["legacy"]["family"] == "SW" and o["picks"]["single"]["family"] == "EQ"
        for o in changed
    )
    side_flip = sum(o["picks"]["legacy"]["above"] != o["picks"]["single"]["above"] for o in changed)
    out(
        f"changed={len(changed) / len(observations) * 100:5.2f}% EQ->SW={eq_to_sw} "
        f"SW->EQ={sw_to_eq} side flip={side_flip} "
        f"median d_atr {median([o['picks']['legacy']['d_atr'] for o in changed]):.2f}"
        f"->{median([o['picks']['single']['d_atr'] for o in changed]):.2f}"
    )
    ties_used = sum(tie_break_rivals(o) > 1 for o in observations)
    out(f"observations where the strength tie-break was actually consulted: {ties_used}")

    out("\n## D4.4 pathology: a <=1 ATR candidate existed and the winner is >3 ATR")
    eligible = [o for o in observations if any(c["d_atr"] <= 1 for c in o["candidates"])]
    for arm in ("legacy", "single", "best_eq", "best_swing"):
        chosen = [o["picks"][arm] for o in eligible if o["picks"][arm]]
        far = [w for w in chosen if w["d_atr"] > 3]
        share = len(far) / len(eligible) * 100
        out(f"{arm:<12} far={len(far):4d} ({share:5.2f}% of {len(eligible)})")

    out("\n## D4.5/D4.13 DUAL availability and visual density")
    both = [o for o in observations if o["picks"]["best_eq"] and o["picks"]["best_swing"]]
    only_eq = [o for o in observations if o["picks"]["best_eq"] and not o["picks"]["best_swing"]]
    only_sw = [o for o in observations if o["picks"]["best_swing"] and not o["picks"]["best_eq"]]
    out(
        f"both={len(both)} ({len(both) / len(observations) * 100:5.2f}%) "
        f"only EQ={len(only_eq)} only swing={len(only_sw)} neither=0"
    )
    out(
        f"DUAL would draw 2 levels in {len(both) / len(observations) * 100:.2f}% of observations, "
        f"1 in {(len(only_eq) + len(only_sw)) / len(observations) * 100:.2f}%"
    )
    for tf in TFS:
        sel = [o for o in observations if o["tf"] == tf]
        pair = [o for o in sel if o["picks"]["best_eq"] and o["picks"]["best_swing"]]
        out(f"  {tf:>3} both={len(pair) / len(sel) * 100:5.2f}%")

    out("\n## D4.6 when both exist: same side or opposite sides")
    same = [o for o in both if o["picks"]["best_eq"]["above"] == o["picks"]["best_swing"]["above"]]
    opposite = [o for o in both if o not in same]
    out(
        f"same side={len(same)} ({len(same) / len(both) * 100:5.2f}%) "
        f"opposite={len(opposite)} ({len(opposite) / len(both) * 100:5.2f}%)"
    )
    same_gap = median(
        [abs(o["picks"]["best_eq"]["d_atr"] - o["picks"]["best_swing"]["d_atr"]) for o in same]
    )
    nearer_eq = sum(
        o["picks"]["best_eq"]["d_atr"] < o["picks"]["best_swing"]["d_atr"] for o in same
    )
    out(
        f"  on the same side the EQ is the nearer one in {nearer_eq / len(same) * 100:5.2f}% "
        f"| median gap={same_gap:.2f} ATR"
    )
    opp_eq = median([o["picks"]["best_eq"]["d_atr"] for o in opposite])
    opp_sw = median([o["picks"]["best_swing"]["d_atr"] for o in opposite])
    out(
        f"  on opposite sides the two are {opp_eq:.2f} ATR (EQ) "
        f"and {opp_sw:.2f} ATR (swing) away"
    )

    out("\n## D4.7 diagnostic: the best level above and below price, family-free")
    for tf in TFS:
        sel = [o for o in observations if o["tf"] == tf]
        above = [o["picks"]["best_above"] for o in sel if o["picks"]["best_above"]]
        below = [o["picks"]["best_below"] for o in sel if o["picks"]["best_below"]]
        def summary(side, sel=sel):
            eq = sum(w["family"] == "EQ" for w in side) / len(side) * 100
            return (
                f"available={len(side) / len(sel) * 100:5.1f}% "
                f"median={median([w['d_atr'] for w in side]):.2f} ATR (EQ {eq:4.1f}%)"
            )

        out(f"{tf:>3} above {summary(above)} | below {summary(below)}")

    out("\n## D4.8 reachability (geometry favours the nearer arm by construction)")
    for arm in ("legacy", "single", "best_eq", "best_swing"):
        winners = [o["picks"][arm] for o in observations if o["picks"][arm]]
        out(
            f"{arm:<12} n={len(winners):5d} "
            + " ".join(f"h{h}={pct(rate([contacted(w, h) for w in winners])[0])}" for h in HORIZONS)
        )

    out("\n## D4.9 reaction after contact: do the families play different roles?")
    for field in ("reaction", "midpoint_reaction"):
        out(f"-- {'band edge' if field == 'reaction' else 'midpoint (width-neutral)'}")
        for arm in ("best_eq", "best_swing"):
            winners = [o["picks"][arm] for o in observations if o["picks"][arm]]
            out(reaction_table(arm, winners, field))
    out("-- matched on distance bucket (same observation, both families present)")
    for lo, hi in ((0, 1), (1, 2), (2, 3), (3, 99)):
        pairs = [
            o
            for o in both
            if lo <= o["picks"]["best_eq"]["d_atr"] < hi
            and lo <= o["picks"]["best_swing"]["d_atr"] < hi
        ]
        if len(pairs) < 30:
            continue
        out(f"  {lo}-{hi} ATR (n={len(pairs)})")
        for field in ("reaction", "midpoint_reaction"):
            tag = "band edge" if field == "reaction" else "midpoint (width-neutral)"
            out(f"    -- {tag}")
            out("    " + reaction_table("best_eq", [o["picks"]["best_eq"] for o in pairs], field))
            out(
                "    "
                + reaction_table("best_swing", [o["picks"]["best_swing"] for o in pairs], field)
            )
        eq_contact = rate([contacted(o["picks"]["best_eq"], 20) for o in pairs])
        sw_contact = rate([contacted(o["picks"]["best_swing"], 20) for o in pairs])
        out(
            f"    contact@20 EQ={pct(eq_contact[0])} swing={pct(sw_contact[0])}"
        )

    out("-- does the family difference replicate? through rate, <=2 ATR both sides, per block")
    near = [
        o
        for o in both
        if o["picks"]["best_eq"]["d_atr"] <= 2
        and o["picks"]["best_swing"]["d_atr"] <= 2
        and o["picks"]["best_eq"]["midpoint_reaction"]
        and o["picks"]["best_swing"]["midpoint_reaction"]
    ]
    for block in range(4):
        sel = [o for o in near if o["block"] == block]
        if len(sel) < 20:
            out(f"  b{block} n={len(sel)} (too few to read)")
            continue
        eq = [o["picks"]["best_eq"]["midpoint_reaction"]["closed_through"] for o in sel]
        sw = [o["picks"]["best_swing"]["midpoint_reaction"]["closed_through"] for o in sel]
        out(
            f"  b{block} n={len(sel):4d} EQ through={sum(eq) / len(eq) * 100:5.1f}% "
            f"swing through={sum(sw) / len(sw) * 100:5.1f}% "
            f"gap={(sum(eq) / len(eq) - sum(sw) / len(sw)) * 100:+5.1f}pp"
        )

    out("\n## D4.10/D4.12 what a single winner hides")
    for arm_family, label in (("SW", "SINGLE picked a swing"), ("EQ", "SINGLE picked an EQ")):
        sel = [o for o in both if o["picks"]["single"]["family"] == arm_family]
        other = "best_eq" if arm_family == "SW" else "best_swing"
        gaps = [o["picks"][other]["d_atr"] - o["picks"]["single"]["d_atr"] for o in sel]
        out(
            f"{label:<24} n={len(sel):5d} the other family's best was "
            f"{median([o['picks'][other]['d_atr'] for o in sel]):.2f} ATR away "
            f"(median gap {median(gaps):+.2f} ATR)"
        )
    for limit in (1, 2, 3):
        rival = [
            o
            for o in both
            if o["picks"]["best_eq"]["d_atr"] <= limit
            and o["picks"]["best_swing"]["d_atr"] <= limit
        ]
        out(
            f"both families have a candidate within {limit} ATR: {len(rival)} "
            f"({len(rival) / len(observations) * 100:5.2f}% of observations)"
        )
    matches = {"best_eq": 0, "best_swing": 0, "neither": 0}
    for obs in observations:
        winner = obs["picks"]["legacy"]
        if winner is obs["picks"]["best_eq"]:
            matches["best_eq"] += 1
        elif winner is obs["picks"]["best_swing"]:
            matches["best_swing"] += 1
        else:
            matches["neither"] += 1
    out(
        "the LEGACY winner is "
        + " ".join(f"{k}={v} ({v / len(observations) * 100:.2f}%)" for k, v in matches.items())
    )

    out("\n## D4.15 window dependence: the same observation under a shorter loaded window")
    alt = [o for o in observations if o["has_alt_window"]]
    for arm in ("legacy", "single", "best_eq", "best_swing"):
        moved = 0
        for obs in alt:
            shifted = picks(obs["candidates"], "strength_alt")[arm]
            current = obs["picks"][arm]
            if (current is None) != (shifted is None):
                moved += 1
            elif current is not None and current["level"] != shifted["level"]:
                moved += 1
        share = moved / len(alt) * 100
        out(f"{arm:<12} choice changed in {moved:5d} of {len(alt)} ({share:5.2f}%)")

    out("\n## D4.16 temporal blocks")
    for block in range(4):
        sel = [o for o in observations if o["block"] == block]
        pair = [o for o in sel if o["picks"]["best_eq"] and o["picks"]["best_swing"]]
        eq_share = sum(o["picks"]["single"]["family"] == "EQ" for o in sel) / len(sel) * 100
        legacy_far = [o for o in sel if o["picks"]["legacy"]["d_atr"] > 3]
        single_far = [o for o in sel if o["picks"]["single"]["d_atr"] > 3]
        out(
            f"b{block} n={len(sel):5d} dual-both={len(pair) / len(sel) * 100:5.2f}% "
            f"| >3 ATR legacy={len(legacy_far) / len(sel) * 100:5.2f}% "
            f"single={len(single_far) / len(sel) * 100:5.2f}% "
            f"| single EQ share={eq_share:5.1f}%"
        )

    out("\n## D4.17 per symbol")
    symbols = sorted({o["symbol"] for o in observations})
    shares, duals = [], []
    for symbol in symbols:
        sel = [o for o in observations if o["symbol"] == symbol]
        shares.append(sum(o["picks"]["single"]["family"] == "EQ" for o in sel) / len(sel))
        duals.append(
            sum(bool(o["picks"]["best_eq"] and o["picks"]["best_swing"]) for o in sel) / len(sel)
        )
    out(
        f"SINGLE EQ share per symbol: median={median(shares) * 100:5.2f}% "
        f"p10={quantile(shares, 0.1) * 100:5.2f}% p90={quantile(shares, 0.9) * 100:5.2f}% "
        f"symbols with zero EQ winners={sum(s == 0 for s in shares)}/{len(symbols)}"
    )
    out(
        f"DUAL both-available per symbol: median={median(duals) * 100:5.2f}% "
        f"p10={quantile(duals, 0.1) * 100:5.2f}% p90={quantile(duals, 0.9) * 100:5.2f}%"
    )

    out("\n## D4.18 case F: an exact distance tie, where strength actually decides")
    for obs in observations:
        if tie_break_rivals(obs) > 1:
            winner = obs["picks"]["single"]
            rivals = [
                c
                for c in obs["candidates"]
                if abs(c["d_atr"] - winner["d_atr"]) < 1e-12 and c is not winner
            ]
            out(
                f"[F] {obs['symbol']} {obs['tf']} {obs['observed_at']} "
                f"{winner['type']} strength={winner['strength']:.4f} beat "
                f"{rivals[0]['type']} strength={rivals[0]['strength']:.4f} "
                f"at the same {winner['d_atr']:.4f} ATR"
            )
            break

    out("\n## D4.18 concrete cases (BTC / ETH / SOL)")
    def describe(level):
        if not level:
            return "none"
        side = "above" if level["above"] else "below"
        return f"{level['type']} @{level['d_atr']:.2f} {side}"

    shown = dict.fromkeys("ABCDEF", 0)
    for obs in observations:
        if obs["symbol"] not in CASE_SYMBOLS:
            continue
        p = obs["picks"]
        tag = None
        if p["legacy"]["d_atr"] > 3 and p["single"]["d_atr"] <= 1 and p["single"]["family"] == "SW":
            tag = "A"
        elif (
            p["legacy"] is p["single"]
            and p["legacy"]["family"] == "EQ"
            and p["legacy"]["d_atr"] <= 1
        ):
            tag = "B"
        elif all(c["legacy_distance_score"] == 0 for c in obs["candidates"]) and obs["tf"] == "4h":
            tag = "E"
        elif p["best_eq"] and p["best_swing"] and p["best_eq"]["above"] != p["best_swing"]["above"]:
            tag = "D"
        elif p["best_eq"] and p["best_swing"] and p["best_eq"]["above"] == p["best_swing"]["above"]:
            tag = "C"
        if tag and shown[tag] < 1:
            shown[tag] += 1
            eq = p["best_eq"]
            sw = p["best_swing"]
            out(
                f"[{tag}] {obs['symbol']} {obs['tf']} {obs['observed_at']} "
                f"price={obs['price']:.4f} "
                f"| LEGACY {p['legacy']['type']} @{p['legacy']['d_atr']:.2f} ATR "
                f"(strength {p['legacy']['strength']:.3f}) | SINGLE {p['single']['type']} "
                f"@{p['single']['d_atr']:.2f} | DUAL EQ "
                + describe(eq)
                + " + SW "
                + describe(sw)
            )
