"""Exhaustion Flush — measurement harness.

Measures the pattern the control oscillator's *hollow* bars draw: a stretch of
candles where price moves on **positions closing** rather than on fresh money.

    long_liquidation  (sell aggression + OI falling) → longs being burned
                                                     → thesis: reversal UP
    short_covering    (buy aggression + OI falling)  → shorts being squeezed out
                                                     → thesis: reversal DOWN

The thesis under test is "the fuel runs out": a fall carried only by longs
liquidating lasts exactly as long as there are longs left to liquidate, and
nobody new is short to hold price down afterwards. Whether that is *tradable*
— rather than merely true — is what this script asks.

Lives outside the ``liquidity_hunter`` package on purpose: the package is a
research platform and carries no entry logic. This only consumes its output,
and reuses ``raid_reversal``'s point-in-time target machinery.

Confirmation modes
------------------
``flush``    entry at the close of the candle *after* the cluster ends (the
             first bar at which the cluster is known to be over).
``confirm``  wait up to ``--confirm-window`` candles for the opposite
             **solid** bar — a buildup quadrant on the reversal side, i.e.
             fresh money actually arriving — and enter at that close. This is
             the sequence the chart suggested: burn, then arrival.

Lookahead discipline
--------------------
Every regime reading is computed from a trailing window ending at its own
candle, so it is known at that candle's close; both modes enter at or after
the last bar they read. Targets/pools are recomputed here point-in-time,
never read from a stored ``is_mitigated`` flag.

The control
-----------
A directional pattern must be measured against a *direction-matched* random
entry — the lesson from ``raid_reversal``, where a +0.94R "edge" became
−0.06R once the control stopped being a coin flip. ``random/rev`` mirrors the
flush events' direction mix, ``random/confirm`` the confirmed subset's.

Usage
-----
    poetry run python research/exhaustion_flush.py
    poetry run python research/exhaustion_flush.py --symbols BTCUSDT ETHUSDT \
        --timeframes 15m 1h --min-len 3 --horizons 5 10 20 40
"""

from __future__ import annotations

import argparse
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

from liquidity_hunter.app import load_dashboard_data
from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.core.domain.enums import MarketDirection, StructureEvent
from raid_reversal import (  # noqa: E402 — sibling research script, same folder
    BEAR,
    BULL,
    _equal_level_targets,
    _index_at_or_after,
    _pierced_before,
    next_opposing_target,
)

# The two "hollow bar" quadrants and the reversal each argues for.
_EXIT_REGIME = {"long_liquidation": BULL, "short_covering": BEAR}
# The "solid bar" that confirms fresh money arriving on a given side.
_BUILDUP_REGIME = {BULL: "long_buildup", BEAR: "short_buildup"}

# VSA patterns that argue for a reversal, by the direction they argue *for*.
_VSA_REVERSAL = {
    BULL: {"selling_climax", "down_thrust", "no_supply"},
    BEAR: {"buying_climax", "up_thrust", "no_demand"},
}


@dataclass
class Event:
    kind: str  # "flush" | "random"
    mode: str  # "flush" | "confirm" | "rev"
    symbol: str
    timeframe: str
    direction: MarketDirection
    entry_index: int
    entry: float
    stop: float
    target: float | None
    r: float = 0.0
    mfe: dict[int, float] = field(default_factory=dict)
    mae: dict[int, float] = field(default_factory=dict)
    hit_target: bool | None = None
    target_r: float = 0.0
    # --- selection features, all point-in-time -----------------------------
    cluster_len: int = 0  # hollow bars in a row
    at_extreme: bool = False  # the cluster's extreme is the window's extreme
    confirmed: bool = False  # a solid buildup bar followed, within the window
    vsa_agrees: bool = False  # same-side VSA reversal pattern at the extreme
    oi_flush: bool = False  # an OI-qualified FLUSH inside the cluster
    at_pool: bool = False  # the extreme reached a still-virgin equal-level pool
    at_ob: bool = False  # the extreme landed in a still-unbroken order block
    choch_after: bool = False  # a same-direction CHoCH followed, within the window
    htf_aligned: bool = False
    depth_atr: float = 0.0  # how far the cluster travelled, in ATR


