"""Raid Reversal — measurement harness.

Measures a candidate entry event against baselines. Lives outside the
``liquidity_hunter`` package on purpose: the package is a research platform
and carries no entry logic. This script only *consumes* its output.

The event under test
--------------------
1. A **virgin target** exists — an equal-highs / equal-lows zone that formed
   before ``t`` and that no candle has pierced since (recomputed here from the
   candle series, never read from the zone's stored ``is_mitigated`` flag,
   which is computed with full hindsight).
2. A **raid**: one candle's wick pierces the far edge of that target and its
   close returns beyond the near edge — the stops were taken and given back.
   Raiding an equal-highs zone is a bearish event; equal-lows, bullish.
3. A **confirmation** mode selects when the entry is taken (see ``MODES``).

Measured forward: MFE / MAE in R (R = entry → the raid wick extreme), and
whether the next opposing virgin target was reached before −1R.

Lookahead discipline
--------------------
``raid`` and ``vsa`` modes use only information available at the candle they
fire on. ``choch`` does not: a confirmed CHANGE_OF_CHARACTER needs swing
pivots that form later, so it is entered ``--choch-lag`` candles after the
event timestamp. That lag is an approximation, not a proof; treat ``choch``
numbers as optimistic.

Usage
-----
    poetry run python research/raid_reversal.py
    poetry run python research/raid_reversal.py --symbols BTCUSDT ETHUSDT \
        --timeframes 15m 1h --horizons 5 10 20 40
"""

from __future__ import annotations

import argparse
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from liquidity_hunter.app import load_dashboard_data
from liquidity_hunter.core.domain import Candle, LiquidityZone, TimeFrame
from liquidity_hunter.core.domain.enums import (
    LiquidityZoneType,
    MarketDirection,
    StructureEvent,
)

BEAR = MarketDirection.BEARISH
BULL = MarketDirection.BULLISH

MODES = ("raid", "vsa", "choch")

# VSA patterns that argue for a reversal, by the direction they argue *for*.
_VSA_REVERSAL = {
    BULL: {"selling_climax", "down_thrust", "no_supply"},
    BEAR: {"buying_climax", "up_thrust", "no_demand"},
}


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Target:
    """A virgin pool of resting liquidity."""

    low: float
    high: float
    formed_at: datetime
    side: MarketDirection  # direction a raid of it argues for
    strength: float

    @property
    def far(self) -> float:
        """The edge a raid must pierce."""
        return self.high if self.side is BEAR else self.low

    @property
    def near(self) -> float:
        """The edge the close must return beyond."""
        return self.low if self.side is BEAR else self.high


@dataclass
class Event:
    kind: str  # "raid" | "sweep" | "choch" | "random"
    mode: str  # confirmation / framing: "raid" | "vsa" | "choch" | "cont" | "baseline"
    symbol: str
    timeframe: str
    direction: MarketDirection
    raid_index: int
    entry_index: int
    entry: float
    stop: float
    target: float | None
    families: int
    control_agrees: bool
    htf_aligned: bool
    r: float = 0.0
    mfe: dict[int, float] = field(default_factory=dict)
    mae: dict[int, float] = field(default_factory=dict)
    hit_target: bool | None = None
    hit_stop_first: bool = False
    target_r: float = 0.0  # distance to target, in R
    # --- selection features, all point-in-time -----------------------------
    vsa_agrees: bool = False  # a same-side VSA reversal pattern on the candle
    oi_flush: bool = False  # OI-qualified FLUSH — leveraged positions burned
    at_pool: bool = False  # the wick reached a still-virgin equal-level pool
    wick_atr: float = 0.0  # size of the rejected excursion, in ATR


# --------------------------------------------------------------------------
# point-in-time target construction
# --------------------------------------------------------------------------
def _equal_level_targets(zones: Sequence[LiquidityZone]) -> list[Target]:
    out: list[Target] = []
    for z in zones:
        if z.zone_type is LiquidityZoneType.EQUAL_HIGHS:
            side = BEAR
        elif z.zone_type is LiquidityZoneType.EQUAL_LOWS:
            side = BULL
        else:
            continue
        out.append(
            Target(
                low=z.price_low,
                high=z.price_high,
                formed_at=z.formed_at,
                side=side,
                strength=z.strength,
            )
        )
    return out


