"""D2: is a distance scale in ATR comparable across timeframes?

Research only. Production is untouched: the curves below exist in this module
and nowhere else, and every detector, tolerance and strength is a production
call replayed on causal prefixes.

Pre-registration (D2.0), fixed before any outcome of this round was read:

- The D1 report pre-registered the *family* of the hypothesis (`distance_score`
  should depend on distance in ATR, not on a fixed percentage) and deliberately
  left curve and threshold unchosen. Three curves are chosen here, by argument,
  and none is tuned afterwards:
  - `LINEAR_5_ATR` — 100 at 0 ATR, linear to 0 at 5 ATR. 5 ATR is already the
    D0/D1 reporting landmark (the `>5 ATR` bucket), not a fitted number.
  - `LINEAR_3_ATR` — same shape, zero at 3 ATR. 3 ATR is the other D0 landmark,
    the one the far-winner pathology is defined on.
  - `SOFT_DECAY` — `100 / (1 + d_atr)`. Parameter-free and monotone; it never
    reaches zero, so saturation is structurally impossible rather than tuned
    away.
- The success criteria (D2.22) were written down before the arms were run.
- If none of the three passes, the round stops. No 2.5 / 3.5 / 4 / 6 ATR sweep
  on this sample.

Card intent (D2.4) is taken from the documentation, not invented here:
`docs/scoring.md` says the engine ranks zones "by how relevant they are as
**liquidity targets** relative to the current price", a descriptive research
metric. That is intent (A) — the level most likely to be *visited*. Which is
exactly why reachability cannot be the score to maximize: the nearest level
wins it by construction (D0 already warned against that), so the arms are
judged on the D2.22 criteria and reachability is reported beside reaction,
never blended into one number.
"""

from __future__ import annotations

import hashlib
import json
import statistics as st
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from liquidity_hunter.core.domain import Candle, LiquiditySide
from liquidity_hunter.liquidity.detectors import SwingHighDetector, SwingLowDetector
from liquidity_hunter.liquidity.mitigation import mark_swept_zones
from liquidity_hunter.scoring.engine import LiquidityScoringEngine
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
BASELINE = ROOT / "research/.replay_cache/dominant_liquidity_d2_baseline.json"

#: Pre-registered distance curves. d_atr -> [0, 100]. Never edited after a run.
CURVES = {
    "LEGACY_PCT": None,  # the production curve, driven by d_pct, not d_atr
    "LINEAR_5_ATR": lambda d: max(0.0, min(100.0, (1.0 - d / 5.0) * 100.0)),
    "LINEAR_3_ATR": lambda d: max(0.0, min(100.0, (1.0 - d / 3.0) * 100.0)),
    "SOFT_DECAY": lambda d: 100.0 / (1.0 + d),
}

#: (arm name, curve, strength mode). "all" = production strength, "eq" = only
#: the EQ family keeps it, "none" = the channel is switched off entirely.
ARMS = [
    ("LEGACY", "LEGACY_PCT", "all"),
    *[
        (f"{prefix}__{curve}", curve, mode)
        for curve in ("LINEAR_5_ATR", "LINEAR_3_ATR", "SOFT_DECAY")
        for prefix, mode in (
            ("ATR_CURRENT_STRENGTH", "all"),
            ("ATR_NO_STRENGTH", "none"),
            ("ATR_EQ_STRENGTH_ONLY", "eq"),
        )
    ],
    ("DISTANCE_ONLY", None, "none"),
]

DISCOVERY_BLOCKS = (0, 1)
HOLDOUT_BLOCKS = (2, 3)


def distance_score(candidate: dict, curve: str | None) -> float:
    """The arm's distance channel, in the same 0-100 units production uses."""
    if curve is None:
        return 0.0
    if curve == "LEGACY_PCT":
        return candidate["distance_score"]
    return CURVES[curve](candidate["d_atr"])


def arm_key(candidate: dict, curve: str | None, mode: str) -> float:
    """Ranking key for one arm. Same weights as production; only the distance
    curve and which families keep `touch_score` change."""
    if curve is None:  # DISTANCE_ONLY: pure geometry, in ATR
        return -candidate["d_atr"]
    strength = candidate["touch_score"] if mode == "all" else 0.0
    if mode == "eq" and candidate["family"] == "EQ":
        strength = candidate["touch_score"]
    return (
        distance_score(candidate, curve) * 0.4
        + strength * 0.4
        + candidate["timeframe_score"] * 0.2
    )


