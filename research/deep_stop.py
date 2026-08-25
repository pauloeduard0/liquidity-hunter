"""Is the deep stop a different setup, or the same setup measured worse?

The block-reclaim layer puts the stop at the extreme of the **visit** -- the
lowest low from the first candle that tested the block through the reclaim
candle. On a chart that stop is often only a fraction of a percent away, and a
reader looking at the same picture marks it instead under the *deepest* wick
price drove into the block, which can be several candles further back and
belong to an earlier visit entirely.

A previous round already widened the existing stop (+0.25 / +0.5 / +1.0 ATR,
and the block's far edge) and measured a monotonic degradation. That round is
not this question. It moved the stop while keeping the population that the
tight `r_atr <= 1.0` gate had already selected -- so it asked "does this trade
do better with more room", never "is there a different, well-selected
population when the stop is defined the deep way from the start".

Here each stop definition is its own setup: its own R, its own `r_atr`, its own
gate swept over the same grid, its own direction-matched control. The tight
arm is included unchanged as the incumbent.

Two horizons, because a fixed candle horizon is not neutral between them: a
wider stop puts 2R further away in price, so it needs more time to get there,
and measuring both arms at 40 candles would charge the wide one for the clock.

Run:
    poetry run python -m research.deep_stop --out /tmp/deep_stop.json
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Sequence
from pathlib import Path
from statistics import fmean, median

from liquidity_hunter.app.block_reclaim import ATR_PERIOD, detect_block_reclaims
from liquidity_hunter.app.dashboard_data import load_dashboard_data
from liquidity_hunter.core.domain import Candle, MarketDirection, TimeFrame
from liquidity_hunter.data.exceptions import DataProviderError
from liquidity_hunter.indicators import ema_series
from pydantic import ValidationError
from research._paginated import NoFuturesProvider, PaginatedFuturesProvider
from research._symbols import UNIVERSE, sample_of

#: Round-trip cost as a fraction of price: taker in, taker out.
COST_PCT = 0.0010
#: How far back a "deep" stop is allowed to look for the wick, in candles.
#: Swept rather than picked: the reader's mark on the ZEC case sat 17 candles
#: behind the trigger, which no single choice would have hit by luck.
LOOKBACKS = (10, 20, 30, 50)
#: Larger values of the detector's own `MERGE_GAP_CANDLES` (production: 3).
#: The principled version of "deep stop": the stop goes further back because
#: the *visit* is understood to be longer, not because a fixed window says so.
#: The UNIUSDT 2026-08-23 case shows the two coincide when price lingers --
#: the detector already marked the reader's exact stop there, since the visit
#: it merged ran 24 candles.
MERGE_GAPS = (10, 20, 40)
TARGETS = (1.5, 2.0, 3.0)
HORIZONS = (40, 120)


def _atr(candles: Sequence[Candle], index: int, period: int = ATR_PERIOD) -> float | None:
    if index < period:
        return None
    trs = []
    for j in range(index - period + 1, index + 1):
        prev = candles[j - 1].close
        trs.append(max(
            candles[j].high - candles[j].low,
            abs(candles[j].high - prev),
            abs(candles[j].low - prev),
        ))
    return fmean(trs) or None


def _outcome(
    candles: Sequence[Candle], i0: int, entry: float, stop: float, r: float,
    *, bull: bool, target: float, horizon: int,
) -> float:
    """kR target, 1R stop, marked to market at the horizon.

    Same convention as `research/vwap_ob_pinbar._measure`, including crediting
    the adverse side when both levels fall inside one candle.
    """
    window = candles[i0 + 1 : i0 + 1 + horizon]
    for c in window:
        if (c.low <= stop) if bull else (c.high >= stop):
            return -1.0
        if (c.high >= entry + target * r) if bull else (c.low <= entry - target * r):
            return target
    if not window:
        return 0.0
    move = window[-1].close - entry
    return (move if bull else -move) / r


def _visit_start(
    touches: Sequence[int], i0: int, gap: int
) -> int:
    """Where the visit containing (or last preceding) `i0` began, at this gap.

    `touches` are the indices that qualified as a test of this block, in order.
    Walking back from the last one at or before the trigger, any earlier touch
    within `gap` candles belongs to the same visit.
    """
    prior = [t for t in touches if t <= i0]
    if not prior:
        return i0
    start = prior[-1]
    for t in reversed(prior[:-1]):
        if start - t <= gap:
            start = t
        else:
            break
    return start


def _stops(
    candles: Sequence[Candle], i0: int, start: int, *, bull: bool,
    test_extreme: float, block_low: float, block_high: float,
    touches: Sequence[int],
) -> dict[str, float]:
    """Every stop definition for one reclaim, named."""
    out = {"extreme": test_extreme}
    for g in MERGE_GAPS:
        vs = _visit_start(touches, i0, g)
        window = candles[vs : i0 + 1]
        out[f"gap{g}"] = (
            min(c.low for c in window) if bull else max(c.high for c in window)
        )
    for k in LOOKBACKS:
        window = candles[max(0, i0 - k + 1) : i0 + 1]
        out[f"look{k}"] = (
            min(c.low for c in window) if bull else max(c.high for c in window)
        )
    out["blockedge"] = block_low if bull else block_high
    return out


def run(symbols: Sequence[str], timeframe: TimeFrame, limit: int, out_path: str) -> None:
    provider = PaginatedFuturesProvider()
    futures = NoFuturesProvider()
    rng = random.Random(7)
    rows: list[dict] = []

    for n, symbol in enumerate(symbols, 1):
        try:
            data = load_dashboard_data(
                provider=provider, symbol=symbol, timeframe=timeframe,
                limit=limit, futures_provider=futures, compute_narrative=False,
            )
        except DataProviderError as exc:
            print(f"  ! {symbol}: {exc}", flush=True)
            continue
        except ValidationError as exc:
            # Nunca nomear a causa: a mensagem "candle invalido" mascarou um
            # bug diferente (ConsolidationRange degenerado no LRCUSDT) por
            # horas. Imprime o que a excecao de fato diz.
            first = str(exc).splitlines()
            detail = first[1].strip() if len(first) > 1 else (first[0] if first else "")
            print(f"  ! {symbol} pulado: {type(exc).__name__}: {detail[:120]}", flush=True)
            continue
        candles = data.candles
        if len(candles) < 400 or data.vwap is None:
            print(f"  ! {symbol}: janela curta demais")
            continue
        idx = {c.timestamp: i for i, c in enumerate(candles)}
        reclaims = detect_block_reclaims(
            candles, data.poi_zones, data.vwap, symbol=symbol, timeframe=timeframe,
            ema=ema_series(candles, 9),
        )
        max_h = max(HORIZONS)
        kept = 0
        for rec in reclaims:
            if rec.provisional:
                continue
            i0 = idx[rec.timestamp]
            start = idx[rec.test_start_timestamp]
            if i0 + max_h >= len(candles):
                continue
            atr = _atr(candles, i0)
            if not atr:
                continue
            bull = rec.direction is MarketDirection.BULLISH
            entry = rec.reclaim_price
            touches = [
                j for j, c in enumerate(candles[: i0 + 1])
                if c.low <= rec.block_price_high and c.high >= rec.block_price_low
            ]
            for name, stop in _stops(
                candles, i0, start, bull=bull, test_extreme=rec.test_extreme,
                block_low=rec.block_price_low, block_high=rec.block_price_high,
                touches=touches,
            ).items():
                r = (entry - stop) if bull else (stop - entry)
                if r <= 0:
                    continue
                row = {
                    "symbol": symbol, "sample": sample_of(symbol),
                    "timestamp": rec.timestamp.isoformat(),
                    "stop_arm": name, "direction": rec.direction.value,
                    "r_pct": r / entry, "r_atr": r / atr,
                    "first_test": rec.first_test,
                    "vwap_candles": rec.vwap_candles,
                }
                for h in HORIZONS:
                    for t in TARGETS:
                        row[f"r{t}_h{h}"] = _outcome(
                            candles, i0, entry, stop, r,
                            bull=bull, target=t, horizon=h,
                        )
                rows.append(row)
                kept += 1
                # direction-matched control carrying THIS arm's R
                for _ in range(3):
                    ri = rng.randrange(50, len(candles) - max_h - 1)
                    e2 = candles[ri].close
                    s2 = e2 - r if bull else e2 + r
                    crow = dict(row, stop_arm=f"rand-{name}", symbol=symbol)
                    for h in HORIZONS:
                        for t in TARGETS:
                            crow[f"r{t}_h{h}"] = _outcome(
                                candles, ri, e2, s2, r,
                                bull=bull, target=t, horizon=h,
                            )
                    crow["r_pct"] = r / e2
                    rows.append(crow)
        print(f"[{n}/{len(symbols)}] {symbol:11s} {len(reclaims):5d} reclaims"
              f" -> {kept} pernas", flush=True)

    Path(out_path).write_text(json.dumps(rows))
    print(f"\ngravado {len(rows)} linhas -> {out_path}")
    report(rows)


GATES = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 99.0)


def _net(rs: Sequence[dict], key: str, target: float) -> tuple[int, float, float, float]:
    n = len(rs)
    hit = sum(1 for r in rs if r[key] >= target - 0.01) / n
    gross = fmean(r[key] for r in rs)
    cost = fmean(COST_PCT / r["r_pct"] for r in rs)
    return n, hit, gross, gross - cost


def report(rows: Sequence[dict]) -> None:
    arms = [
        "extreme",
        *[f"gap{g}" for g in MERGE_GAPS],
        *[f"look{k}" for k in LOOKBACKS],
        "blockedge",
    ]
    for sample in ("search", "holdout"):
        for h in HORIZONS:
            key = f"r2.0_h{h}"
            print(f"\n=== {sample}   alvo 2R   horizonte {h} velas   custo {COST_PCT:.2%}")
            print(f"{'stop':10s} {'gate':>6s} {'n':>6s} {'R%med':>7s} {'custo':>6s} "
                  f"{'acerto':>7s} {'bruto':>8s} {'liq':>8s} {'controle':>9s}")
            for arm in arms:
                base = [r for r in rows if r["stop_arm"] == arm and r["sample"] == sample]
                ctrl = [r for r in rows if r["stop_arm"] == f"rand-{arm}"
                        and r["sample"] == sample]
                for g in GATES:
                    sel = [r for r in base if r["r_atr"] <= g]
                    if len(sel) < 30:
                        continue
                    csel = [r for r in ctrl if r["r_atr"] <= g]
                    n, hit, gross, net = _net(sel, key, 2.0)
                    cnet = _net(csel, key, 2.0)[3] if len(csel) >= 30 else float("nan")
                    print(f"{arm:10s} {g:6.2f} {n:6d} "
                          f"{median(r['r_pct'] for r in sel):6.3%} "
                          f"{fmean(COST_PCT / r['r_pct'] for r in sel):5.2f}R "
                          f"{hit:6.1%} {gross:+8.3f} {net:+8.3f} {cnet:+9.3f}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+", default=list(UNIVERSE))
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--limit", type=int, default=60_000)
    p.add_argument("--out", default="/tmp/deep_stop.json")
    p.add_argument("--report-only", default=None)
    a = p.parse_args()
    if a.report_only:
        report(json.loads(Path(a.report_only).read_text()))
        return
    run(a.symbols, TimeFrame(a.timeframe), a.limit, a.out)


if __name__ == "__main__":
    main()
