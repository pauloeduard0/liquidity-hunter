"""K6: o candidato H_env (clímax + HUNT + Tide no envelope) em janela longa.

De onde vem
-----------
K5 (`research/climax_tide_setup.py`) deixou um candidato em H1: clímax VSA a
favor da HTF, perna LTF contra a HTF (HUNT ativo) e fase da VWAP dentro do
envelope ±1σ. +0,17R, positivo nas duas amostras, mas holdout n=18 numa
janela de ~102 dias. Aqui a mesma regra vai para a série inteira do cache
(até 60k candles) em M15, M30, H1 e H4.

Como fica barato
----------------
O contexto ao vivo só é calculado nos candles de clímax (~1-2% da série):
`load_dashboard_data` sobre os 500 candles que terminam no candle do clímax,
HTF em formação removida (`SlicedProvider` do K3). VSA por `classify_candle`
(só janela para trás).

Braços, fixados ANTES de rodar (idênticos ao K4/K5)
---------------------------------------------------
    A0     clímax a favor da HTF                      (base)
    A1     A0 + HUNT ativo
    T_env  A0 + fase orientada em (-50, +50)          (Tide sem HUNT)
    H_str  A0 + HUNT + fase orientada <= -50
    H_env  A0 + HUNT + fase orientada em (-50, +50)   <- o candidato

Régua do K4/K5: entrada no close do clímax, stop no extremo dele, alvo 2R,
horizonte 60 candles, custo 0,13% ida e volta. Controle casado por série,
direção e r_atr.

Critério do H_env, por TF
-------------------------
Quatro recortes: early/late (70/30 no tempo de cada série) e search/holdout
(split fixo por símbolo, `research._symbols.sample_of`). Em CADA recorte:
n >= 30, net > 0, net > controle e net > A0. Predição escrita antes: passa
no H1 ou em nenhum; o K1 viu o HUNT repintar mais nos TFs curtos, então M15
é o mais provável de falhar.

Run:
    poetry run python -m research.climax_hunt_long --tfs 15m,30m,1h,4h --workers 7 \\
        --out research/climax_hunt_long_baseline.json
    poetry run python -m research.climax_hunt_long \\
        --report-only research/climax_hunt_long_baseline.json
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
from liquidity_hunter.core.domain import TimeFrame, VSAPattern
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
ARMS = ("A0", "A1", "T_env", "H_str", "H_env")
CACHE = Path(__file__).parent / ".replay_cache" / "climax_hunt_long"
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
        side = data.higher_timeframe_direction.value
        if side != ("bullish" if up else "bearish"):
            continue
        ltf = ltf_trend(data)
        phase = phase_at(data, c)
        oriented = None if phase is None else (phase if up else -phase)
        hunt = ltf in ("bullish", "bearish") and ltf != side
        arms = ["A0"]
        if hunt:
            arms.append("A1")
        if oriented is not None and -50 < oriented < 50:
            arms.append("T_env")
            if hunt:
                arms.append("H_env")
        if hunt and oriented is not None and oriented <= -50:
            arms.append("H_str")
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
    print(f"\n{'=' * 78}\nK6 - CLIMAX + HUNT + TIDE EM JANELA LONGA\n{'=' * 78}")
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
                if arm == "A0":
                    base[val] = st
                b = base.get(val)
                if (st is None or st["n"] < MIN_CUT or st["net"] <= 0 or st["net"] <= st["ctl"]
                        or (arm != "A0" and (b is None or st["net"] <= b["net"]))):
                    ok = False
            if arm == "H_env":
                print(f"         => {'APROVADO' if ok else 'reprovado'}")


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