def _pierced_before(candles: Sequence[Candle], t: Target, start: int, end: int) -> bool:
    """Whether any candle in ``[start, end)`` already pierced the target."""
    for i in range(start, end):
        c = candles[i]
        if t.side is BEAR and c.high > t.far:
            return True
        if t.side is BULL and c.low < t.far:
            return True
    return False


def _index_at_or_after(candles: Sequence[Candle], ts: datetime) -> int:
    for i, c in enumerate(candles):
        if c.timestamp >= ts:
            return i
    return len(candles)


def find_raids(candles: Sequence[Candle], targets: Sequence[Target]) -> list[tuple[int, Target]]:
    """Locate every raid, point-in-time.

    A raid is the *first* piercing of a still-virgin target whose candle
    closes back beyond the near edge.
    """
    hits: list[tuple[int, Target]] = []
    for t in targets:
        start = _index_at_or_after(candles, t.formed_at) + 1
        for i in range(start, len(candles)):
            c = candles[i]
            pierced = c.high > t.far if t.side is BEAR else c.low < t.far
            if not pierced:
                continue
            # first pierce only — after this the pool is consumed
            rejected = c.close < t.near if t.side is BEAR else c.close > t.near
            if rejected:
                hits.append((i, t))
            break
    hits.sort(key=lambda h: h[0])
    return hits


def next_opposing_target(
    candles: Sequence[Candle],
    targets: Sequence[Target],
    index: int,
    direction: MarketDirection,
) -> float | None:
    """Nearest still-virgin pool in the trade's direction, as of ``index``."""
    price = candles[index].close
    best: float | None = None
    for t in targets:
        formed = _index_at_or_after(candles, t.formed_at)
        if formed >= index:
            continue
        if _pierced_before(candles, t, formed + 1, index + 1):
            continue
        if direction is BEAR and t.side is BULL and t.far < price:
            if best is None or t.far > best:
                best = t.far
        if direction is BULL and t.side is BEAR and t.far > price:
            if best is None or t.far < best:
                best = t.far
    return best


# --------------------------------------------------------------------------
# forward measurement
# --------------------------------------------------------------------------
def measure(ev: Event, candles: Sequence[Candle], horizons: Sequence[int]) -> Event:
    r = abs(ev.entry - ev.stop)
    if r <= 0:
        ev.r = 0.0
        return ev
    ev.r = r
    i0 = ev.entry_index
    for h in horizons:
        window = candles[i0 + 1 : i0 + 1 + h]
        if not window:
            continue
        hi = max(c.high for c in window)
        lo = min(c.low for c in window)
        if ev.direction is BEAR:
            ev.mfe[h] = (ev.entry - lo) / r
            ev.mae[h] = (hi - ev.entry) / r
        else:
            ev.mfe[h] = (hi - ev.entry) / r
            ev.mae[h] = (ev.entry - lo) / r

    # target vs stop, whichever comes first
    if ev.target is not None:
        ev.target_r = abs(ev.target - ev.entry) / r
        for c in candles[i0 + 1 :]:
            stopped = c.high >= ev.stop if ev.direction is BEAR else c.low <= ev.stop
            reached = c.low <= ev.target if ev.direction is BEAR else c.high >= ev.target
            if stopped and reached:
                ev.hit_stop_first = True  # ambiguous bar, count against
                ev.hit_target = False
                break
            if stopped:
                ev.hit_stop_first = True
                ev.hit_target = False
                break
            if reached:
                ev.hit_target = True
                break
    return ev


