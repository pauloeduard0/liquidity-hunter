"""D3: can `strength` survive as a *relative position inside its own family*?

Research only; production is untouched. The distance curve is frozen at the
D2 survivor (`LINEAR_5_ATR`) so that this round isolates the strength channel.

Pre-registration (D3.1), fixed before any outcome of this round was read:

- `FAMILY_RANK` — the candidate's midrank percentile among the candidates of
  *its own family* visible in that same observation, scaled to [0, 100]. A
  family with a single visible member scores **50**, not 100: one member
  carries no relative information, and handing it the whole channel would
  rebuild the very bias this round exists to avoid.
- `FAMILY_ROBUST` — the same percentile, but against the causal expanding
  history of that family on that symbol/timeframe (every candidate published
  in strictly earlier observations of the replay). Neutral 50 until 20 prior
  samples exist. This is the "is a strong EQ strong *for an EQ here*" reading,
  where `FAMILY_RANK` only asks "is it the strongest EQ on screen right now".
- `NO_STRENGTH` — the known baseline.

No weight is searched (D3.5): the normalized value feeds the existing 0.4
`touch_score` channel unchanged. EQ and swing are never normalized together.

An analytic note that the round then checks empirically (D3.13): every swing
in one prefix divides its prominence by the *same* `price_range`, so a
cross-sectional rank inside the swing family is invariant to that denominator
— which is exactly the window-dependence defect D1/D2 measured. A rank against
history is not invariant, because the history was built under other windows.
"""

from __future__ import annotations

import hashlib
import json
import statistics as st
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from liquidity_hunter.core.domain import Candle
from liquidity_hunter.liquidity.detectors import SwingHighDetector, SwingLowDetector
from liquidity_hunter.liquidity.mitigation import mark_swept_zones
from liquidity_hunter.scoring.engine import LiquidityScoringEngine
from research.dominant_liquidity_distance_audit import (
    CURVES,
    reaction,
    swing_key,
)
from research.dominant_liquidity_strength_audit import (
    HORIZONS,
    MIN_FUTURE,
    REACTION_BARS,
    START,
    STEP,
    TFS,
    atr14,
    bucket_of,
    contacted,
    family,
    first_contact,
    midpoint,
    pct,
    rate,
    stratified_split,
)
from research.eq_levels_e1 import FACTORIES

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "research/.replay_cache/dominant_liquidity_d3_baseline.json"

CURVE = "LINEAR_5_ATR"  # D3.3: frozen, no other curve is tested here
NEUTRAL = 50.0
ROBUST_WARMUP = 20

#: (arm, distance source, strength channel)
ARMS = [
    ("LEGACY", "pct", "current"),
    ("L5_CURRENT", "atr", "current"),
    ("L5_NO_STRENGTH", "atr", "none"),
    ("L5_FAMILY_RANK", "atr", "rank"),
    ("L5_FAMILY_ROBUST", "atr", "robust"),
]

DISCOVERY_BLOCKS = (0, 1)
HOLDOUT_BLOCKS = (2, 3)


def midrank_percentile(value: float, population: list[float]) -> float:
    """Position of `value` inside `population` (which includes it), in [0, 100].

    Midranks, so exact ties share one position and no ordering is invented. A
    population of one is neutral by pre-registration, not maximal.
    """
    if len(population) < 2:
        return NEUTRAL
    below = sum(other < value for other in population)
    equal = sum(other == value for other in population)
    return (below + 0.5 * (equal - 1)) / (len(population) - 1) * 100.0


def family_ranks(candidates: list[dict]) -> None:
    """Attach the cross-sectional family percentile to each candidate in place.

    Causal by construction: the population is the candidates visible in this
    very observation, nothing later and nothing from another chart.
    """
    populations: dict[str, list[float]] = defaultdict(list)
    for candidate in candidates:
        populations[candidate["family"]].append(candidate["strength"])
    for candidate in candidates:
        candidate["rank_score"] = midrank_percentile(
            candidate["strength"], populations[candidate["family"]]
        )


def robust_ranks(candidates: list[dict], history: dict[str, list[float]]) -> None:
    """Attach the percentile against this chart's *earlier* candidates.

    `history` holds only strengths published in strictly previous observations;
    the caller updates it after this call, never before.
    """
    for candidate in candidates:
        past = history[candidate["family"]]
        candidate["robust_score"] = (
            NEUTRAL
            if len(past) < ROBUST_WARMUP
            else midrank_percentile(candidate["strength"], [*past, candidate["strength"]])
        )


