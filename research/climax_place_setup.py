"""K7: clímax VSA num LUGAR (OB ou pool de liquidez) + HTF + Tide.

De onde vem
-----------
K6 (`research/climax_hunt_long.py`) fechou o clímax como gatilho solto: em
janela longa ele perde em todo TF e nem o HUNT nem o Tide o levam a
positivo. O que ganhou dinheiro no projeto (Block Reclaim, Trend Ride) tem
um lugar. Hipótese: o clímax vale como CONFIRMAÇÃO de um lugar.

Lugar, lido do snapshot ao vivo do candle do clímax
---------------------------------------------------
    ob    `POIZone` na direção do trade, criado antes do clímax, não
          invalidado antes dele; o pavio entra na zona (low <= topo numa
          compra) e o fechamento fica acima do fundo.
    pool  `LiquidityZone` do lado oposto (sell_side numa compra: EQL/swing
          low), formada antes, não invalidada antes; o pavio passa do nível
          (low < price_low) e o candle fecha de volta acima de price_high.

Braços, fixados ANTES de rodar (compra = selling climax, venda = buying climax)
-------------------------------------------------------------------------------
    P0       clímax, sem HTF nem lugar                          (base)
    P_ob     clímax dentro de OB
    P_pool   clímax varrendo pool e fechando de volta
    P_any    ob ou pool
    P_htf    P_any + HTF a favor
    P_env    P_any + fase orientada em (-50, +50)
    P_full   P_any + HTF a favor + fase em (-50, +50)

Régua K4-K6 (entrada no close, stop no extremo do clímax, 2R, h60, custo
0,13%), controle casado por série, direção e r_atr. Critério por TF e braço
de lugar: nos 4 recortes (early/late, search/holdout) n >= 30, net > 0,
net > controle e net > P0. Compra e venda reportadas separadas (informativo).
Predição escrita antes: lugar melhora ~0,1R sobre P0; aprova no máximo um
braço em H1/H4; M15 não aprova.

Run:
    poetry run python -m research.climax_place_setup --tfs 15m,30m,1h,4h --workers 7 \\
        --out research/climax_place_setup_baseline.json
    poetry run python -m research.climax_place_setup \\
        --report-only research/climax_place_setup_baseline.json
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import random
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Any

from liquidity_hunter.app.dashboard_data import _HIGHER_TIMEFRAME_MAP, load_dashboard_data
from liquidity_hunter.core.domain import (
    LiquiditySide,
    LiquidityZoneType,
    MarketDirection,
    TimeFrame,
    VSAPattern,
)
from liquidity_hunter.indicators import volume_delta_series
from liquidity_hunter.psychology import VolumeSpreadAnalyzer
from research._paginated import NoFuturesProvider
from research._symbols import UNIVERSE, sample_of
from research.block_reclaim_hunt_context import LIMIT, SlicedProvider
from research.hunt_knowability import TRADE_HORIZON, causal_atr, trade_r
from research.hunt_reclaim_setup import CONTROL_DRAWS, _fmt, _stats, ltf_trend, net_r, phase_at
from research.quality_features import CachedProvider

VISIBLE = 500
EARLY_SHARE = 0.7
MIN_CUT = 30
ARMS = ("P0", "P_ob", "P_pool", "P_any", "P_htf", "P_env", "P_full")
PLACE_ARMS = ARMS[1:]
CACHE = Path(__file__).parent / ".replay_cache" / "climax_place_setup"
POOL_TYPES = frozenset({
    LiquidityZoneType.EQUAL_HIGHS, LiquidityZoneType.EQUAL_LOWS,
    LiquidityZoneType.SWING_HIGH, LiquidityZoneType.SWING_LOW,
})
CLIMAX_UP = {VSAPattern.SELLING_CLIMAX: True, VSAPattern.BUYING_CLIMAX: False}


def job(symbol: str, tf_name: str, seed: int) -> list[dict[str, Any]]:
    out_path = CACHE / f"{tf_name}_{symbol}.json"
    if out_path.exists():
        return json.loads(out_path.read_text())
    tf = TimeFrame(tf_name)
    htf = _HIGHER_TIMEFRAME_MAP[tf]
    provider = CachedProvider()
    series = {
        tf: provider.get_ohlcv(symbol, tf, LIMIT),
        htf: provider.get_ohlcv(symbol, htf, LIMIT),
    }
    candles = series[tf]
    n = len(candles)
    last = n - TRADE_HORIZON - 1
    rows: list[dict[str, Any]] = []
    if last <= VISIBLE + 100:
        out_path.write_text("[]")
        return rows
    atrs = causal_atr(candles)
    vd = volume_delta_series(candles)
    vsa = VolumeSpreadAnalyzer()
    window = SlicedProvider(series, htf)
    rng = random.Random(f"{seed}|{symbol}|{tf_name}")
    days = (candles[last].timestamp - candles[VISIBLE].timestamp).total_seconds() / 86400
    for i in range(VISIBLE, last):
        sig = vsa.classify_candle(candles, vd, i)
        if sig is None or sig.pattern not in CLIMAX_UP or not atrs[i]:
            continue
        up = CLIMAX_UP[sig.pattern]
        c = candles[i]
        risk = (c.close - c.low) if up else (c.high - c.close)
        if risk <= 0:
            continue
        window.cut = c.timestamp
        try:
            data = load_dashboard_data(
                provider=window, symbol=symbol, timeframe=tf, limit=VISIBLE,
                futures_provider=NoFuturesProvider(),
            )
        except Exception:  # noqa: BLE001 - corte quebrado fica fora
            continue
        ts = c.timestamp
        side = data.higher_timeframe_direction.value
        htf_with = side == ("bullish" if up else "bearish")
        phase = phase_at(data, c)
        oriented = None if phase is None else (phase if up else -phase)
        env = oriented is not None and -50 < oriented < 50
        want = MarketDirection.BULLISH if up else MarketDirection.BEARISH
        in_ob = any(
            z.direction is want and z.created_at < ts
            and (z.invalidated_at is None or z.invalidated_at >= ts)
            and ((c.low <= z.price_high and c.close >= z.price_low) if up
                 else (c.high >= z.price_low and c.close <= z.price_high))
            for z in data.poi_zones
        )
        pool_side = LiquiditySide.SELL_SIDE if up else LiquiditySide.BUY_SIDE
        in_pool = any(
            z.side is pool_side and z.formed_at < ts
            and z.zone_type in POOL_TYPES
            and (z.invalidated_at is None or z.invalidated_at >= ts)
            and ((c.low < z.price_low and c.close > z.price_high) if up
                 else (c.high > z.price_high and c.close < z.price_low))
            for z in data.liquidity_zones
        )
        arms = ["P0"]
        if in_ob:
            arms.append("P_ob")
        if in_pool:
            arms.append("P_pool")
        if in_ob or in_pool:
            arms.append("P_any")
            if htf_with:
                arms.append("P_htf")
            if env:
                arms.append("P_env")
            if htf_with and env:
                arms.append("P_full")
        ltf = ltf_trend(data)
        net = net_r(candles, i, up, risk)
        if net is None:
            continue
        r_atr = risk / atrs[i]
        ctl = []
        for _ in range(CONTROL_DRAWS):
            j = rng.randrange(VISIBLE, last)
            if atrs[j]:
                v = net_r(candles, j, up, r_atr * atrs[j])
                if v is not None:
                    ctl.append(v)
        rows.append({
            "symbol": symbol, "tf": tf_name, "ts": c.timestamp.isoformat(), "up": up,
            "arms": arms, "r_atr": r_atr, "net": net,
            "gross": trade_r(candles, i, up, risk, 2.0),
            "ctl_net": fmean(ctl) if ctl else None, "phase": oriented, "ltf": ltf,
            "time": "early" if (i - VISIBLE) / (last - VISIBLE) < EARLY_SHARE else "late",
            "sample": sample_of(symbol), "days": days,
        })
    CACHE.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows))
    return rows


def run(symbols: tuple[str, ...], tfs: list[str], seed: int, workers: int) -> dict[str, Any]:
    CACHE.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    spans: dict[str, float] = {}
    ctx = multiprocessing.get_context("spawn")
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        futs = {pool.submit(job, s, tf, seed): (s, tf) for tf in tfs for s in symbols}
        for k, fut in enumerate(as_completed(futs), 1):
            s, tf = futs[fut]
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                print(f"  ! {tf} {s}: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
                continue
            rows.extend(res)
            if res:
                spans[f"{tf}:{s}"] = res[0]["days"]
            print(f"  [{k}/{len(futs)}] {tf} {s:11s} {len(res):4d} ({time.time() - t0:6.0f}s)",
                  flush=True)
    return {"meta": {"generated": datetime.now(tz=UTC).isoformat(), "seed": seed, "tfs": tfs},
            "rows": rows, "spans": spans}


def report(payload: dict[str, Any]) -> None:
    rows, spans = payload["rows"], payload["spans"]
    print(f"\n{'=' * 78}\nK7 - CLIMAX NUM LUGAR (OB / POOL) + HTF + TIDE\n{'=' * 78}")
    for tf in payload["meta"]["tfs"]:
        tf_spans = [v for k, v in spans.items() if k.startswith(f"{tf}:")]
        universe_days = max(tf_spans) if tf_spans else 1
        sel_tf = [r for r in rows if r["tf"] == tf]
        print(f"\n--- {tf} --- {len(tf_spans)} series, span mediano "
              f"{sorted(tf_spans)[len(tf_spans) // 2] if tf_spans else 0:.0f}d")
        base = {}
        for arm in ARMS:
            sel = [r for r in sel_tf if arm in r["arms"]]
            month = len(sel) / universe_days * 30
            print(f"  {arm:6s} {_fmt(_stats(sel))}  ~{month:.0f}/mes")
            ok = True
            for key, val in (("time", "early"), ("time", "late"),
                             ("sample", "search"), ("sample", "holdout")):
                st = _stats([r for r in sel if r[key] == val])
                print(f"         [{val:7s}] {_fmt(st)}")
                if arm == "P0":
                    base[val] = st
                b = base.get(val)
                if (st is None or st["n"] < MIN_CUT or st["net"] <= 0 or st["net"] <= st["ctl"]
                        or (arm != "P0" and (b is None or st["net"] <= b["net"]))):
                    ok = False
            if arm in PLACE_ARMS:
                print(f"         => {'APROVADO' if ok else 'reprovado'}")
            for up, lado in ((True, "compra"), (False, "venda")):
                print(f"         ({lado}) {_fmt(_stats([r for r in sel if r['up'] is up]))}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report-only", type=Path)
    parser.add_argument("--tfs", default="15m,30m,1h,4h")
    parser.add_argument("--symbols", type=int, default=len(UNIVERSE))
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--workers", type=int, default=7)
    args = parser.parse_args()
    if args.report_only:
        report(json.loads(args.report_only.read_text()))
        return
    payload = run(UNIVERSE[: args.symbols], args.tfs.split(","), args.seed, args.workers)
    if args.out:
        args.out.write_text(json.dumps(payload))
        print(f"\nescrito: {args.out}")
    report(payload)


if __name__ == "__main__":
    main()