@dataclass(frozen=True)
class Cluster:
    """A run of consecutive hollow bars of one exit quadrant."""

    start: int
    end: int  # inclusive
    direction: MarketDirection  # the reversal it argues for
    extreme: float  # the low (BULL) / high (BEAR) reached over the run


def find_clusters(
    candles: Sequence[Candle],
    regime_at: dict[int, str],
    min_len: int,
) -> list[Cluster]:
    """Every run of ``min_len``+ consecutive exit-quadrant candles."""
    out: list[Cluster] = []
    i = 0
    while i < len(candles):
        reg = regime_at.get(i)
        d = _EXIT_REGIME.get(reg or "")
        if d is None:
            i += 1
            continue
        j = i
        while j + 1 < len(candles) and regime_at.get(j + 1) == reg:
            j += 1
        if j - i + 1 >= min_len:
            span = candles[i : j + 1]
            extreme = min(c.low for c in span) if d is BULL else max(c.high for c in span)
            out.append(Cluster(start=i, end=j, direction=d, extreme=extreme))
        i = j + 1
    return out


def measure(ev: Event, candles: Sequence[Candle], horizons: Sequence[int]) -> Event:
    """Forward MFE/MAE in R, and whether the target resolved before the stop."""
    r = abs(ev.entry - ev.stop)
    if r <= 0:
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

    if ev.target is not None:
        ev.target_r = abs(ev.target - ev.entry) / r
        for c in candles[i0 + 1 :]:
            stopped = c.high >= ev.stop if ev.direction is BEAR else c.low <= ev.stop
            reached = c.low <= ev.target if ev.direction is BEAR else c.high >= ev.target
            if stopped:  # an ambiguous bar counts against the thesis
                ev.hit_target = False
                break
            if reached:
                ev.hit_target = True
                break
    return ev