def strength_channel(candidate: dict, mode: str) -> float:
    if mode == "none":
        return 0.0
    if mode == "current":
        return candidate["touch_score"]
    if mode == "rank":
        return candidate["rank_score"]
    if mode == "robust":
        return candidate["robust_score"]
    raise ValueError(mode)


def distance_channel(candidate: dict, source: str) -> float:
    if source == "pct":
        return candidate["distance_score"]
    return CURVES[CURVE](candidate["d_atr"])


def arm_key(candidate: dict, source: str, mode: str) -> float:
    """Production weights, unchanged (D3.5). Only the channels' content moves."""
    return (
        distance_channel(candidate, source) * 0.4
        + strength_channel(candidate, mode) * 0.4
        + candidate["timeframe_score"] * 0.2
    )


def pick(candidates: list[dict], source: str, mode: str, by_proximity: bool = False) -> int:
    keys = [arm_key(c, source, mode) for c in candidates]
    best = max(keys)
    tied = [i for i, k in enumerate(keys) if abs(k - best) < 1e-10]
    if by_proximity:
        return min(tied, key=lambda i: candidates[i]["d_atr"])
    return tied[0]


def observe(zones, price, atr, future, history):
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
                "above": midpoint(z) > price,
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
    family_ranks(candidates)
    robust_ranks(candidates, history)
    return candidates


def rank_stability(previous: dict, swings: list) -> dict:
    """D3.13: the same pivot, one step of series growth later.

    Both readings are taken over the *same population* — the pivots present in
    both prefixes — so a change cannot come from new pivots joining the family.
    What is left is the normalization: `raw_changed` counts pivots whose
    production strength moved, `rank_changed` those whose family percentile
    moved. New pivots are a legitimate reason for a rank to move and are
    deliberately excluded from this probe.
    """
    current = {swing_key(s): s.strength for s in swings}
    shared = [k for k in current if k in previous]
    before = [previous[k] for k in shared]
    after = [current[k] for k in shared]
    raw_changed = sum(abs(a - b) > 1e-12 for a, b in zip(after, before, strict=True))
    rank_changed = sum(
        abs(midrank_percentile(a, after) - midrank_percentile(b, before)) > 1e-12
        for a, b in zip(after, before, strict=True)
    )
    return {
        "current": current,
        "counts": {
            "shared": len(shared),
            "same_membership": set(current) == set(previous),
            "raw_changed": raw_changed,
            "rank_changed": rank_changed,
        },
    }