def pick(candidates: list[dict], curve: str | None, mode: str, by_proximity: bool = False) -> int:
    """Stable argmax, i.e. ties keep detector entry order exactly like `sorted`.

    `by_proximity` simulates the alternative tie-break (D2.16) without changing
    anything in production.
    """
    keys = [arm_key(c, curve, mode) for c in candidates]
    best = max(keys)
    tied = [i for i, k in enumerate(keys) if abs(k - best) < 1e-10]
    if by_proximity:
        return min(tied, key=lambda i: candidates[i]["d_atr"])
    return tied[0]


def reaction(zone, future, contact, atr):
    """Behaviour in the `REACTION_BARS` bars after first contact.

    `rejection` is the excursion back toward the origin side (the level held),
    `through` the excursion past the far side (it gave way); both in ATR, both
    measured from the band's own edges, so they are not two views of one number.
    """
    if contact is None or atr <= 0 or len(future) < contact + REACTION_BARS:
        return None
    window = future[contact : contact + REACTION_BARS]
    if zone.side == LiquiditySide.BUY_SIDE:
        closed_through = any(c.close > zone.price_high for c in window)
        rejection = (zone.price_low - min(c.low for c in window)) / atr
        through = (max(c.high for c in window) - zone.price_high) / atr
    else:
        closed_through = any(c.close < zone.price_low for c in window)
        rejection = (max(c.high for c in window) - zone.price_high) / atr
        through = (zone.price_low - min(c.low for c in window)) / atr
    rejection, through = max(0.0, rejection), max(0.0, through)
    return {
        "closed_through": closed_through,
        "rejection_atr": rejection,
        "through_atr": through,
        "ratio": rejection / through if through > 1e-9 else None,
    }


def swing_key(zone):
    return (zone.side.value, zone.formed_at.isoformat(), round(zone.price_high, 10))


def observe(zones, price, atr, future):
    ranked = LiquidityScoringEngine().score([z for z in zones if not z.is_mitigated], price)
    if not ranked:
        return None
    candidates = []
    for scored in ranked:
        z = scored.zone
        delta = abs(midpoint(z) - price)
        contact = first_contact(z, future)
        candidates.append(
            {
                "family": family(z),
                "type": z.zone_type.value,
                "side": z.side.value,
                "above": midpoint(z) > price,
                "d_pct": delta / price * 100,
                "d_atr": delta / atr if atr else None,
                "width_atr": (z.price_high - z.price_low) / atr if atr else None,
                "strength": z.strength,
                "touch_score": scored.touch_score,
                "distance_score": scored.distance_score,
                "timeframe_score": scored.timeframe_score,
                "contact": contact,
                "future_bars": len(future),
                "reaction": reaction(z, future, contact, atr),
            }
        )
    return candidates


