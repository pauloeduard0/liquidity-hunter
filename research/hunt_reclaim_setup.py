"""K2: um setup de HUNT/CONT com gatilho que existe no candle e nao repinta.

De onde vem
-----------
K1 (`research/hunt_knowability.py`) mostrou que o edge medido do HUNT/CONT era
atraso de confirmacao + sobrevivencia: entrando no candle em que o episodio
aparece ao vivo, a continuation cai de 61,2% para 51,4% (controle 51,5%). O
usuario pediu para construir o setup mesmo assim. A unica forma honesta e
trocar o gatilho por algo que um observador tem NO FECHAMENTO do candle.

O que o registro ja matou, e portanto nao e repetido como achado
-----------------------------------------------------------------
`research/raid_reversal.py`: `raid/cont` (pavio fura e fecha de volta, a favor
da HTF) deu +0,28R contra +0,34R do aleatorio na mesma direcao. O braco S0
abaixo e essa ideia refeita com gatilho causal, e existe so como BASE: o que
o setup precisa mostrar e que o CONTEXTO do hunt (S1/S2) acrescenta algo a ela.

O gatilho (tudo conhecido no fechamento do candle i)
----------------------------------------------------
    lado       HTF de `load_dashboard_data` no corte i, com o candle HTF em
               formacao REMOVIDO (`WindowProvider(drop=...)`). Long se bullish.
    sweep      low[i] < minima dos `SWEEP_LOOKBACK` candles anteriores e
               close[i] > essa minima (espelho para short).
    entrada    close[i].  stop  low[i] (extremo do sweep, sem folga - a regra
               que sobreviveu no Block Reclaim).  alvo  2R.  horizonte 60.
    custo      0,13% ida e volta sobre o preco de entrada, em R (a taxa medida
               em `research/spread_trades.py`, meio do bracket).
    gate       r_atr = risco / ATR14 causal <= 1,0 (no Block Reclaim o gate
               era o setup inteiro). Reportado tambem sem gate.

Contexto, lido do snapshot ao vivo do corte i
---------------------------------------------
    perna LTF  trend do replay dos eventos internos confirmados do snapshot
               (BOS/CHoCH fixam, CHOCH_FAILED reverte) -- o mesmo replay do
               motor do hunt e do Tide.
    fase       50 x (close - VWAP) / (upper_1 - VWAP) no candle i, orientada
               ao lado do trade (H10).

Bracos, fixados ANTES de rodar
------------------------------
    S0   sweep a favor da HTF                              (base = raid/cont)
    S1   S0 + perna LTF CONTRA a HTF                       (o HUNT causal)
    S1c  S0 + perna LTF A FAVOR da HTF                     (a CONT causal)
    S2   S1  + fase orientada em (-50, +50)                (HUNT no envelope)
    S2c  S1c + fase orientada em (-50, +50)                (CONT no envelope)

Criterio de aprovacao (por braco x TF, com gate)
------------------------------------------------
Em DISCOVERY e HOLDOUT (70/30 temporal por serie):
    - net em R apos custo > 0;
    - net > net do controle casado (mesma serie, mesma direcao, mesmo r_atr,
      mesmo custo, indices aleatorios);
    - holdout com n >= 30 (abaixo disso o braco e INCONCLUSIVO, nao aprovado).
Um braco de contexto (S1..S2c) so conta como setup novo se tambem bater o S0
do mesmo TF no net, nas duas amostras. Aprovado aqui => walk-forward com PBO
antes de qualquer uso. Nada em `liquidity_hunter/` e tocado.

Run:
    poetry run python -m research.hunt_reclaim_setup \\
        --out research/hunt_reclaim_setup_baseline.json
    poetry run python -m research.hunt_reclaim_setup \\
        --report-only research/hunt_reclaim_setup_baseline.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any

from liquidity_hunter.app.dashboard_data import _HIGHER_TIMEFRAME_MAP, load_dashboard_data
from liquidity_hunter.core.domain import Candle, MarketDirection, StructureEvent, TimeFrame
from research._paginated import PaginatedFuturesProvider
from research._symbols import UNIVERSE
from research.hunt_htf_causality import TFS, NoFuturesProvider, WindowProvider
from research.hunt_knowability import TRADE_HORIZON, causal_atr, trade_r

FETCH = 3000
VISIBLE = 500
SWEEP_LOOKBACK = 20
ROUND_TRIP_COST = 0.0013
GATE = 1.0
BAND = 50.0
TARGET = 2.0
CONTROL_DRAWS = 20
DISCOVERY_SHARE = 0.7
MIN_HOLDOUT = 30
ARMS = ("S0", "S1", "S1c", "S2", "S2c")
CACHE_DIR = Path(__file__).parent / ".replay_cache" / "hunt_reclaim_setup"


# ---------------------------------------------------------------------------
# 1. Contexto ao vivo por corte
# ---------------------------------------------------------------------------


def ltf_trend(data: Any) -> str:
    trend = "neutral"
    events = sorted(
        (e for e in data.internal_structure_events if not e.provisional),
        key=lambda e: e.timestamp,
    )
    for e in events:
        if e.event in (StructureEvent.BREAK_OF_STRUCTURE, StructureEvent.CHANGE_OF_CHARACTER):
            trend = e.direction.value
        elif e.event is StructureEvent.CHOCH_FAILED:
            trend = "bearish" if e.direction is MarketDirection.BULLISH else "bullish"
    return trend


def phase_at(data: Any, candle: Candle) -> float | None:
    if data.vwap is None or not data.vwap.points:
        return None
    p = data.vwap.points[-1]
    if p.timestamp != candle.timestamp or p.upper_1 is None:
        return None
    span = p.upper_1 - p.value
    if span <= abs(p.value) * 1e-4:
        return None
    return 50.0 * (candle.close - p.value) / span


def context_series(
    symbol: str, tf_name: str, series: dict[TimeFrame, list[Candle]]
) -> dict[str, list[Any]]:
    tf = TFS[tf_name]
    htf = _HIGHER_TIMEFRAME_MAP[tf]
    candles = series[tf]
    own = f"{symbol}_{tf_name}_{len(candles)}_{candles[-1].timestamp:%Y%m%d%H%M}.json"
    cache = CACHE_DIR / own
    if cache.exists():
        return json.loads(cache.read_text())
    htf_dir: list[str | None] = [None] * len(candles)
    ltf: list[str | None] = [None] * len(candles)
    phase: list[float | None] = [None] * len(candles)
    for cut in range(VISIBLE, len(candles)):
        try:
            data = load_dashboard_data(
                provider=WindowProvider(series, cut=candles[cut].timestamp, drop=frozenset({htf})),
                symbol=symbol,
                timeframe=tf,
                limit=VISIBLE,
                futures_provider=NoFuturesProvider(),
            )
        except Exception:  # noqa: BLE001 - um corte quebrado fica sem contexto
            continue
        htf_dir[cut] = data.higher_timeframe_direction.value
        ltf[cut] = ltf_trend(data)
        phase[cut] = phase_at(data, candles[cut])
    out = {"htf": htf_dir, "ltf": ltf, "phase": phase}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out))
    return out


# ---------------------------------------------------------------------------
# 2. Gatilho, trade e controle
# ---------------------------------------------------------------------------


def sweep_trigger(candles: list[Candle], i: int, up: bool) -> bool:
    prior = candles[i - SWEEP_LOOKBACK : i]
    if up:
        level = min(c.low for c in prior)
        return candles[i].low < level < candles[i].close
    level = max(c.high for c in prior)
    return candles[i].high > level > candles[i].close


def arms_for(up: bool, ltf: str | None, phase: float | None) -> list[str]:
    side = "bullish" if up else "bearish"
    out = ["S0"]
    if ltf is None or ltf == "neutral":
        return out
    counter = ltf != side
    oriented = None if phase is None else (phase if up else -phase)
    band = oriented is not None and -BAND < oriented < BAND
    if counter:
        out.append("S1")
        if band:
            out.append("S2")
    else:
        out.append("S1c")
        if band:
            out.append("S2c")
    return out


def net_r(candles: list[Candle], i: int, up: bool, risk: float) -> float | None:
    r = trade_r(candles, i, up, risk, TARGET)
    if r is None:
        return None
    return r - ROUND_TRIP_COST * candles[i].close / risk


def analyse_series(
    symbol: str, tf_name: str, series: dict[TimeFrame, list[Candle]], seed: int
) -> list[dict[str, Any]]:
    ctx = context_series(symbol, tf_name, series)
    candles = series[TFS[tf_name]]
    atrs = causal_atr(candles)
    rng = random.Random(f"{seed}|{symbol}|{tf_name}")
    n = len(candles)
    last = n - TRADE_HORIZON - 1
    span = last - VISIBLE
    days = (candles[last].timestamp - candles[VISIBLE].timestamp).total_seconds() / 86400
    rows: list[dict[str, Any]] = []
    for i in range(max(VISIBLE, SWEEP_LOOKBACK + 15), last):
        side = ctx["htf"][i]
        atr = atrs[i]
        if side not in ("bullish", "bearish") or not atr:
            continue
        up = side == "bullish"
        if not sweep_trigger(candles, i, up):
            continue
        risk = (candles[i].close - candles[i].low) if up else (candles[i].high - candles[i].close)
        if risk <= 0:
            continue
        net = net_r(candles, i, up, risk)
        if net is None:
            continue
        r_atr = risk / atr
        ctl = []
        for _ in range(CONTROL_DRAWS):
            j = rng.randrange(VISIBLE, last)
            aj = atrs[j]
            if aj:
                c = net_r(candles, j, up, r_atr * aj)
                if c is not None:
                    ctl.append(c)
        rows.append(
            {
                "symbol": symbol,
                "tf": tf_name,
                "i": i,
                "up": up,
                "arms": arms_for(up, ctx["ltf"][i], ctx["phase"][i]),
                "r_atr": r_atr,
                "net": net,
                "gross": trade_r(candles, i, up, risk, TARGET),
                "ctl_net": fmean(ctl) if ctl else None,
                "split": "discovery" if (i - VISIBLE) / span < DISCOVERY_SHARE else "holdout",
                "days": days,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# 3. Painel
# ---------------------------------------------------------------------------


def _work(args: tuple[str, str, dict[TimeFrame, list[Candle]], int]) -> list[dict[str, Any]]:
    return analyse_series(*args)


def run_panel(
    symbols: tuple[str, ...], tf_names: tuple[str, ...], seed: int, workers: int
) -> dict[str, Any]:
    provider = PaginatedFuturesProvider()
    jobs, errors = [], []
    spans: dict[str, float] = {}
    for symbol in symbols:
        for tf_name in tf_names:
            tf = TFS[tf_name]
            try:
                series = {
                    tf: provider.get_ohlcv(symbol, tf, FETCH),
                    _HIGHER_TIMEFRAME_MAP[tf]: provider.get_ohlcv(
                        symbol, _HIGHER_TIMEFRAME_MAP[tf], FETCH
                    ),
                }
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{symbol} {tf_name}: {type(exc).__name__}: {exc}")
                continue
            if len(series[tf]) < VISIBLE + TRADE_HORIZON + 200:
                errors.append(f"{symbol} {tf_name}: serie insuficiente ({len(series[tf])})")
                continue
            jobs.append((symbol, tf_name, series, seed))
    print(f"  {len(jobs)} series; contexto ao vivo em {workers} processos", flush=True)
    rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_work, j): (j[0], j[1]) for j in jobs}
        for done, fut in enumerate(as_completed(futs), 1):
            symbol, tf_name = futs[fut]
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{symbol} {tf_name}: {type(exc).__name__}: {exc}")
                continue
            rows.extend(res)
            if res:
                spans[f"{symbol}:{tf_name}"] = res[0]["days"]
            print(f"  [{done}/{len(jobs)}] {symbol} {tf_name}: {len(res)} gatilhos", flush=True)
    return {
        "meta": {
            "generated": datetime.now(tz=UTC).isoformat(),
            "symbols": list(symbols),
            "timeframes": list(tf_names),
            "seed": seed,
            "fetch": FETCH,
            "sweep_lookback": SWEEP_LOOKBACK,
            "cost": ROUND_TRIP_COST,
            "gate": GATE,
            "target": TARGET,
        },
        "rows": rows,
        "spans": spans,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# 4. Relatorio
# ---------------------------------------------------------------------------


def _stats(rows: list[dict[str, Any]]) -> dict[str, float] | None:
    rs = [r for r in rows if r["ctl_net"] is not None]
    if not rs:
        return None
    nets = [r["net"] for r in rs]
    sd = pstdev(nets) if len(nets) > 1 else 0.0
    return {
        "n": len(rs),
        "net": fmean(nets),
        "ctl": fmean(r["ctl_net"] for r in rs),
        "t": fmean(nets) / sd * math.sqrt(len(nets)) if sd > 0 else 0.0,
        "hit": fmean(1.0 if (r["gross"] or 0) >= TARGET else 0.0 for r in rs),
    }


def _fmt(st: dict[str, float] | None) -> str:
    if st is None:
        return "-"
    return (
        f"n={int(st['n']):5d} net {st['net']:+6.3f}R (ctl {st['ctl']:+6.3f}) "
        f"t {st['t']:+5.2f} alvo {st['hit']:5.1%}"
    )


def _per_month(rows: list[dict[str, Any]], spans: dict[str, float]) -> float:
    keys = {f"{r['symbol']}:{r['tf']}" for r in rows}
    days = sum(spans.get(k, 0.0) for k in keys) / max(1, len(keys))
    return len(rows) / days * 30 if days else 0.0


def report(payload: dict[str, Any]) -> None:
    rows, spans, meta = payload["rows"], payload["spans"], payload["meta"]
    title = "K2 - SETUP HUNT/CONT COM GATILHO CAUSAL (sweep no fechamento)"
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    print(
        f"painel: {len(meta['symbols'])} simbolos x {meta['timeframes']} | "
        f"custo {meta['cost']:.2%} ida e volta | erros: {len(payload['errors'])}"
    )
    verdicts: dict[tuple[str, str], bool] = {}
    for tf in meta["timeframes"]:
        print(f"\n--- {tf} ---")
        base: dict[str, dict[str, float] | None] = {}
        for arm in ARMS:
            sel = [r for r in rows if r["tf"] == tf and arm in r["arms"]]
            gated = [r for r in sel if r["r_atr"] <= GATE]
            print(f"  {arm:4s} sem gate   {_fmt(_stats(sel))}")
            monthly = _per_month(gated, spans)
            print(f"  {arm:4s} r_atr<=1   {_fmt(_stats(gated))}  ~{monthly:.0f}/mes")
            ok = True
            for split in ("discovery", "holdout"):
                st = _stats([r for r in gated if r["split"] == split])
                print(f"       [{split:9s}] {_fmt(st)}")
                if arm == "S0":
                    base[split] = st
                if st is None or st["net"] <= 0 or st["net"] <= st["ctl"]:
                    ok = False
                if split == "holdout" and (st is None or st["n"] < MIN_HOLDOUT):
                    ok = False
                if arm != "S0":
                    b = base.get(split)
                    if st is None or b is None or st["net"] <= b["net"]:
                        ok = False
            verdicts[(tf, arm)] = ok
            print(f"       => {'APROVADO' if ok else 'reprovado'}")
    print("\n  RESUMO:")
    for (tf, arm), ok in verdicts.items():
        if ok:
            print(f"    {tf} {arm}: APROVADO (segue para walk-forward)")
    if not any(verdicts.values()):
        print("    nenhum braco aprovado")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report-only", type=Path)
    parser.add_argument("--symbols", type=int, default=len(UNIVERSE))
    parser.add_argument("--tfs", default="M15,H1,H4")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--workers", type=int, default=11)
    args = parser.parse_args()
    if args.report_only:
        report(json.loads(args.report_only.read_text()))
        return
    payload = run_panel(
        UNIVERSE[: args.symbols], tuple(args.tfs.split(",")), args.seed, args.workers
    )
    if args.out:
        args.out.write_text(json.dumps(payload, default=str))
        print(f"\nescrito: {args.out}")
    report(payload)


if __name__ == "__main__":
    main()
