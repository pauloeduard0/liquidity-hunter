"""Does the pinbar's own colour belong in the trigger?

`pinbar_grades` measures the body as `abs(close - open)` and never asks which
way the candle closed, so a **red** candle satisfies the *bullish* `l2`. A
reader spotted it on a chart (UNIUSDT M15, 2026-07-16 18:45 UTC-3); reading the
code confirms it. The docstring says `l2` is "a candle that closed most of the
way through its own range" -- upward, for a bullish read -- so intent and code
disagree.

That is a defect in the description, not yet a defect in the edge: the union of
the three grades was validated out-of-sample **with** this behaviour inside it,
so some of that edge may be coming from the wrong-coloured candles. Three arms
on one collection, differing in exactly one thing:

    off   the shipped trigger
    all   every grade must close the trade's way
    l2    only `l2` must -- `legacy` and `l1` cap the body at 35% and 15% of
          the range, where a red candle with a long tail beneath it is an
          ordinary hammer and the tail is the reading

Each arm carries its own direction-matched control, because the arms trigger on
different candles and therefore have different R.

Run:
    poetry run python -m research.pinbar_color --out /tmp/pinbar_color.json
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Sequence
from pathlib import Path
from statistics import fmean

from liquidity_hunter.app.block_reclaim import detect_block_reclaims
from liquidity_hunter.app.dashboard_data import load_dashboard_data
from liquidity_hunter.core.domain import Candle, MarketDirection, TimeFrame
from liquidity_hunter.data.exceptions import DataProviderError
from liquidity_hunter.indicators import ema_series
from pydantic import ValidationError
from research._paginated import NoFuturesProvider, PaginatedFuturesProvider
from research._symbols import UNIVERSE, sample_of

COST_PCT = 0.0010
HORIZON = 40
ARMS: tuple[tuple[str, str | None], ...] = (("off", None), ("all", "all"), ("l2", "l2"))


def _outcome(candles, i0, entry, stop, r, *, bull, target=2.0) -> float:
    w = candles[i0 + 1 : i0 + 1 + HORIZON]
    for c in w:
        if (c.low <= stop) if bull else (c.high >= stop):
            return -1.0
        if (c.high >= entry + target * r) if bull else (c.low <= entry - target * r):
            return target
    if not w:
        return 0.0
    move = w[-1].close - entry
    return (move if bull else -move) / r


def run(symbols: Sequence[str], timeframe: TimeFrame, limit: int, out: str) -> None:
    provider, futures = PaginatedFuturesProvider(), NoFuturesProvider()
    rng = random.Random(7)
    rows: list[dict] = []
    for n, symbol in enumerate(symbols, 1):
        try:
            data = load_dashboard_data(
                provider=provider, symbol=symbol, timeframe=timeframe, limit=limit,
                futures_provider=futures, compute_narrative=False,
            )
        except (DataProviderError, ValidationError) as exc:
# Nunca nomear a causa: a mensagem "candle invalido" mascarou um
            # bug diferente (ConsolidationRange degenerado no LRCUSDT) por
            # horas. Imprime o que a excecao de fato diz.
            first = str(exc).splitlines()
            detail = first[1].strip() if len(first) > 1 else (first[0] if first else "")
            print(f"  ! {symbol} pulado: {type(exc).__name__}: {detail[:120]}", flush=True)
            continue
        candles: list[Candle] = data.candles
        if len(candles) < 400 or data.vwap is None:
            continue
        idx = {c.timestamp: i for i, c in enumerate(candles)}
        ema = ema_series(candles, 9)
        counts = {}
        for arm, scope in ARMS:
            recs = detect_block_reclaims(
                candles, data.poi_zones, data.vwap, symbol=symbol,
                timeframe=timeframe, ema=ema, require_pinbar_color=scope,
            )
            kept = 0
            for rec in recs:
                if rec.provisional or rec.r_atr is None:
                    continue
                i0 = idx[rec.timestamp]
                if i0 + HORIZON >= len(candles):
                    continue
                bull = rec.direction is MarketDirection.BULLISH
                entry, stop = rec.reclaim_price, rec.test_extreme
                r = abs(entry - stop)
                if r <= 0:
                    continue
                row = {
                    "symbol": symbol, "sample": sample_of(symbol), "arm": arm,
                    "timestamp": rec.timestamp.isoformat(),
                    "direction": rec.direction.value,
                    "r_pct": r / entry, "r_atr": rec.r_atr,
                    "grade": rec.pinbar_grade, "line": rec.trigger_line,
                    "vwap_candles": rec.vwap_candles,
                    "r2": _outcome(candles, i0, entry, stop, r, bull=bull),
                }
                rows.append(row)
                kept += 1
                for _ in range(3):
                    ri = rng.randrange(50, len(candles) - HORIZON - 1)
                    e2 = candles[ri].close
                    s2 = e2 - r if bull else e2 + r
                    rows.append(dict(
                        row, arm=f"rand-{arm}", r_pct=r / e2,
                        r2=_outcome(candles, ri, e2, s2, r, bull=bull),
                    ))
            counts[arm] = kept
        print(f"[{n}/{len(symbols)}] {symbol:11s} "
              + "  ".join(f"{a}={counts.get(a, 0)}" for a, _ in ARMS), flush=True)
    Path(out).write_text(json.dumps(rows))
    print(f"\ngravado {len(rows)} -> {out}", flush=True)
    report(rows)


def _net(rs):
    if len(rs) < 30:
        return f"n={len(rs):5d} (poucos)"
    hit = sum(1 for r in rs if r["r2"] >= 1.99) / len(rs)
    g = fmean(r["r2"] for r in rs)
    c = fmean(COST_PCT / r["r_pct"] for r in rs)
    tot = sum(r["r2"] - COST_PCT / r["r_pct"] for r in rs)
    return (f"n={len(rs):5d}  acerto {hit:5.1%}  liq {g - c:+.3f}  total {tot:+8.1f}R")


def report(rows: Sequence[dict]) -> None:
    for sample in ("search", "holdout"):
        S = [r for r in rows if r["sample"] == sample]
        for band, lo, hi in (("gate r_atr<=1.0", 0.0, 1.0), ("tudo", 0.0, 1e9)):
            print(f"\n=== {sample} · {band} · alvo 2R · h{HORIZON}")
            for arm, _ in ARMS:
                sel = [r for r in S if r["arm"] == arm and lo <= r["r_atr"] <= hi]
                ctl = [r for r in S if r["arm"] == f"rand-{arm}" and lo <= r["r_atr"] <= hi]
                print(f"  cor={arm:4s}         {_net(sel)}")
                print(f"  {'  controle':14s} {_net(ctl)}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+", default=list(UNIVERSE))
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--limit", type=int, default=60_000)
    p.add_argument("--out", default="/tmp/pinbar_color.json")
    p.add_argument("--report-only", default=None)
    a = p.parse_args()
    if a.report_only:
        report(json.loads(Path(a.report_only).read_text()))
        return
    run(a.symbols, TimeFrame(a.timeframe), a.limit, a.out)


if __name__ == "__main__":
    main()
