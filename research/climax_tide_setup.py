"""K5: clímax VSA a favor da HTF + Tide (fase da VWAP) + confirmação, H1.

Continua o K4 (`research/climax_hunt_setup.py`): o clímax a favor da HTF tem
volume (~380/mês no universo) mas perde (−0,10R); com HUNT ativo empata
(+0,02R) e bate o controle. Pedido do usuário: somar o Tide e um gatilho que
melhore o acerto. Braços fixados ANTES de rodar, mesma janela/régua/controle
do K4.

Fase orientada = 50 x (close − VWAP) / (upper_1 − VWAP) no candle do clímax,
com sinal a favor do trade (H10). Negativa = preço do lado "errado" da VWAP.

    A0     clímax a favor da HTF (base do K4)
    T_env  A0 + fase orientada em (−50, +50)   dentro do envelope ±1σ
    T_str  A0 + fase orientada <= −50           esticado contra, fora da banda
    T_rec  A0 + fase orientada >= 0             fechou do lado certo da VWAP
    H_env  A0 + HUNT + T_env
    H_str  A0 + HUNT + T_str
    H_rec  A0 + HUNT + T_rec
    C0     A0 confirmado: o candle seguinte fecha além do meio do clímax a
           favor; entrada no close dele, stop no extremo do clímax
    HC     C0 + HUNT

Critério: net > 0 e > controle em discovery e holdout (holdout n >= 30) e
bater o A0 nas duas amostras.

Run:
    poetry run python -m research.climax_tide_setup \\
        --out research/climax_tide_setup_baseline.json
"""

from __future__ import annotations

import argparse
import json
import random
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Any

from liquidity_hunter.indicators import volume_delta_series
from liquidity_hunter.psychology import VolumeSpreadAnalyzer
from research._symbols import UNIVERSE
from research.climax_hunt_setup import TF, _climax_for, load_window
from research.hunt_knowability import TRADE_HORIZON, causal_atr, trade_r
from research.hunt_reclaim_setup import (
    CONTROL_DRAWS,
    DISCOVERY_SHARE,
    MIN_HOLDOUT,
    VISIBLE,
    _fmt,
    _stats,
    net_r,
)
from research.quality_features import CachedProvider

ARMS = ("A0", "T_env", "T_str", "T_rec", "H_env", "H_str", "H_rec", "C0", "HC")


def analyse(symbol: str, seed: int) -> list[dict[str, Any]]:
    loaded = load_window(CachedProvider(), symbol)
    if loaded is None:
        return []
    series, ctx = loaded
    candles = series[TF]
    n = len(candles)
    last = n - TRADE_HORIZON - 2
    span = last - VISIBLE
    days = (candles[last].timestamp - candles[VISIBLE].timestamp).total_seconds() / 86400
    atrs = causal_atr(candles)
    vd = volume_delta_series(candles)
    vsa = VolumeSpreadAnalyzer()
    rng = random.Random(f"{seed}|{symbol}")

    def control(up: bool, r_atr: float) -> float | None:
        vals = []
        for _ in range(CONTROL_DRAWS):
            j = rng.randrange(VISIBLE, last)
            if atrs[j]:
                v = net_r(candles, j, up, r_atr * atrs[j])
                if v is not None:
                    vals.append(v)
        return fmean(vals) if vals else None

    def row(i: int, up: bool, risk: float, arms: list[str]) -> dict[str, Any] | None:
        net = net_r(candles, i, up, risk)
        if net is None or not atrs[i]:
            return None
        r_atr = risk / atrs[i]
        return {
            "symbol": symbol, "i": i, "up": up, "arms": arms, "r_atr": r_atr,
            "net": net, "gross": trade_r(candles, i, up, risk, 2.0),
            "ctl_net": control(up, r_atr),
            "split": "discovery" if (i - VISIBLE) / span < DISCOVERY_SHARE else "holdout",
            "days": days,
        }

    rows: list[dict[str, Any]] = []
    for i in range(VISIBLE, last):
        side = ctx["htf"][i]
        if side not in ("bullish", "bearish"):
            continue
        up = side == "bullish"
        sig = vsa.classify_candle(candles, vd, i)
        if sig is None or sig.pattern is not _climax_for(up):
            continue
        c = candles[i]
        ltf = ctx["ltf"][i]
        hunt = ltf in ("bullish", "bearish") and ltf != side
        phase = ctx["phase"][i]
        oriented = None if phase is None else (phase if up else -phase)
        arms = ["A0"]
        if oriented is not None:
            tags = []
            if -50 < oriented < 50:
                tags.append("env")
            if oriented <= -50:
                tags.append("str")
            if oriented >= 0:
                tags.append("rec")
            arms += [f"T_{t}" for t in tags]
            if hunt:
                arms += [f"H_{t}" for t in tags]
        risk = (c.close - c.low) if up else (c.high - c.close)
        if risk > 0:
            r = row(i, up, risk, arms)
            if r:
                rows.append(r)
        # confirmação no candle seguinte
        nx = candles[i + 1]
        mid = (c.high + c.low) / 2
        if (nx.close > mid) if up else (nx.close < mid):
            risk2 = (nx.close - c.low) if up else (c.high - nx.close)
            if risk2 > 0:
                r = row(i + 1, up, risk2, ["C0"] + (["HC"] if hunt else []))
                if r:
                    rows.append(r)
    return rows


def run(symbols: tuple[str, ...], seed: int, workers: int) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    spans: dict[str, float] = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(analyse, s, seed): s for s in symbols}
        for fut in as_completed(futs):
            try:
                res = fut.result()
            except Exception:  # noqa: BLE001 - serie corrompida (KNCUSDT) fica fora, como no K4
                continue
            rows.extend(res)
            if res:
                spans[futs[fut]] = res[0]["days"]
    return {"meta": {"generated": datetime.now(tz=UTC).isoformat(), "seed": seed},
            "rows": rows, "spans": spans}


def report(payload: dict[str, Any]) -> None:
    rows, spans = payload["rows"], payload["spans"]
    days = sum(spans.values()) / max(len(spans), 1)
    print(f"\nK5 - CLIMAX + TIDE + CONFIRMACAO, H1 | {len(spans)} simbolos, ~{days:.0f} dias")
    base: dict[str, dict | None] = {}
    for arm in ARMS:
        sel = [r for r in rows if arm in r["arms"]]
        print(f"\n  {arm:6s} {_fmt(_stats(sel))}  ~{len(sel) / days * 30:.0f}/mes")
        ok = True
        for split in ("discovery", "holdout"):
            st = _stats([r for r in sel if r["split"] == split])
            print(f"         [{split:9s}] {_fmt(st)}")
            if arm == "A0":
                base[split] = st
            if st is None or st["net"] <= 0 or st["net"] <= st["ctl"]:
                ok = False
            if split == "holdout" and (st is None or st["n"] < MIN_HOLDOUT):
                ok = False
            b = base.get(split)
            if arm != "A0" and (st is None or b is None or st["net"] <= b["net"]):
                ok = False
        print(f"         => {'APROVADO' if ok else 'reprovado'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--workers", type=int, default=7)
    args = parser.parse_args()
    payload = run(UNIVERSE, args.seed, args.workers)
    if args.out:
        args.out.write_text(json.dumps(payload))
    report(payload)


if __name__ == "__main__":
    main()