def audit(path: Path, expected: dict) -> dict:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected["sha256"]:
        raise ValueError(f"fixture changed: {path.name}")
    data = json.loads(raw)
    bars = [Candle.model_validate(c) for c in data["candles"][:-1]]
    rows: list[dict] = []
    previous_swings: dict = {}
    for end in range(START, len(bars) - MIN_FUTURE + 1, STEP):
        prefix = bars[:end]
        swings = [*SwingHighDetector().detect(prefix), *SwingLowDetector().detect(prefix)]
        # D2.17: the same pivot, one step of series growth later. Only the
        # denominator (`price_range` of the whole series) can have moved.
        drift = [
            {
                "before": previous_swings[swing_key(s)],
                "after": s.strength,
                "tf": data["timeframe"],
            }
            for s in swings
            if swing_key(s) in previous_swings
        ]
        previous_swings = {swing_key(s): s.strength for s in swings}
        zones = mark_swept_zones(
            [
                *swings,
                *FACTORIES["EQH"]().detect(prefix),
                *FACTORIES["EQL"]().detect(prefix),
            ],
            prefix,
        )
        price = prefix[-1].close
        atr = atr14(prefix)
        rows.append(
            {
                "end": end,
                "block": (end - 1) * 4 // len(bars),
                "observed_at": prefix[-1].timestamp.isoformat(),
                "price": price,
                "atr": atr,
                "atr_pct": atr / price * 100,
                "swing_drift": drift,
                "candidates": observe(zones, price, atr, bars[end : end + max(HORIZONS)]) or [],
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
                "start": START,
                "step": STEP,
                "min_future": MIN_FUTURE,
                "horizons": list(HORIZONS),
                "reaction_bars": REACTION_BARS,
                "arms": [[name, curve, mode] for name, curve, mode in ARMS],
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
    drift = []
    for chart in data["charts"]:
        for row in chart["rows"]:
            for item in row["swing_drift"]:
                drift.append(item)
            if not row["candidates"]:
                continue
            observations.append(
                {
                    "symbol": chart["symbol"],
                    "tf": chart["timeframe"],
                    "block": row["block"],
                    "observed_at": row["observed_at"],
                    "price": row["price"],
                    "atr": row["atr"],
                    "atr_pct": row["atr_pct"],
                    "candidates": row["candidates"],
                }
            )
    return data, observations, drift


def winners(observations, curve, mode, by_proximity=False):
    """The winner each arm picks, one per observation, in observation order."""
    return [
        obs["candidates"][pick(obs["candidates"], curve, mode, by_proximity)]
        for obs in observations
    ]


def median(values):
    kept = [v for v in values if v is not None]
    return st.median(kept) if kept else float("nan")


def report() -> None:  # noqa: C901 - a report is a sequence of tables
    data, observations, drift = load()
    out = print
    discovery = [o for o in observations if o["block"] in DISCOVERY_BLOCKS]
    holdout = [o for o in observations if o["block"] in HOLDOUT_BLOCKS]
    all_candidates = [c for o in observations for c in o["candidates"]]

    out(f"# observations={len(observations)} candidates={len(all_candidates)}")
    out(f"# discovery(blocks 0-1)={len(discovery)} holdout(blocks 2-3)={len(holdout)}")
    out(f"# arms={len(ARMS)} horizons={data['horizons']}")

    out("\n## D2.6 saturation of the distance channel, per curve")
    for curve in CURVES:
        for tf in TFS:
            obs_tf = [o for o in observations if o["tf"] == tf]
            cands = [c for o in obs_tf for c in o["candidates"]]
            zero = sum(distance_score(c, curve) == 0 for c in cands) / len(cands) * 100
            all_zero = (
                sum(
                    all(distance_score(c, curve) == 0 for c in o["candidates"]) for o in obs_tf
                )
                / len(obs_tf)
                * 100
            )
            scores = [distance_score(c, curve) for c in cands]
            out(
                f"{curve:>13} {tf:>3} zeroed={zero:6.2f}% all-zero obs={all_zero:6.2f}% "
                f"score p25={sorted(scores)[len(scores) // 4]:6.2f} "
                f"p50={median(scores):6.2f} p75={sorted(scores)[len(scores) * 3 // 4]:6.2f}"
            )

    out("\n## D2.5/D2.14/D2.15 winner distance in ATR, per arm and TF")
    for name, curve, mode in ARMS:
        for tf in TFS:
            picks = winners([o for o in observations if o["tf"] == tf], curve, mode)
            d = sorted(c["d_atr"] for c in picks)
            out(
                f"{name:<28} {tf:>3} n={len(d):4d} "
                f"p50={d[len(d) // 2]:6.2f} p75={d[len(d) * 3 // 4]:6.2f} "
                f"p90={d[len(d) * 9 // 10]:6.2f} "
                f">1={sum(v > 1 for v in d) / len(d) * 100:5.1f}% "
                f">2={sum(v > 2 for v in d) / len(d) * 100:5.1f}% "
                f">3={sum(v > 3 for v in d) / len(d) * 100:5.1f}% "
                f">5={sum(v > 5 for v in d) / len(d) * 100:5.1f}%"
            )

    legacy = winners(observations, "LEGACY_PCT", "all")
    out("\n## D2.7/D2.8 winner identity vs LEGACY, and family share")
    for name, curve, mode in ARMS:
        picks = winners(observations, curve, mode)
        changed = [
            (a, b) for a, b in zip(legacy, picks, strict=True) if a is not b
        ]
        eq_to_sw = sum(a["family"] == "EQ" and b["family"] == "SW" for a, b in changed)
        sw_to_eq = sum(a["family"] == "SW" and b["family"] == "EQ" for a, b in changed)
        side_flip = sum(a["above"] != b["above"] for a, b in changed)
        out(
            f"{name:<28} changed={len(changed) / len(picks) * 100:6.2f}% "
            f"EQ->SW={eq_to_sw:5d} SW->EQ={sw_to_eq:4d} side flip={side_flip:5d} "
            f"| EQ share={sum(c['family'] == 'EQ' for c in picks) / len(picks) * 100:5.1f}% "
            f"above={sum(c['above'] for c in picks) / len(picks) * 100:5.1f}% "
            f"median d_atr={median([c['d_atr'] for c in picks]):5.2f}"
        )

    out("\n## D2.9 pathology: winner >3 ATR while a <=1 ATR candidate existed")
    eligible = [o for o in observations if any(c["d_atr"] <= 1 for c in o["candidates"])]
    out(f"observations with a <=1 ATR candidate: {len(eligible)}")
    for name, curve, mode in ARMS:
        picks = winners(eligible, curve, mode)
        far = [c for c in picks if c["d_atr"] > 3]
        fams = {f: sum(c["family"] == f for c in far) for f in ("EQ", "SW")}
        out(
            f"{name:<28} far winners={len(far):4d} "
            f"({len(far) / len(eligible) * 100:5.2f}% of eligible) "
            f"EQ={fams['EQ']:4d} SW={fams['SW']:4d}"
        )

    out("\n## D2.10 reachability of the winner (paired: same observations)")
    for label, subset in (("all", observations), ("discovery", discovery), ("holdout", holdout)):
        out(f"-- {label} (n={len(subset)})")
        for name, curve, mode in ARMS:
            picks = winners(subset, curve, mode)
            cells = " ".join(
                f"h{h}={pct(rate([contacted(c, h) for c in picks])[0])}" for h in HORIZONS
            )
            out(f"{name:<28} {cells}")

    out("\n## D2.11/D2.12 reaction after contact, winners only (reported beside, never blended)")
    for name, curve, mode in ARMS:
        picks = [c for c in winners(observations, curve, mode) if c["reaction"]]
        for fam in ("EQ", "SW"):
            sel = [c for c in picks if c["family"] == fam]
            if len(sel) < 30:
                out(f"{name:<28} {fam} n={len(sel):4d} (too few to read)")
                continue
            reactions = [c["reaction"] for c in sel]
            through_rate = sum(r["closed_through"] for r in reactions) / len(reactions) * 100
            ratios = [r["ratio"] for r in reactions if r["ratio"] is not None]
            out(
                f"{name:<28} {fam} n={len(sel):4d} "
                f"through={through_rate:5.1f}% "
                f"rejection={median([r['rejection_atr'] for r in reactions]):5.3f} ATR "
                f"through_exc={median([r['through_atr'] for r in reactions]):5.3f} ATR "
                f"rej/thr={median(ratios):5.3f}"
            )

    out("\n## D2.13 winner vs the nearest level on the winner's own side")
    for name, curve, mode in ARMS[:1] + ARMS[1:4] + ARMS[-1:]:
        gaps = []
        for obs in observations:
            winner = obs["candidates"][pick(obs["candidates"], curve, mode)]
            same = min(
                (c for c in obs["candidates"] if c["above"] == winner["above"]),
                key=lambda c: c["d_atr"],
            )
            gaps.append(winner["d_atr"] - same["d_atr"])
        out(
            f"{name:<28} winner is the nearest of its side in "
            f"{sum(g <= 1e-9 for g in gaps) / len(gaps) * 100:5.2f}% "
            f"| median excess={median(gaps):5.2f} ATR"
        )

    out("\n## D2.16 tie-break: entry order vs proximity, per arm")
    for name, curve, mode in ARMS:
        order = winners(observations, curve, mode)
        prox = winners(observations, curve, mode, by_proximity=True)
        moved = sum(a is not b for a, b in zip(order, prox, strict=True))
        share = moved / len(order) * 100
        out(f"{name:<28} moved by a proximity tie-break: {moved:4d} ({share:5.2f}%)")

    out("\n## D2.17 window dependence of swing strength (same pivot, +100 bars)")
    for tf in TFS:
        sel = [d for d in drift if d["tf"] == tf and d["before"] > 0]
        rel = [abs(d["after"] - d["before"]) / d["before"] * 100 for d in sel]
        changed = sum(abs(d["after"] - d["before"]) > 1e-12 for d in sel)
        out(
            f"{tf:>3} pivots re-seen={len(sel):6d} "
            f"strength changed={changed / len(sel) * 100:6.2f}% "
            f"median |delta|={median(rel):6.2f}% p90={sorted(rel)[len(rel) * 9 // 10]:7.2f}%"
        )
    out("arms with mode='none' carry no such term at all (strength is not read).")

    out("\n## D2.19 temporal stability: far-winner rate per block")
    for name, curve, mode in ARMS:
        cells = []
        for block in range(4):
            sel = [o for o in eligible if o["block"] == block]
            picks = winners(sel, curve, mode) if sel else []
            cells.append(
                f"b{block}={sum(c['d_atr'] > 3 for c in picks) / len(picks) * 100:5.2f}%"
                if picks
                else f"b{block}=n/a"
            )
        out(f"{name:<28} " + " ".join(cells))

    out("\n## D2.20 per symbol: change in far-winner rate vs LEGACY (lower is better)")
    symbols = sorted({o["symbol"] for o in observations})
    for name, curve, mode in ARMS:
        if name == "LEGACY":
            continue
        deltas = []
        for symbol in symbols:
            sel = [o for o in eligible if o["symbol"] == symbol]
            if len(sel) < 5:
                continue
            base = winners(sel, "LEGACY_PCT", "all")
            arm = winners(sel, curve, mode)
            deltas.append(
                sum(c["d_atr"] > 3 for c in arm) / len(arm)
                - sum(c["d_atr"] > 3 for c in base) / len(base)
            )
        if not deltas:
            continue
        out(
            f"{name:<28} symbols={len(deltas):3d} better={sum(d < 0 for d in deltas):3d} "
            f"worse={sum(d > 0 for d in deltas):3d} same={sum(d == 0 for d in deltas):3d} "
            f"median={median(deltas) * 100:+6.2f}pp "
            f"worst={max(deltas) * 100:+6.2f}pp"
        )

    out("\n## D2.21 concrete cases (BTC / ETH / SOL)")
    # The surviving candidate arm, not the degenerate one: cases are read
    # against the change actually under consideration.
    best = ("ATR_CURRENT_STRENGTH__LINEAR_5_ATR", "LINEAR_5_ATR", "all")
    shown = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0}
    for obs in observations:
        if obs["symbol"] not in CASE_SYMBOLS:
            continue
        base = obs["candidates"][pick(obs["candidates"], "LEGACY_PCT", "all")]
        arm = obs["candidates"][pick(obs["candidates"], best[1], best[2])]
        by_prox = obs["candidates"][pick(obs["candidates"], best[1], best[2], True)]
        tag = None
        if base["d_atr"] > 3 and arm["d_atr"] <= 1:
            tag = "A"
        elif arm["d_atr"] > base["d_atr"] + 1:
            tag = "B"
        elif all(c["distance_score"] == 0 for c in obs["candidates"]):
            tag = "C"
        elif by_prox is not arm:
            tag = "D"
        elif base["family"] != arm["family"]:
            tag = "E"
        if tag and shown[tag] < 2:
            shown[tag] += 1
            out(
                f"[{tag}] {obs['symbol']} {obs['tf']} {obs['observed_at']} "
                f"price={obs['price']:.4f} "
                f"atr={obs['atr']:.4f} | LEGACY {base['type']} @{base['d_atr']:.2f} ATR "
                f"(strength {base['strength']:.3f}) -> ARM {arm['type']} @{arm['d_atr']:.2f} ATR "
                f"(strength {arm['strength']:.3f})"
            )
    out(f"case arm: {best[0]}; tags A/B/C/D/E as defined in the D2.21 prompt.")