# --------------------------------------------------------------------------
# per-combo run
# --------------------------------------------------------------------------
def run_combo(
    symbol: str,
    timeframe: TimeFrame,
    *,
    limit: int,
    horizons: Sequence[int],
    choch_lag: int,
    choch_window: int,
    min_r_atr: float,
    target_mode: str,
    target_r: float,
    stop_mode: str,
    stop_atr: float,
    random_reps: int,
    rng: random.Random,
) -> list[Event]:
    data = load_dashboard_data(symbol=symbol, timeframe=timeframe, limit=limit)
    candles = data.candles
    if len(candles) < 50:
        return []

    targets = _equal_level_targets(data.liquidity_zones)
    idx_of = {c.timestamp: i for i, c in enumerate(candles)}
    htf = data.higher_timeframe_direction

    vsa_at: dict[int, set[str]] = {}
    for s in data.volume_spread_signals or []:
        i = idx_of.get(s.timestamp)
        if i is not None:
            vsa_at.setdefault(i, set()).add(s.pattern.value)

    control_at: dict[int, str] = {}
    if data.market_control:
        for p in data.market_control.series:
            i = idx_of.get(p.timestamp)
            if i is not None:
                control_at[i] = p.controller.value

    vwap_at: dict[int, float] = {}
    if data.vwap:
        for p in data.vwap.points:
            i = idx_of.get(p.timestamp)
            if i is not None:
                vwap_at[i] = p.value

    flush_at: set[int] = set()
    if data.oi_analysis:
        for qe in data.oi_analysis.qualified_events:
            if qe.participation.value == "flush":
                i = idx_of.get(qe.event_timestamp)
                if i is not None:
                    flush_at.add(i)

    choch_at: dict[int, MarketDirection] = {}
    sweep_at: dict[int, MarketDirection] = {}
    for e in data.internal_structure_events:
        if e.provisional:
            continue
        i = idx_of.get(e.timestamp)
        if i is None:
            continue
        if e.event is StructureEvent.CHANGE_OF_CHARACTER:
            choch_at[i] = e.direction
        elif e.event is StructureEvent.LIQUIDITY_SWEEP:
            sweep_at[i] = e.direction

    # A tiny wick makes R tiny and every R-multiple explode, which flatters
    # whichever event happens to fire on a doji. Floor R at a fraction of ATR.
    trs = [
        max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
        for p, c in zip(candles, candles[1:], strict=False)
    ]
    atr = statistics.mean(trs) if trs else 0.0
    min_r = min_r_atr * atr

    max_h = max(horizons)
    events: list[Event] = []

    def resolve_target(
        ei: int, d: MarketDirection, entry: float, stop: float
    ) -> float | None:
        """Where the trade is trying to go.

        ``pool``  — the next still-virgin opposing pool (the original read).
        ``rmult`` — a fixed multiple of the risk taken.
        ``vwap``  — the periodic VWAP, i.e. the mean price the current
                    population paid; ``None`` when it sits the wrong side of
                    entry (there is no reversion trade to take).
        """
        if target_mode == "rmult":
            r = abs(entry - stop)
            return entry - target_r * r if d is BEAR else entry + target_r * r
        if target_mode == "vwap":
            v = vwap_at.get(ei)
            if v is None:
                return None
            if (d is BEAR and v >= entry) or (d is BULL and v <= entry):
                return None
            return v
        return next_opposing_target(candles, targets, ei, d)

    def build(
        kind: str, mode: str, ri: int, ei: int, d: MarketDirection, fams: int,
        zone: Target | None = None,
    ) -> Event | None:
        if ei + max_h >= len(candles):
            return None
        raid = candles[ri]
        wick = raid.high if d is BEAR else raid.low
        if stop_mode == "atr":
            stop = wick + stop_atr * atr if d is BEAR else wick - stop_atr * atr
        elif stop_mode == "zone" and zone is not None:
            # beyond the whole pool, not just the wick that took it
            edge = max(zone.high, wick) if d is BEAR else min(zone.low, wick)
            stop = edge + stop_atr * atr if d is BEAR else edge - stop_atr * atr
        else:
            stop = wick
        entry = candles[ei].close
        if (d is BEAR and stop <= entry) or (d is BULL and stop >= entry):
            return None
        if abs(entry - stop) < min_r:
            return None
        who = control_at.get(ei, "balanced")
        agrees = (d is BEAR and who == "sellers") or (d is BULL and who == "buyers")
        # did the rejected wick actually reach resting liquidity?
        at_pool = False
        for t in targets:
            formed = _index_at_or_after(candles, t.formed_at)
            if formed >= ri or t.side is not d:
                continue
            if _pierced_before(candles, t, formed + 1, ri):
                continue  # already consumed — not virgin at the sweep
            if abs(wick - t.far) <= 0.5 * atr:
                at_pool = True
                break
        vsa_hit = bool(
            _VSA_REVERSAL[d] & (vsa_at.get(ri, set()) | vsa_at.get(ri - 1, set()))
        )
        return Event(
            kind=kind,
            mode=mode,
            symbol=symbol,
            timeframe=timeframe.value,
            direction=d,
            raid_index=ri,
            entry_index=ei,
            entry=entry,
            stop=stop,
            target=resolve_target(ei, d, entry, stop),
            families=fams,
            control_agrees=agrees,
            htf_aligned=(d is htf),
            vsa_agrees=vsa_hit,
            oi_flush=ri in flush_at,
            at_pool=at_pool,
            wick_atr=abs(entry - wick) / atr if atr else 0.0,
        )

    # --- the event under test, in three confirmation modes -----------------
    raids = find_raids(candles, targets)
    for ri, t in raids:
        d = t.side

        if (ev := build("raid", "raid", ri, ri, d, 1, t)) is not None:
            events.append(ev)

        if _VSA_REVERSAL[d] & vsa_at.get(ri, set()) | _VSA_REVERSAL[d] & vsa_at.get(ri + 1, set()):
            ei = ri if _VSA_REVERSAL[d] & vsa_at.get(ri, set()) else ri + 1
            if (ev := build("raid", "vsa", ri, ei, d, 1, t)) is not None:
                events.append(ev)

        for k in range(ri, min(ri + choch_window, len(candles))):
            if choch_at.get(k) is d:
                if (ev := build("raid", "choch", ri, k + choch_lag, d, 1, t)) is not None:
                    events.append(ev)
                break

        # continuation framing: the pool was consumed, keep going *with* the
        # higher timeframe instead of reading the raid as a reversal.
        if htf in (BEAR, BULL):
            if (ev := build("raid", "cont", ri, ri, htf, 1, t)) is not None:
                events.append(ev)

    # --- baselines ---------------------------------------------------------
    for i, d in sweep_at.items():
        rev = BEAR if d is BULL else BULL
        if (ev := build("sweep", "baseline", i, i, rev, 0)) is not None:
            events.append(ev)
        if htf in (BEAR, BULL) and (ev := build("sweep", "cont", i, i, htf, 0)) is not None:
            events.append(ev)

    for i, d in choch_at.items():
        if (ev := build("choch", "baseline", i, i + choch_lag, d, 0)) is not None:
            events.append(ev)

    # matched random: same direction mix, same R, arbitrary candles. Drawn
    # ``random_reps`` times per raid — one draw per raid leaves the baseline
    # noisier than the effect being measured.
    for ri, t in [(r, tt) for r, tt in raids for _ in range(random_reps)]:
        j = rng.randrange(20, max(21, len(candles) - max_h - 1))
        # stop at the same distance the raid would have given, from candle j
        raid_r = abs(candles[ri].close - (candles[ri].high if t.side is BEAR else candles[ri].low))
        if stop_mode == "atr":
            raid_r += stop_atr * atr
        elif stop_mode == "zone":
            raid_r += (t.high - t.low) + stop_atr * atr
        entry = candles[j].close
        stop = entry + raid_r if t.side is BEAR else entry - raid_r
        if j + max_h >= len(candles) or raid_r < min_r:
            continue
        # A continuation event is HTF-aligned by construction, so the mixed
        # -direction random above is not its control: it must be compared
        # against a random entry *in the same direction*.
        if htf in (BEAR, BULL):
            c_stop = entry + raid_r if htf is BEAR else entry - raid_r
            events.append(
                Event(
                    kind="random",
                    mode="cont",
                    symbol=symbol,
                    timeframe=timeframe.value,
                    direction=htf,
                    raid_index=j,
                    entry_index=j,
                    entry=entry,
                    stop=c_stop,
                    target=resolve_target(j, htf, entry, c_stop),
                    families=0,
                    control_agrees=False,
                    htf_aligned=True,
                )
            )
        events.append(
            Event(
                kind="random",
                mode="baseline",
                symbol=symbol,
                timeframe=timeframe.value,
                direction=t.side,
                raid_index=j,
                entry_index=j,
                entry=entry,
                stop=stop,
                target=resolve_target(j, t.side, entry, stop),
                families=0,
                control_agrees=False,
                htf_aligned=(t.side is htf),
            )
        )

    return [measure(e, candles, horizons) for e in events]


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
def _fmt(vals: list[float]) -> str:
    if not vals:
        return "    —"
    return f"{statistics.mean(vals):5.2f}"


