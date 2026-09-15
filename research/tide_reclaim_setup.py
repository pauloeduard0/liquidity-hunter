"""K8: HUNT/Tide dizem ONDE procurar, a retomada da VWAP é a entrada (sem OB).

De onde vem
-----------
K4-K7 fecharam a família do clímax: entrar no candle do evento (clímax ou
sweep) empata com o aleatório em qualquer contexto. O que ganhou dinheiro no
projeto espera o preço VOLTAR e retomar uma linha (Block Reclaim, Trend Ride).
Aqui o gatilho é a retomada da VWAP do Tide depois de um recuo contra a HTF,
e o HUNT/CONT e a fase do Tide entram como contexto. Nenhum OB é usado, para
não reconstruir o Block Reclaim.

Gatilho (tudo no fechamento do candle i)
----------------------------------------
    VWAP     a periódica do Tide (sessão até H1, semanal no H4), calculada na
             série inteira: acumula só dentro do período, portanto causal.
    recuo    >= 3 candles seguidos fechando do lado CONTRA da VWAP, no mesmo
             período (o candle i-1 incluso). O 1º candle de um período não
             conta: ali a VWAP reinicia e "fechar acima" é automático.
    retomada close[i] do lado A FAVOR da VWAP; direção = HTF do snapshot ao
             vivo (candle HTF em formação removido).
    stop     extremo do recuo (menor mínima do recuo + candle i numa compra),
             sem folga.  alvo 2R, horizonte 60, custo 0,13% (régua K4-K7).

Braços, fixados ANTES de rodar
------------------------------
    V0      retomada a favor da HTF                              (base)
    V_hunt  V0 + perna LTF CONTRA a HTF no candle i (o hunt ainda aberto)
    V_ep    V0 + episódio HUNT/CONT com captura A FAVOR terminado nos
            últimos 20 candles (leitura ao vivo do snapshot, contexto K3)
    V_env   V0 + fase orientada em (0, 50): voltou para dentro do envelope,
            sem fechar esticado
    V_he    V_hunt + V_env
    V_ee    V_ep + V_env
Informativo: r_atr <= 1 / > 1, compra/venda.

Critério por TF (M15, M30, H1, H4): nos 4 recortes (early/late 70/30 no
tempo, search/holdout por símbolo) n >= 30, net > 0, net > controle casado
(série, direção, r_atr) e, para os braços de contexto, net > V0.
Predição escrita antes: V0 perde em M15/M30 e fica perto de zero em H1/H4;
o envelope soma ~0,1R como em K6/K7; V_ep não ajuda (K3: episódio a favor
foi o pior corte do Block Reclaim).

Run:
    poetry run python -m research.tide_reclaim_setup --tfs 15m,30m,1h,4h --workers 7 \\
        --out research/tide_reclaim_setup_baseline.json
    poetry run python -m research.tide_reclaim_setup \\
        --report-only research/tide_reclaim_setup_baseline.json
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import random
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import fmean
from typing import Any

from liquidity_hunter.app.dashboard_data import (
    _HIGHER_TIMEFRAME_MAP,
    _VWAP_ANCHOR_PERIOD,
    _VWAP_DEFAULT_ANCHOR_PERIOD,
    load_dashboard_data,
)
from liquidity_hunter.core.domain import TimeFrame
from liquidity_hunter.indicators import vwap
from research._paginated import NoFuturesProvider
from research._symbols import UNIVERSE, sample_of
from research.block_reclaim_hunt_context import (
    LIMIT,
    RECENT,
    TF_MINUTES,
    SlicedProvider,
    context_at,
)
from research.hunt_knowability import TRADE_HORIZON, causal_atr, trade_r
from research.hunt_reclaim_setup import CONTROL_DRAWS, _fmt, _stats, net_r
from research.quality_features import CachedProvider

VISIBLE = 500
MIN_PULLBACK = 3
MAX_PULLBACK = 60
EARLY_SHARE = 0.7
MIN_CUT = 30
ARMS = ("V0", "V_hunt", "V_ep", "V_env", "V_he", "V_ee")
CACHE = Path(__file__).parent / ".replay_cache" / "tide_reclaim_setup"


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
    tide = vwap(candles, symbol=symbol, timeframe=tf,
                anchor=_VWAP_ANCHOR_PERIOD.get(tf, _VWAP_DEFAULT_ANCHOR_PERIOD))
    if last <= VISIBLE + 100 or tide is None:
        out_path.write_text("[]")
        return rows
    pts = {p.timestamp: p for p in tide.points}
    atrs = causal_atr(candles)
    window = SlicedProvider(series, htf)
    rng = random.Random(f"{seed}|{symbol}|{tf_name}")
    step = timedelta(minutes=TF_MINUTES.get(tf_name, 1440))
    days = (candles[last].timestamp - candles[VISIBLE].timestamp).total_seconds() / 86400

    def side_of(k: int) -> int:
        p = pts.get(candles[k].timestamp)
        if p is None:
            return 0
        return 1 if candles[k].close > p.value else (-1 if candles[k].close < p.value else 0)

    sides = [side_of(k) for k in range(n)]
    for i in range(VISIBLE, last):
        s = sides[i]
        p = pts.get(candles[i].timestamp)
        if s == 0 or p is None or not atrs[i]:
            continue
        run = 0
        k = i - 1
        while k >= 0 and run < MAX_PULLBACK and sides[k] == -s:
            q = pts.get(candles[k].timestamp)
            if q is None or q.anchor_timestamp != p.anchor_timestamp:
                break
            run += 1
            k -= 1
        if run < MIN_PULLBACK:
            continue
        up = s == 1
        seg = candles[i - run : i + 1]
        c = candles[i]
        risk = (c.close - min(x.low for x in seg)) if up else (max(x.high for x in seg) - c.close)
        if risk <= 0:
            continue
        window.cut = c.timestamp
        try:
            data = load_dashboard_data(
                provider=window, symbol=symbol, timeframe=tf, limit=VISIBLE,
                futures_provider=NoFuturesProvider(),
            )
            ctx = context_at(data, up, c.timestamp - RECENT * step)
        except Exception:  # noqa: BLE001 - corte quebrado fica fora
            continue
        if ctx["htf"] != "with":
            continue
        span = (p.upper_1 - p.value) if p.upper_1 is not None else 0.0
        oriented = None
        if span > abs(p.value) * 1e-4:
            oriented = 50.0 * (c.close - p.value) / span * (1 if up else -1)
        env = oriented is not None and 0 < oriented < 50
        hunt = ctx["regime"] == "counter"
        ep = ctx["episode"] == "with"
        arms = ["V0"]
        if hunt:
            arms.append("V_hunt")
        if ep:
            arms.append("V_ep")
        if env:
            arms.append("V_env")
            if hunt:
                arms.append("V_he")
            if ep:
                arms.append("V_ee")
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
            "arms": arms, "r_atr": r_atr, "net": net, "run": run,
            "gross": trade_r(candles, i, up, risk, 2.0),
            "ctl_net": fmean(ctl) if ctl else None, "phase": oriented,
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
            print(f"  [{k}/{len(futs)}] {tf} {s:11s} {len(res):5d} ({time.time() - t0:6.0f}s)",
                  flush=True)
    return {"meta": {"generated": datetime.now(tz=UTC).isoformat(), "seed": seed, "tfs": tfs},
            "rows": rows, "spans": spans}


def report(payload: dict[str, Any]) -> None:
    rows, spans = payload["rows"], payload["spans"]
    print(f"\n{'=' * 78}\nK8 - RETOMADA DA VWAP DO TIDE + HUNT/CONT\n{'=' * 78}")
    for tf in payload["meta"]["tfs"]:
        tf_spans = [v for k, v in spans.items() if k.startswith(f"{tf}:")]
        universe_days = max(tf_spans) if tf_spans else 1
        sel_tf = [r for r in rows if r["tf"] == tf]
        print(f"\n--- {tf} --- {len(tf_spans)} series")
        base: dict[str, Any] = {}
        for arm in ARMS:
            sel = [r for r in sel_tf if arm in r["arms"]]
            print(f"  {arm:6s} {_fmt(_stats(sel))}  ~{len(sel) / universe_days * 30:.0f}/mes")
            ok = True
            for key, val in (("time", "early"), ("time", "late"),
                             ("sample", "search"), ("sample", "holdout")):
                st = _stats([r for r in sel if r[key] == val])
                print(f"         [{val:7s}] {_fmt(st)}")
                if arm == "V0":
                    base[val] = st
                b = base.get(val)
                if (st is None or st["n"] < MIN_CUT or st["net"] <= 0 or st["net"] <= st["ctl"]
                        or (arm != "V0" and (b is None or st["net"] <= b["net"]))):
                    ok = False
            print(f"         => {'APROVADO' if ok else 'reprovado'}")
            print(f"         (r_atr<=1) {_fmt(_stats([r for r in sel if r['r_atr'] <= 1]))}")
            print(f"         (r_atr>1 ) {_fmt(_stats([r for r in sel if r['r_atr'] > 1]))}")
            for up, lado in ((True, "compra"), (False, "venda ")):
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