def audit(path: Path, expected: dict) -> dict:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected["sha256"]:
        raise ValueError(f"fixture changed: {path.name}")
    data = json.loads(raw)
    bars = [Candle.model_validate(c) for c in data["candles"][:-1]]
    rows: list[dict] = []
    history: dict[str, list[float]] = defaultdict(list)
    previous: dict = {}
    stability: list[dict] = []
    for end in range(START, len(bars) - MIN_FUTURE + 1, STEP):
        prefix = bars[:end]
        swings = [*SwingHighDetector().detect(prefix), *SwingLowDetector().detect(prefix)]
        if previous:
            probe = rank_stability(previous, swings)
            stability.append(probe["counts"])
            previous = probe["current"]
        else:
            previous = rank_stability({}, swings)["current"]
        zones = mark_swept_zones(
            [*swings, *FACTORIES["EQH"]().detect(prefix), *FACTORIES["EQL"]().detect(prefix)],
            prefix,
        )
        price = prefix[-1].close
        atr = atr14(prefix)
        candidates = observe(zones, price, atr, bars[end : end + max(HORIZONS)], history) or []
        for candidate in candidates:  # history stays strictly causal
            history[candidate["family"]].append(candidate["strength"])
        rows.append(
            {
                "end": end,
                "block": (end - 1) * 4 // len(bars),
                "observed_at": prefix[-1].timestamp.isoformat(),
                "price": price,
                "atr": atr,
                "candidates": candidates,
            }
        )
    return {
        "symbol": data["symbol"],
        "timeframe": data["timeframe"],
        "sha256": expected["sha256"],
        "stability": stability,
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
                "neutral": NEUTRAL,
                "robust_warmup": ROBUST_WARMUP,
                "arms": [list(a) for a in ARMS],
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


def load():
    data = json.loads(BASELINE.read_text())
    observations, stability = [], []
    for chart in data["charts"]:
        stability.extend({**s, "tf": chart["timeframe"]} for s in chart["stability"])
        for row in chart["rows"]:
            if row["candidates"]:
                observations.append({**row, "symbol": chart["symbol"], "tf": chart["timeframe"]})
    return data, observations, stability


def winners(observations, source, mode, by_proximity=False):
    return [
        obs["candidates"][pick(obs["candidates"], source, mode, by_proximity)]
        for obs in observations
    ]


def median(values):
    kept = [v for v in values if v is not None]
    return st.median(kept) if kept else float("nan")


def quantile(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def report() -> None:  # noqa: C901 - a report is a sequence of tables
    data, observations, stability = load()
    out = print
    discovery = [o for o in observations if o["block"] in DISCOVERY_BLOCKS]
    holdout = [o for o in observations if o["block"] in HOLDOUT_BLOCKS]
    cands = [c for o in observations for c in o["candidates"]]

    out(f"# observations={len(observations)} candidates={len(cands)} curve={data['curve']}")
    out(f"# discovery={len(discovery)} holdout={len(holdout)} warmup={data['robust_warmup']}")

    out("\n## D3.1 the normalized channels, as distributions (0-100)")
    for fam in ("EQ", "SW"):
        sel = [c for c in cands if c["family"] == fam]
        channels = (("current", "touch_score"), ("rank", "rank_score"), ("robust", "robust_score"))
        for label, key in channels:
            values = [c[key] for c in sel]
            neutral_share = sum(abs(v - NEUTRAL) < 1e-9 for v in values) / len(values) * 100
            out(
                f"{fam} {label:>7} n={len(values):6d} "
                f"p10={quantile(values, 0.10):6.2f} p50={median(values):6.2f} "
                f"p90={quantile(values, 0.90):6.2f} mean={st.mean(values):6.2f} "
                f"at-100={sum(v >= 99.999 for v in values) / len(values) * 100:5.1f}% "
                f"at-neutral={neutral_share:5.1f}%"
            )
    lone = sum(
        len([c for c in o["candidates"] if c["family"] == f]) == 1
        for o in observations
        for f in ("EQ", "SW")
    )
    out(f"single-member families (scored neutral by pre-registration): {lone}")

    out("\n## D3.6 family share of the winner, per arm and TF")
    for name, source, mode in ARMS:
        cells = []
        for tf in TFS:
            picks = winners([o for o in observations if o["tf"] == tf], source, mode)
            share = sum(c["family"] == "EQ" for c in picks) / len(picks) * 100
            cells.append(f"{tf}: EQ={share:5.1f}%")
        overall = winners(observations, source, mode)
        out(
            f"{name:<18} " + "  ".join(cells)
            + f"  | all EQ={sum(c['family'] == 'EQ' for c in overall) / len(overall) * 100:5.1f}%"
        )

    out("\n## D3.7 winner distance in ATR, per arm and TF")
    for name, source, mode in ARMS:
        for tf in TFS:
            picks = winners([o for o in observations if o["tf"] == tf], source, mode)
            d = sorted(c["d_atr"] for c in picks)
            out(
                f"{name:<18} {tf:>3} p50={quantile(d, 0.5):6.2f} p75={quantile(d, 0.75):6.2f} "
                f"p90={quantile(d, 0.90):6.2f} "
                f">1={sum(v > 1 for v in d) / len(d) * 100:5.1f}% "
                f">2={sum(v > 2 for v in d) / len(d) * 100:5.1f}% "
                f">3={sum(v > 3 for v in d) / len(d) * 100:5.1f}% "
                f">5={sum(v > 5 for v in d) / len(d) * 100:5.1f}%"
            )

    out("\n## D3.8 pathology: winner >3 ATR while a <=1 ATR candidate existed")
    eligible = [o for o in observations if any(c["d_atr"] <= 1 for c in o["candidates"])]
    out(f"eligible observations={len(eligible)}")
    for name, source, mode in ARMS:
        far = [c for c in winners(eligible, source, mode) if c["d_atr"] > 3]
        out(
            f"{name:<18} far={len(far):4d} ({len(far) / len(eligible) * 100:5.2f}%) "
            f"EQ={sum(c['family'] == 'EQ' for c in far):4d} "
            f"SW={sum(c['family'] == 'SW' for c in far):4d}"
        )

    out("\n## D3.9 reachability of the winner (paired)")
    for label, subset in (("all", observations), ("discovery", discovery), ("holdout", holdout)):
        out(f"-- {label} (n={len(subset)})")
        for name, source, mode in ARMS:
            picks = winners(subset, source, mode)
            out(
                f"{name:<18} "
                + " ".join(
                    f"h{h}={pct(rate([contacted(c, h) for c in picks])[0])}" for h in HORIZONS
                )
            )

    out("\n## D3.10 reaction after contact (MFE=rejection, MAE=excursion beyond)")
    for name, source, mode in ARMS:
        for fam in ("EQ", "SW"):
            picks = winners(observations, source, mode)
            sel = [c for c in picks if c["reaction"] and c["family"] == fam]
            if len(sel) < 30:
                out(f"{name:<18} {fam} n={len(sel):4d} (too few to read)")
                continue
            rx = [c["reaction"] for c in sel]
            ratios = [r["ratio"] for r in rx if r["ratio"] is not None]
            out(
                f"{name:<18} {fam} n={len(sel):4d} "
                f"through={sum(r['closed_through'] for r in rx) / len(rx) * 100:5.1f}% "
                f"MFE={median([r['rejection_atr'] for r in rx]):5.3f} "
                f"MAE={median([r['through_atr'] for r in rx]):5.3f} "
                f"MFE/MAE={median(ratios):5.3f}"
            )

    out("\n## D3.11 does the family rank add anything over no strength at all?")
    base = winners(observations, "atr", "none")
    for name, source, mode in ARMS:
        if mode == "none":
            continue
        picks = winners(observations, source, mode)
        differing = [
            (b, p) for b, p in zip(base, picks, strict=True) if b is not p
        ]
        if not differing:
            out(f"{name:<18} identical to L5_NO_STRENGTH")
            continue
        for horizon in (20, 40):
            arm_hits = rate([contacted(p, horizon) for _, p in differing])
            base_hits = rate([contacted(b, horizon) for b, _ in differing])
            out(
                f"{name:<18} differs in {len(differing):4d} obs "
                f"| h{horizon} arm={pct(arm_hits[0])} vs no-strength={pct(base_hits[0])}"
            )
        arm_rx = [p["reaction"] for _, p in differing if p["reaction"]]
        base_rx = [b["reaction"] for b, _ in differing if b["reaction"]]
        out(
            f"{'':<18} on those: through arm="
            f"{sum(r['closed_through'] for r in arm_rx) / len(arm_rx) * 100:5.1f}% "
            f"vs {sum(r['closed_through'] for r in base_rx) / len(base_rx) * 100:5.1f}% "
            f"| MFE {median([r['rejection_atr'] for r in arm_rx]):5.3f} "
            f"vs {median([r['rejection_atr'] for r in base_rx]):5.3f} "
            f"| d_atr {median([p['d_atr'] for _, p in differing]):5.2f} "
            f"vs {median([b['d_atr'] for b, _ in differing]):5.2f}"
        )

    out("\n## D3.11b same question, distance-matched (|d_atr difference| <= 0.25)")
    for name, source, mode in ARMS:
        if mode == "none":
            continue
        picks = winners(observations, source, mode)
        pairs = [
            (b, p)
            for b, p in zip(base, picks, strict=True)
            if b is not p and abs(b["d_atr"] - p["d_atr"]) <= 0.25
        ]
        if len(pairs) < 30:
            out(f"{name:<18} matched pairs={len(pairs)} (too few to read)")
            continue
        arm_rx = [p["reaction"] for _, p in pairs if p["reaction"]]
        base_rx = [b["reaction"] for b, _ in pairs if b["reaction"]]
        out(
            f"{name:<18} matched pairs={len(pairs):4d} "
            f"| h20 arm={pct(rate([contacted(p, 20) for _, p in pairs])[0])} "
            f"vs {pct(rate([contacted(b, 20) for b, _ in pairs])[0])} "
            f"| through {sum(r['closed_through'] for r in arm_rx) / len(arm_rx) * 100:5.1f}% "
            f"vs {sum(r['closed_through'] for r in base_rx) / len(base_rx) * 100:5.1f}% "
            f"| MFE {median([r['rejection_atr'] for r in arm_rx]):5.3f} "
            f"vs {median([r['rejection_atr'] for r in base_rx]):5.3f}"
        )

    out("\n## D3.11c candidate level: does the channel separate inside a distance bucket?")
    keys = [lambda c: (c["symbol"], c["tf"], c["above"], c["bucket"], c["block"])]
    channels = (
        ("raw (D1)", "touch_score"),
        ("family rank", "rank_score"),
        ("robust", "robust_score"),
    )
    for label, key in channels:
        for fam in ("EQ", "SW"):
            rows = [
                {
                    **c,
                    "strength": c[key],
                    "symbol": o["symbol"],
                    "tf": o["tf"],
                    "block": o["block"],
                    "bucket": bucket_of(c["d_atr"]),
                }
                for o in observations
                for c in o["candidates"]
                if c["family"] == fam
            ]
            res = stratified_split(rows, lambda c: contacted(c, 20), keys)
            out(
                f"{label:<12} {fam} n={len(rows):6d} "
                + (
                    f"high={pct(res['high'])} low={pct(res['low'])} gap={res['gap'] * 100:+.2f}pp "
                    f"strata={res['strata']} w/l={res['wins']}/{res['losses']}"
                    if res
                    else "no usable strata"
                )
            )

    out("\n## D3.12 winner quality: how strong is the winner inside its own family")
    for name, source, mode in ARMS:
        picks = winners(observations, source, mode)
        top_share = sum(c["rank_score"] >= 99.999 for c in picks) / len(picks) * 100
        out(
            f"{name:<18} median family percentile={median([c['rank_score'] for c in picks]):6.2f} "
            f"top-of-family={top_share:5.1f}% "
            f"median d_atr={median([c['d_atr'] for c in picks]):5.2f}"
        )

    out("\n## D3.13 window dependence: same pivots, +100 bars, population held fixed")
    for tf in TFS:
        sel = [s for s in stability if s["tf"] == tf and s["shared"] > 1]
        shared = sum(s["shared"] for s in sel)
        out(
            f"{tf:>3} steps={len(sel):5d} pivots={shared:6d} "
            f"raw strength changed={sum(s['raw_changed'] for s in sel) / shared * 100:6.2f}% "
            f"family rank changed={sum(s['rank_changed'] for s in sel) / shared * 100:6.2f}%"
        )
    everything = [s for s in stability if s["shared"] > 1]
    total = sum(s["shared"] for s in everything)
    out(
        f"all timeframes: raw="
        f"{sum(s['raw_changed'] for s in everything) / total * 100:.2f}% "
        f"rank={sum(s['rank_changed'] for s in everything) / total * 100:.2f}%"
    )

    out("\n## D3.14 tie-break diagnosis (entry order vs proximity), unchanged in every arm")
    for name, source, mode in ARMS:
        order = winners(observations, source, mode)
        prox = winners(observations, source, mode, by_proximity=True)
        moved = sum(a is not b for a, b in zip(order, prox, strict=True))
        out(f"{name:<18} moved={moved:4d} ({moved / len(order) * 100:5.2f}%)")

    out("\n## D3.16 robustness: far-winner rate vs LEGACY, per symbol / block / side")
    symbols = sorted({o["symbol"] for o in observations})
    for name, source, mode in ARMS[1:]:
        deltas = []
        for symbol in symbols:
            sel = [o for o in eligible if o["symbol"] == symbol]
            if len(sel) < 5:
                continue
            arm = winners(sel, source, mode)
            legacy = winners(sel, "pct", "current")
            deltas.append(
                sum(c["d_atr"] > 3 for c in arm) / len(arm)
                - sum(c["d_atr"] > 3 for c in legacy) / len(legacy)
            )
        cells = []
        for block in range(4):
            sel = [o for o in eligible if o["block"] == block]
            far = winners(sel, source, mode) if sel else []
            rate_far = sum(c["d_atr"] > 3 for c in far) / len(far) * 100 if far else float("nan")
            cells.append(f"b{block}={rate_far:5.2f}%")
        blocks = " ".join(cells)
        out(
            f"{name:<18} symbols={len(deltas):3d} better={sum(d < 0 for d in deltas):3d} "
            f"worse={sum(d > 0 for d in deltas):3d} median={median(deltas) * 100:+6.2f}pp "
            f"worst={max(deltas) * 100:+6.2f}pp | {blocks}"
        )
    for name, source, mode in ARMS:
        picks = winners(observations, source, mode)
        above = sum(c["above"] for c in picks) / len(picks) * 100
        out(
            f"{name:<18} winner above price={above:5.1f}%"
        )

    out("\n## D3.17 interpretability: mean contribution of each channel to the winner")
    for name, source, mode in ARMS:
        picks = winners(observations, source, mode)
        dist = [distance_channel(c, source) * 0.4 for c in picks]
        strength = [strength_channel(c, mode) * 0.4 for c in picks]
        decided = sum(
            strength_channel(w, mode) > strength_channel(n, mode)
            and distance_channel(w, source) < distance_channel(n, source)
            for w, n in zip(
                picks,
                [min(o["candidates"], key=lambda c: c["d_atr"]) for o in observations],
                strict=True,
            )
        )
        out(
            f"{name:<18} distance={st.mean(dist):6.2f} pts strength={st.mean(strength):6.2f} pts "
            f"| winner beat the nearest ON STRENGTH in {decided / len(picks) * 100:5.2f}% of obs"
        )
