"""Does the block reclaim need a trend to lean on?

Every geometric axis has now been measured and none separates the deep
`r_atr` bucket: approach verticality, rejection strength, prior departure,
first visit, VWAP accumulation, block age, distance to the block, block
height, penetration depth. All sit at 10-14% on the 2R hit rate.

What a reader looking at the same charts keeps naming instead is not geometry
at all -- "no impulse to continue", "the VWAP went flat", "no prospect of a
rally". One claim in three wordings: there was no trend for the reclaim to
lean on. Nothing in the study has ever carried that.

So this measures the slope of the lines the setup already draws, plus the
slower ones it does not, each normalized by local ATR so a slope is
comparable across symbols and regimes:

    vwap_slope    the session VWAP over the trailing 20 candles
    ema9_slope    the fast line the trigger route already uses
    ema50_slope   the intraday swing
    ema200_slope  ~2 days on M15, the standing regime
    above_ema200  which side of the regime line price sits on

Each is signed to the trade, so positive always means "the line agreed".
Emitted, never filtered -- the same discipline `r_atr` follows.

Run:
    poetry run python -m research.trend_context --out /tmp/trend_ctx.json
"""

from __future__ import annotations

import argparse
import json
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
HORIZONS = (40, 120)
ATR_PERIOD = 14


def _atr(candles: Sequence[Candle], i: int) -> float | None:
    if i < ATR_PERIOD:
        return None
    trs = [
        max(candles[j].high - candles[j].low,
            abs(candles[j].high - candles[j - 1].close),
            abs(candles[j].low - candles[j - 1].close))
        for j in range(i - ATR_PERIOD + 1, i + 1)
    ]
    return fmean(trs) or None


def _slope(series: Sequence[float | None], i: int, back: int, atr: float) -> float | None:
    """Change over `back` candles, in ATR, positive = rising."""
    j = i - back
    if j < 0 or series[i] is None or series[j] is None:
        return None
    return (series[i] - series[j]) / atr


def _outcome(candles, i0, entry, stop, r, *, bull, target, horizon) -> float:
    w = candles[i0 + 1 : i0 + 1 + horizon]
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
        candles = data.candles
        if len(candles) < 400 or data.vwap is None:
            continue
        idx = {c.timestamp: i for i, c in enumerate(candles)}
        vwap_line: list[float | None] = [None] * len(candles)
        for p in data.vwap.points:
            k = idx.get(p.timestamp)
            if k is not None:
                vwap_line[k] = p.value
        e9, e50, e200 = (ema_series(candles, p) for p in (9, 50, 200))
        reclaims = detect_block_reclaims(
            candles, data.poi_zones, data.vwap, symbol=symbol,
            timeframe=timeframe, ema=e9,
        )
        kept = 0
        for rec in reclaims:
            if rec.provisional:
                continue
            i0 = idx[rec.timestamp]
            if i0 + max(HORIZONS) >= len(candles):
                continue
            atr = _atr(candles, i0)
            if not atr:
                continue
            bull = rec.direction is MarketDirection.BULLISH
            sign = 1.0 if bull else -1.0
            entry, stop = rec.reclaim_price, rec.test_extreme
            r = abs(entry - stop)
            if r <= 0:
                continue
            def signed(series, back, *, _i=i0, _atr=atr, _sign=sign):
                s = _slope(series, _i, back, _atr)
                return None if s is None else s * _sign
            row = {
                "symbol": symbol, "sample": sample_of(symbol),
                "timestamp": rec.timestamp.isoformat(),
                "direction": rec.direction.value,
                "r_pct": r / entry, "r_atr": r / atr,
                "vwap_candles": rec.vwap_candles,
                "vwap_slope": signed(vwap_line, 20),
                "ema9_slope": signed(e9, 9),
                # A mesma inclinacao terminando UMA VELA ANTES da entrada.
                # `ema9_slope` inclui o proprio candle de gatilho, e um pinbar
                # de reclaim forte levanta a EMA9 sozinho -- sem esta versao
                # defasada nao da para saber se o achado e contexto ou e o
                # gatilho dito de novo.
                "ema9_slope_lag1": (
                    None if i0 - 1 < 9 or e9[i0 - 1] is None or e9[i0 - 10] is None
                    else sign * (e9[i0 - 1] - e9[i0 - 10]) / atr
                ),
                "vwap_slope_lag1": (
                    None if i0 - 1 < 20 or vwap_line[i0 - 1] is None
                    or vwap_line[i0 - 21] is None
                    else sign * (vwap_line[i0 - 1] - vwap_line[i0 - 21]) / atr
                ),
                "ema50_slope": signed(e50, 20),
                "ema200_slope": signed(e200, 50),
                "regime_side": (
                    None if e200[i0] is None
                    else bool((candles[i0].close > e200[i0]) == bull)
                ),
                "ema200_dist": (
                    None if e200[i0] is None
                    else sign * (candles[i0].close - e200[i0]) / atr
                ),
            }
            for h in HORIZONS:
                row[f"r2_h{h}"] = _outcome(
                    candles, i0, entry, stop, r, bull=bull, target=2.0, horizon=h)
            rows.append(row)
            kept += 1
        print(f"[{n}/{len(symbols)}] {symbol:11s} {kept} entradas", flush=True)
    Path(out).write_text(json.dumps(rows))
    print(f"\ngravado {len(rows)} -> {out}", flush=True)
    report(rows)


