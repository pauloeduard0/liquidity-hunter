"""O que conta como um *teste* do bloco?

Metade do caso AVAXUSDT (2026-08-17 21:00 UTC-3, ver `docs/block_reclaim.md`)
ja virou regra: o gatilho disparava na vela em que a VWAP reancora. A outra
metade nao foi medida. Naquele trade o "teste" do bloco foi uma unica vela cujo
pavio entrou 20% da altura do bloco e saiu; nenhuma vela fechou la dentro. Um
leitor olhando o grafico disse "nem tinha chegado no OB ainda", e o detector
achava que tinha.

Hoje o criterio e sobreposicao de faixa: qualquer vela cujo range toca o bloco
conta. Este script emite, por reclaim, o que distingue um toque de uma visita:

    pen_frac      quanto o extremo do teste entrou, como fracao da altura
    pen_atr       o mesmo, em ATR local (a altura do bloco varia muito)
    closed_in     alguma vela FECHOU dentro do bloco durante a visita
    closes_in     quantas
    visit_candles velas entre o inicio do teste e o gatilho
    touch_candles quantas tocaram o bloco na visita
    block_atr     altura do bloco em ATR

Emitido, nunca filtrado -- a mesma disciplina de `r_atr` e de `trend_context`.

Previsoes registradas antes da rodada: `pen_frac`/`pen_atr` e `visit_candles`
NAO devem separar (todo eixo geometrico ja medido falhou); `closed_in` pode,
por ser categorico -- "o preco foi la e ficou" contra "um pavio encostou".

Run:
    poetry run python -m research.block_test_quality --out /tmp/blocktest.json
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
HORIZON = 40
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
    rows: list[dict] = []
    for n, symbol in enumerate(symbols, 1):
        try:
            data = load_dashboard_data(
                provider=provider, symbol=symbol, timeframe=timeframe, limit=limit,
                futures_provider=futures, compute_narrative=False,
            )
        except (DataProviderError, ValidationError) as exc:
            first = str(exc).splitlines()
            detail = first[1].strip() if len(first) > 1 else (first[0] if first else "")
            print(f"  ! {symbol} pulado: {type(exc).__name__}: {detail[:120]}", flush=True)
            continue
        candles = data.candles
        if len(candles) < 400 or data.vwap is None:
            continue
        idx = {c.timestamp: i for i, c in enumerate(candles)}
        reclaims = detect_block_reclaims(
            candles, data.poi_zones, data.vwap, symbol=symbol,
            timeframe=timeframe, ema=ema_series(candles, 9),
        )
        kept = 0
        for rec in reclaims:
            if rec.provisional:
                continue
            i0 = idx[rec.timestamp]
            if i0 + HORIZON >= len(candles):
                continue
            atr = _atr(candles, i0)
            if not atr:
                continue
            bull = rec.direction is MarketDirection.BULLISH
            entry, stop = rec.reclaim_price, rec.test_extreme
            r = abs(entry - stop)
            if r <= 0:
                continue
            lo, hi = rec.block_price_low, rec.block_price_high
            height = hi - lo
            if height <= 0:
                continue
            # Um teste bullish desce ate o bloco por cima (borda proxima = hi);
            # um bearish sobe ate ele por baixo (borda proxima = lo).
            depth = (hi - rec.test_extreme) if bull else (rec.test_extreme - lo)
            start = idx.get(rec.test_start_timestamp, i0)
            visit = candles[start : i0 + 1]
            closes_in = sum(1 for c in visit if lo <= c.close <= hi)
            touches = sum(1 for c in visit if c.low <= hi and c.high >= lo)
            rows.append({
                "symbol": symbol, "sample": sample_of(symbol),
                "timestamp": rec.timestamp.isoformat(),
                "direction": rec.direction.value,
                "r_pct": r / entry, "r_atr": r / atr,
                "vwap_candles": rec.vwap_candles,
                "first_test": rec.first_test,
                "pen_frac": depth / height,
                "pen_atr": depth / atr,
                "block_atr": height / atr,
                "closed_in": closes_in > 0,
                "closes_in": closes_in,
                "visit_candles": len(visit),
                "touch_candles": touches,
                "r2_h40": _outcome(candles, i0, entry, stop, r, bull=bull),
            })
            kept += 1
        print(f"[{n}/{len(symbols)}] {symbol:11s} {kept} entradas", flush=True)
    Path(out).write_text(json.dumps(rows))
    print(f"\ngravado {len(rows)} -> {out}", flush=True)
    report(rows)


def _net(rs) -> str:
    if len(rs) < 40:
        return f"n={len(rs):5d} (poucos)"
    hit = sum(1 for r in rs if r["r2_h40"] >= 1.99) / len(rs)
    net = fmean(r["r2_h40"] - COST_PCT / r["r_pct"] for r in rs)
    return f"n={len(rs):5d}  acerto {hit:5.1%}  liq {net:+.3f}"


def report(rows: Sequence[dict]) -> None:
    """No gate de producao (r_atr<=1.0 e vwap_candles>=4), por eixo."""
    for sample in ("search", "holdout"):
        B = [r for r in rows if r["sample"] == sample
             and r["r_atr"] <= 1.0 and r["vwap_candles"] >= 4]
        if len(B) < 40:
            continue
        print(f"\n=== {sample} · gate r_atr<=1.0 · vwap>=4 · alvo 2R")
        print(f"  {'base':34s} {_net(B)}")
        print(f"  {'fechou dentro do bloco':34s} {_net([r for r in B if r['closed_in']])}")
        print(f"  {'  so pavio (nunca fechou)':34s} {_net([r for r in B if not r['closed_in']])}")
        for field, cuts in (
            ("pen_frac", (0.1, 0.25, 0.5, 1.0)),
            ("pen_atr", (0.25, 0.5, 1.0)),
            ("visit_candles", (2, 5, 10)),
            ("touch_candles", (1, 2, 5)),
            ("block_atr", (1.0, 2.0, 4.0)),
        ):
            lo = float("-inf")
            for hi in (*cuts, float("inf")):
                rs = [r for r in B if lo < r[field] <= hi]
                lbl = f"{field} {lo:g}-{hi:g}".replace("-inf", "0")
                print(f"  {lbl:34s} {_net(rs)}")
                lo = hi


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+", default=list(UNIVERSE))
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--limit", type=int, default=60_000)
    p.add_argument("--out", default="/tmp/blocktest.json")
    p.add_argument("--report-only", default=None)
    a = p.parse_args()
    if a.report_only:
        report(json.loads(Path(a.report_only).read_text()))
        return
    run(a.symbols, TimeFrame(a.timeframe), a.limit, a.out)


if __name__ == "__main__":
    main()