def report(events: Sequence[Event], horizons: Sequence[int], focus: str) -> None:
    groups: dict[str, list[Event]] = {}
    for e in events:
        if e.r <= 0:
            continue
        key = f"{e.kind}/{e.mode}"
        groups.setdefault(key, []).append(e)

    hdr = "  ".join(f"MFE{h:<3} MAE{h:<3}" for h in horizons)
    print(f"\n{'grupo':<18} {'n':>4}  {hdr}   alvo%   dist    E[R]  edge")
    print("-" * (30 + len(hdr) + 14))

    order = ["raid/raid", "raid/vsa", "raid/choch", "raid/cont",
             "sweep/baseline", "sweep/cont", "choch/baseline", "random/baseline", "random/cont"]
    for key in order + [k for k in groups if k not in order]:
        evs = groups.get(key)
        if not evs:
            continue
        cells = []
        for h in horizons:
            cells.append(_fmt([e.mfe[h] for e in evs if h in e.mfe]))
            cells.append(_fmt([e.mae[h] for e in evs if h in e.mae]))
        resolved = [e for e in evs if e.hit_target is not None]
        won = sum(bool(e.hit_target) for e in resolved)
        tgt = f"{100 * won / len(resolved):5.1f}" if resolved else "    —"
        # expectancy in R: winners collect their target's R, losers pay 1R.
        # This is what makes targets at different distances comparable.
        if resolved:
            exp = statistics.mean(
                e.target_r if e.hit_target else -1.0 for e in resolved
            )
            rr = statistics.mean(e.target_r for e in resolved)
            extra = f" {rr:5.2f}R {exp:+5.2f}"
        else:
            extra = "     —     —"
        h0 = horizons[len(horizons) // 2]
        edge_vals = [e.mfe[h0] - e.mae[h0] for e in evs if h0 in e.mfe and h0 in e.mae]
        edge = f"{statistics.mean(edge_vals):+5.2f}" if edge_vals else "    —"
        print(f"{key:<18} {len(evs):>4}  " + "  ".join(cells)
              + f"   {tgt}{extra}  {edge}")

    # cuts, applied to one group only
    f_kind, _, f_mode = focus.partition("/")
    raids = [e for e in events if e.kind == f_kind and e.mode == f_mode and e.r > 0]
    if not raids:
        return
    h0 = horizons[len(horizons) // 2]
    print(f"\ncortes sobre {focus} (MFE−MAE em {h0} candles):")
    cuts = {
        "todos": lambda e: True,
        "VSA concorda": lambda e: e.vsa_agrees,
        "sem VSA": lambda e: not e.vsa_agrees,
        "OI flush": lambda e: e.oi_flush,
        "sem flush": lambda e: not e.oi_flush,
        "pavio em pool virgem": lambda e: e.at_pool,
        "fora de pool": lambda e: not e.at_pool,
        "pavio > 1 ATR": lambda e: e.wick_atr > 1.0,
        "pavio <= 1 ATR": lambda e: e.wick_atr <= 1.0,
        "alinhado com HTF": lambda e: e.htf_aligned,
        "contra a HTF": lambda e: not e.htf_aligned,
        "control concorda": lambda e: e.control_agrees,
        "VSA + pool": lambda e: e.vsa_agrees and e.at_pool,
        "VSA ou flush": lambda e: e.vsa_agrees or e.oi_flush,
        "pool + HTF": lambda e: e.at_pool and e.htf_aligned,
    }
    # Does it hold across the matrix, or is one combo carrying it?
    for axis in ("timeframe", "symbol"):
        print(f"\n  por {axis}:")
        for val in sorted({getattr(e, axis) for e in raids}):
            sel = [e for e in raids if getattr(e, axis) == val and h0 in e.mfe]
            if not sel:
                continue
            vals = [e.mfe[h0] - e.mae[h0] for e in sel]
            res = [e for e in sel if e.hit_target is not None]
            won = sum(bool(e.hit_target) for e in res)
            tgt = f"{100 * won / len(res):4.1f}%" if res else "   —"
            print(f"    {val:<10} {len(sel):>4} ev   edge "
                  f"{statistics.mean(vals):+5.2f}   alvo {tgt}")

    print()
    print(f"  {'corte':<22} {'n':>4} {'mant':>6} {'E[R]':>7} {'alvo%':>7}   edge")
    for name, pred in cuts.items():
        sel = [e for e in raids if pred(e) and h0 in e.mfe]
        if not sel:
            print(f"  {name:<22}    0")
            continue
        vals = [e.mfe[h0] - e.mae[h0] for e in sel]
        res = [e for e in sel if e.hit_target is not None]
        exp = (
            statistics.mean(e.target_r if e.hit_target else -1.0 for e in res)
            if res
            else 0.0
        )
        won = sum(bool(e.hit_target) for e in res)
        tgt = f"{100 * won / len(res):6.1f}" if res else "     —"
        keep = 100 * len(sel) / len(raids)
        print(f"  {name:<22} {len(sel):>4} {keep:5.0f}% {exp:+7.2f} {tgt}  "
              f"{statistics.mean(vals):+6.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT"])
    ap.add_argument("--timeframes", nargs="+", default=["15m", "1h", "4h"])
    ap.add_argument("--limit", type=int, default=1200)
    ap.add_argument("--horizons", nargs="+", type=int, default=[5, 10, 20, 40])
    ap.add_argument("--choch-lag", type=int, default=5)
    ap.add_argument("--choch-window", type=int, default=12,
                    help="janela, em candles, para o CHoCH confirmar depois do raid")
    ap.add_argument("--target-mode", choices=["pool", "rmult", "vwap"], default="pool")
    ap.add_argument("--target-r", type=float, default=2.0)
    ap.add_argument("--stop-mode", choices=["wick", "atr", "zone"], default="wick",
                    help="wick: no extremo do raid; atr: +N ATR além dele; "
                         "zone: além da zona inteira +N ATR")
    ap.add_argument("--stop-atr", type=float, default=1.0)
    ap.add_argument("--focus", default="raid/raid", help="grupo sobre o qual aplicar os cortes")
    ap.add_argument("--min-r-atr", type=float, default=0.25,
                    help="piso do R, em ATR — evita R minúsculo inflando os múltiplos")
    ap.add_argument("--random-reps", type=int, default=10,
                    help="sorteios aleatorios por raid - reduz o ruido do baseline")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    all_events: list[Event] = []
    for sym in args.symbols:
        for tf_raw in args.timeframes:
            tf = TimeFrame(tf_raw)
            try:
                evs = run_combo(
                    sym, tf,
                    limit=args.limit,
                    horizons=args.horizons,
                    choch_lag=args.choch_lag,
                    choch_window=args.choch_window,
                    min_r_atr=args.min_r_atr,
                    target_mode=args.target_mode,
                    target_r=args.target_r,
                    stop_mode=args.stop_mode,
                    stop_atr=args.stop_atr,
                    random_reps=args.random_reps,
                    rng=rng,
                )
            except Exception as exc:  # noqa: BLE001 - a dead symbol must not kill the sweep
                print(f"  ! {sym} {tf_raw}: {type(exc).__name__}: {exc}")
                continue
            n_raid = sum(1 for e in evs if e.kind == "raid" and e.mode == "raid")
            print(f"  {sym:<10} {tf_raw:<4} {len(evs):>4} eventos ({n_raid} raids)")
            all_events.extend(evs)

    print(f"\n[alvo={args.target_mode}"
          + (f" {args.target_r}R" if args.target_mode == "rmult" else "")
          + f" stop={args.stop_mode}"
          + (f" {args.stop_atr}atr" if args.stop_mode != "wick" else "") + "]")
    report(all_events, args.horizons, args.focus)


if __name__ == "__main__":
    main()