def _net(rs, key):
    if len(rs) < 40:
        return f"n={len(rs):5d} (poucos)"
    hit = sum(1 for r in rs if r[key] >= 1.99) / len(rs)
    g = fmean(r[key] for r in rs)
    c = fmean(COST_PCT / r["r_pct"] for r in rs)
    return (f"n={len(rs):5d}  acerto {hit:5.1%}  liq {g - c:+.3f}  "
            f"total {sum(x[key] - COST_PCT / x['r_pct'] for x in rs):+7.1f}R")


def report(rows: Sequence[dict]) -> None:
    for sample in ("search", "holdout"):
        S = [r for r in rows if r["sample"] == sample]
        for band, lo, hi in (("gate r_atr<=1.0", 0.0, 1.0),
                             ("profundo r_atr>=2.5", 2.5, 1e9)):
            B = [r for r in S if lo <= r["r_atr"] <= hi]
            if len(B) < 40:
                continue
            for h in HORIZONS:
                key = f"r2_h{h}"
                print(f"\n=== {sample} · {band} · h{h}")
                print(f"  {'base':32s} {_net(B, key)}")
                for f_, label in (
                    ("vwap_slope", "VWAP a favor"),
                    ("ema9_slope", "EMA9 a favor"),
                    ("ema50_slope", "EMA50 a favor"),
                    ("ema200_slope", "EMA200 a favor"),
                ):
                    ok = [r for r in B if r.get(f_) is not None and r[f_] > 0]
                    no = [r for r in B if r.get(f_) is not None and r[f_] <= 0]
                    print(f"  {label:32s} {_net(ok, key)}")
                    print(f"  {'  (contra)':32s} {_net(no, key)}")
                ok = [r for r in B if r.get("regime_side") is True]
                no = [r for r in B if r.get("regime_side") is False]
                print(f"  {'lado certo da EMA200':32s} {_net(ok, key)}")
                print(f"  {'  (lado errado)':32s} {_net(no, key)}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+", default=list(UNIVERSE))
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--limit", type=int, default=60_000)
    p.add_argument("--out", default="/tmp/trend_ctx.json")
    p.add_argument("--report-only", default=None)
    a = p.parse_args()
    if a.report_only:
        report(json.loads(Path(a.report_only).read_text()))
        return
    run(a.symbols, TimeFrame(a.timeframe), a.limit, a.out)


if __name__ == "__main__":
    main()