def run_combo(
    symbol: str,
    timeframe: TimeFrame,
    *,
    limit: int,
    horizons: Sequence[int],
    min_len: int,
    confirm_window: int,
    choch_window: int,
    choch_lag: int,
    extreme_lookback: int,
    target_mode: str,
    target_r: float,
    stop_atr: float,
    min_r_atr: float,
    random_reps: int,
    rng: random.Random,
) -> tuple[list[Event], int]:
    data = load_dashboard_data(symbol=symbol, timeframe=timeframe, limit=limit)
    candles = data.candles
    if len(candles) < 60 or not data.market_control:
        return [], 0

    idx_of = {c.timestamp: i for i, c in enumerate(candles)}
    htf = data.higher_timeframe_direction
    targets = _equal_level_targets(data.liquidity_zones)

    regime_at: dict[int, str] = {}
    for p in data.market_control.series:
        i = idx_of.get(p.timestamp)
        if i is not None:
            regime_at[i] = p.regime.value
    covered = len(regime_at)

    vsa_at: dict[int, set[str]] = {}
    for s in data.volume_spread_signals or []:
        i = idx_of.get(s.timestamp)
        if i is not None:
            vsa_at.setdefault(i, set()).add(s.pattern.value)

    # Non-provisional CHoCH per candle: the structural confirmation the chart
    # showed after the flush. Entering on it needs pivots that only form later,
    # so the `choch` mode enters `choch_lag` candles after the event — an
    # approximation that flatters the mode; treat its numbers as optimistic.
    choch_at: dict[int, MarketDirection] = {}
    for e in data.internal_structure_events:
        if e.provisional or e.event is not StructureEvent.CHANGE_OF_CHARACTER:
            continue
        i = idx_of.get(e.timestamp)
        if i is not None:
            choch_at[i] = e.direction

    flush_at: set[int] = set()
    if data.oi_analysis:
        for qe in data.oi_analysis.qualified_events:
            if qe.participation.value == "flush":
                i = idx_of.get(qe.event_timestamp)
                if i is not None:
                    flush_at.add(i)

    trs = [
        max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
        for p, c in zip(candles, candles[1:], strict=False)
    ]
    atr = statistics.mean(trs) if trs else 0.0
    min_r = min_r_atr * atr
    max_h = max(horizons)
    events: list[Event] = []

    def resolve_target(ei: int, d: MarketDirection, entry: float, stop: float) -> float | None:
        if target_mode == "rmult":
            return (
                entry - target_r * abs(entry - stop)
                if d is BEAR
                else entry + target_r * abs(entry - stop)
            )
        return next_opposing_target(candles, targets, ei, d)

    def at_order_block(cl: Cluster) -> bool:
        """Did the cluster's extreme land in a still-unbroken order block?

        The zone's own ``status``/``invalidated_at`` are computed over the whole
        series (hindsight), so freshness is recomputed here: the block must have
        been created before the cluster and no candle may have *closed* beyond
        its far edge in between — the detector's own break rule.
        """
        for z in data.poi_zones:
            if z.direction is not cl.direction:
                continue
            created = _index_at_or_after(candles, z.created_at)
            if created >= cl.start:
                continue
            broken = any(
                (c.close < z.price_low) if cl.direction is BULL else (c.close > z.price_high)
                for c in candles[created + 1 : cl.start]
            )
            if broken:
                continue
            if z.price_low - 0.25 * atr <= cl.extreme <= z.price_high + 0.25 * atr:
                return True
        return False

    def at_virgin_pool(cl: Cluster) -> bool:
        for t in targets:
            if t.side is not cl.direction:
                continue
            formed = _index_at_or_after(candles, t.formed_at)
            if formed >= cl.start:
                continue
            if _pierced_before(candles, t, formed + 1, cl.start):
                continue  # already consumed before the cluster
            if abs(cl.extreme - t.far) <= 0.5 * atr:
                return True
        return False

    def build(mode: str, cl: Cluster, ei: int) -> Event | None:
        if ei + max_h >= len(candles) or ei >= len(candles):
            return None
        d = cl.direction
        stop = (
            cl.extreme - stop_atr * atr if d is BULL else cl.extreme + stop_atr * atr
        )
        entry = candles[ei].close
        if (d is BEAR and stop <= entry) or (d is BULL and stop >= entry):
            return None  # price already ran past the cluster extreme
        if abs(entry - stop) < min_r:
            return None
        # Is the cluster's extreme also the extreme of its recent past? The
        # visual read was "the hollow bars printed *at the low*", which is a
        # different claim from "hollow bars appeared".
        back = candles[max(0, cl.start - extreme_lookback) : cl.end + 1]
        at_ext = (
            cl.extreme <= min(c.low for c in back)
            if d is BULL
            else cl.extreme >= max(c.high for c in back)
        )
        confirm_idx = next(
            (
                k
                for k in range(cl.end + 1, min(cl.end + 1 + confirm_window, len(candles)))
                if regime_at.get(k) == _BUILDUP_REGIME[d]
            ),
            None,
        )
        ext_idx = min(
            range(cl.start, cl.end + 1),
            key=lambda k: candles[k].low if d is BULL else -candles[k].high,
        )
        return Event(
            kind="flush",
            mode=mode,
            symbol=symbol,
            timeframe=timeframe.value,
            direction=d,
            entry_index=ei,
            entry=entry,
            stop=stop,
            target=resolve_target(ei, d, entry, stop),
            cluster_len=cl.end - cl.start + 1,
            at_extreme=at_ext,
            confirmed=confirm_idx is not None,
            vsa_agrees=bool(
                _VSA_REVERSAL[d] & (vsa_at.get(ext_idx, set()) | vsa_at.get(ext_idx + 1, set()))
            ),
            oi_flush=any(k in flush_at for k in range(cl.start, cl.end + 1)),
            at_pool=at_virgin_pool(cl),
            at_ob=at_order_block(cl),
            choch_after=any(
                choch_at.get(k) is d
                for k in range(cl.end + 1, min(cl.end + 1 + choch_window, len(candles)))
            ),
            htf_aligned=(d is htf),
            depth_atr=abs(candles[cl.start].open - cl.extreme) / atr if atr else 0.0,
        )

    clusters = find_clusters(candles, regime_at, min_len)
    for cl in clusters:
        if (ev := build("flush", cl, cl.end + 1)) is not None:
            events.append(ev)
        # the "solid bar arrives" sequence
        confirm_idx = next(
            (
                k
                for k in range(cl.end + 1, min(cl.end + 1 + confirm_window, len(candles)))
                if regime_at.get(k) == _BUILDUP_REGIME[cl.direction]
            ),
            None,
        )
        if confirm_idx is not None and (ev := build("confirm", cl, confirm_idx)) is not None:
            events.append(ev)
        # structural confirmation: enter on the CHoCH the reversal printed
        choch_idx = next(
            (
                k
                for k in range(cl.end + 1, min(cl.end + 1 + choch_window, len(candles)))
                if choch_at.get(k) is cl.direction
            ),
            None,
        )
        if choch_idx is not None and (
            ev := build("choch", cl, choch_idx + choch_lag)
        ) is not None:
            events.append(ev)

    # --- direction-matched random control ---------------------------------
    for cl in [c for c in clusters for _ in range(random_reps)]:
        j = rng.randrange(20, max(21, len(candles) - max_h - 1))
        if j + max_h >= len(candles):
            continue
        # same risk the cluster would have given, from an arbitrary candle
        risk = abs(candles[cl.end + 1].close - cl.extreme) + stop_atr * atr if cl.end + 1 < len(
            candles
        ) else 0.0
        if risk < min_r:
            continue
        entry = candles[j].close
        stop = entry - risk if cl.direction is BULL else entry + risk
        events.append(
            Event(
                kind="random",
                mode="rev",
                symbol=symbol,
                timeframe=timeframe.value,
                direction=cl.direction,
                entry_index=j,
                entry=entry,
                stop=stop,
                target=resolve_target(j, cl.direction, entry, stop),
                htf_aligned=(cl.direction is htf),
            )
        )

    return [measure(e, candles, horizons) for e in events], covered


def _se(vals: Sequence[float]) -> float:
    """Standard error of the mean — the width of "is this distinguishable?"."""
    if len(vals) < 2:
        return float("inf")
    return statistics.stdev(vals) / (len(vals) ** 0.5)


def report(events: Sequence[Event], horizons: Sequence[int]) -> None:
    groups: dict[str, list[Event]] = {}
    for e in events:
        if e.r <= 0:
            continue
        groups.setdefault(f"{e.kind}/{e.mode}", []).append(e)

    hdr = "  ".join(f"MFE{h:<3} MAE{h:<3}" for h in horizons)
    print(f"\n{'grupo':<18} {'n':>4}  {hdr}   alvo%   dist    E[R]  edge")
    print("-" * (32 + len(hdr) + 14))

    def fmt(vals: list[float]) -> str:
        return f"{statistics.mean(vals):5.2f}" if vals else "    —"

    h0 = horizons[len(horizons) // 2]
    for key in ["flush/flush", "flush/confirm", "flush/choch", "random/rev"]:
        evs = groups.get(key)
        if not evs:
            continue
        cells = []
        for h in horizons:
            cells.append(fmt([e.mfe[h] for e in evs if h in e.mfe]))
            cells.append(fmt([e.mae[h] for e in evs if h in e.mae]))
        res = [e for e in evs if e.hit_target is not None]
        won = sum(bool(e.hit_target) for e in res)
        tgt = f"{100 * won / len(res):5.1f}" if res else "    —"
        if res:
            outcomes = [e.target_r if e.hit_target else -1.0 for e in res]
            exp = statistics.mean(outcomes)
            rr = statistics.mean(e.target_r for e in res)
            extra = f" {rr:5.2f}R {exp:+5.2f}±{_se(outcomes):.2f}"
        else:
            extra = "     —     —"
        edge = [e.mfe[h0] - e.mae[h0] for e in evs if h0 in e.mfe and h0 in e.mae]
        print(
            f"{key:<18} {len(evs):>4}  " + "  ".join(cells)
            + f"   {tgt}{extra}  " + (f"{statistics.mean(edge):+5.2f}" if edge else "    —")
        )

    def pred_ctl(name: str, e: Event | None) -> bool:
        """Whether a cut is also computable on the random control, and its value.

        Called with ``None`` to ask "is this cut controllable?"; only the cuts
        that describe the *entry's context* rather than the flush itself are.
        """
        if name == "alinhado com HTF":
            return True if e is None else e.htf_aligned
        if name == "contra a HTF":
            return True if e is None else not e.htf_aligned
        if name == "todos":
            return True if e is None else True
        return False

    base = [e for e in events if e.kind == "random" and e.r > 0 and h0 in e.mfe]
    base_edge = statistics.mean([e.mfe[h0] - e.mae[h0] for e in base]) if base else 0.0
    base_res = [e for e in base if e.hit_target is not None]
    base_exp = (
        statistics.mean(e.target_r if e.hit_target else -1.0 for e in base_res)
        if base_res
        else 0.0
    )

    for focus in ("flush", "confirm", "choch"):
        sel_all = [e for e in events if e.kind == "flush" and e.mode == focus and e.r > 0]
        if not sel_all:
            continue
        print(f"\ncortes sobre flush/{focus} (MFE−MAE em {h0} candles; "
              f"controle pareado: edge {base_edge:+.2f}, E[R] {base_exp:+.2f}):")
        cuts = {
            "todos": lambda e: True,
            "no extremo": lambda e: e.at_extreme,
            "fora do extremo": lambda e: not e.at_extreme,
            # LOOKAHEAD on the `flush` mode: whether a solid bar shows up
            # later is not known at the cluster's end. Kept only to show how
            # much of the apparent edge is hindsight -- the honest version of
            # this cut is the separate `flush/confirm` mode, which *enters* on
            # that bar.
            "solida depois (HINDSIGHT)": lambda e: e.confirmed,
            "sem solida (HINDSIGHT)": lambda e: not e.confirmed,
            "cluster >= 5": lambda e: e.cluster_len >= 5,
            "cluster 3-4": lambda e: e.cluster_len < 5,
            "OI flush": lambda e: e.oi_flush,
            "VSA concorda": lambda e: e.vsa_agrees,
            "extremo em pool virgem": lambda e: e.at_pool,
            "extremo em OB": lambda e: e.at_ob,
            "fora de OB": lambda e: not e.at_ob,
            "CHoCH depois (HINDSIGHT)": lambda e: e.choch_after,
            "OB + pool": lambda e: e.at_ob and e.at_pool,
            "OB + extremo": lambda e: e.at_ob and e.at_extreme,
            "OB + CHoCH (HINDSIGHT)": lambda e: e.at_ob and e.choch_after,
            "OB + pool + CHoCH (HIND.)": lambda e: e.at_ob and e.at_pool and e.choch_after,
            "profundidade > 2 ATR": lambda e: e.depth_atr > 2.0,
            "alinhado com HTF": lambda e: e.htf_aligned,
            "contra a HTF": lambda e: not e.htf_aligned,
            "extremo + solida (HINDSIGHT)": lambda e: e.at_extreme and e.confirmed,
            "extremo + pool": lambda e: e.at_extreme and e.at_pool,
        }
        print(f"  {'corte':<24} {'n':>4} {'mant':>6} {'E[R]':>7} {'alvo%':>7}   edge")
        for name, pred in cuts.items():
            sel = [e for e in sel_all if pred(e) and h0 in e.mfe]
            if not sel:
                print(f"  {name:<24}    0")
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
            keep = 100 * len(sel) / len(sel_all)
            se = _se([e.target_r if e.hit_target else -1.0 for e in res])
            # The control, restricted to the same cut where the cut is a
            # property a random entry also has (direction/HTF). Without this a
            # cut that merely re-discovers "HTF alignment pays" reads as an
            # edge of the pattern.
            ctl = [e for e in base_res if pred_ctl(name, e)] if pred_ctl(name, None) else []
            ctl_txt = (
                f"  ctl {statistics.mean([e.target_r if e.hit_target else -1.0 for e in ctl]):+.2f}"
                if ctl
                else ""
            )
            print(
                f"  {name:<24} {len(sel):>4} {keep:5.0f}% {exp:+7.2f}±{se:.2f} {tgt}  "
                f"{statistics.mean(vals):+6.2f}{ctl_txt}"
            )

    # Does any single combo carry the result?
    sel_all = [e for e in events if e.kind == "flush" and e.mode == "flush" and e.r > 0]
    for axis in ("timeframe", "symbol"):
        print(f"\n  flush/flush por {axis}:")
        for val in sorted({getattr(e, axis) for e in sel_all}):
            sel = [e for e in sel_all if getattr(e, axis) == val and h0 in e.mfe]
            ctl = [e for e in base if getattr(e, axis) == val]
            if not sel:
                continue
            vals = [e.mfe[h0] - e.mae[h0] for e in sel]
            cvals = [e.mfe[h0] - e.mae[h0] for e in ctl]
            res = [e for e in sel if e.hit_target is not None]
            won = sum(bool(e.hit_target) for e in res)
            tgt = f"{100 * won / len(res):4.1f}%" if res else "   —"
            ctl_s = f"{statistics.mean(cvals):+5.2f}" if cvals else "   —"
            print(
                f"    {val:<10} {len(sel):>4} ev   edge {statistics.mean(vals):+5.2f}"
                f"   (controle {ctl_s})   alvo {tgt}"
            )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--symbols", nargs="+",
        default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "AVAXUSDT", "LINKUSDT"],
    )
    ap.add_argument("--timeframes", nargs="+", default=["15m", "30m", "1h"])
    ap.add_argument("--limit", type=int, default=1200)
    ap.add_argument("--horizons", nargs="+", type=int, default=[5, 10, 20, 40])
    ap.add_argument("--min-len", type=int, default=3, help="barras ocas consecutivas minimas")
    ap.add_argument("--confirm-window", type=int, default=5)
    ap.add_argument("--choch-window", type=int, default=15,
                    help="janela, em candles, para o CHoCH confirmar depois do cluster")
    ap.add_argument("--choch-lag", type=int, default=3,
                    help="candles apos o CHoCH para entrar (os pivos so formam depois)")
    ap.add_argument("--extreme-lookback", type=int, default=20)
    ap.add_argument("--target-mode", choices=["pool", "rmult"], default="pool")
    ap.add_argument("--target-r", type=float, default=2.0)
    ap.add_argument("--stop-atr", type=float, default=0.5, help="folga alem do extremo do cluster")
    ap.add_argument("--min-r-atr", type=float, default=0.25)
    ap.add_argument("--random-reps", type=int, default=10)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    all_events: list[Event] = []
    for sym in args.symbols:
        for tf_raw in args.timeframes:
            tf = TimeFrame(tf_raw)
            try:
                evs, covered = run_combo(
                    sym, tf,
                    limit=args.limit,
                    horizons=args.horizons,
                    min_len=args.min_len,
                    confirm_window=args.confirm_window,
                    choch_window=args.choch_window,
                    choch_lag=args.choch_lag,
                    extreme_lookback=args.extreme_lookback,
                    target_mode=args.target_mode,
                    target_r=args.target_r,
                    stop_atr=args.stop_atr,
                    min_r_atr=args.min_r_atr,
                    random_reps=args.random_reps,
                    rng=rng,
                )
            except Exception as exc:  # noqa: BLE001 — a dead symbol must not kill the sweep
                print(f"  ! {sym} {tf_raw}: {type(exc).__name__}: {exc}")
                continue
            n = sum(1 for e in evs if e.kind == "flush" and e.mode == "flush")
            nc = sum(1 for e in evs if e.mode == "confirm")
            nch = sum(1 for e in evs if e.mode == "choch")
            print(
                f"  {sym:<10} {tf_raw:<4} {n:>3} clusters ({nc} solida, {nch} choch)"
                f"  [OI cobre {covered} candles]"
            )
            all_events.extend(evs)

    print(
        f"\n[min-len={args.min_len} alvo={args.target_mode}"
        + (f" {args.target_r}R" if args.target_mode == "rmult" else "")
        + f" stop=extremo+{args.stop_atr}atr]"
    )
    report(all_events, args.horizons)


if __name__ == "__main__":
    main()
