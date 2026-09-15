"""K9: a retomada da VWAP do Tide no H4 como OPERAÇÃO (uma posição por vez).

De onde vem
-----------
K8 (`research/tide_reclaim_setup.py`) achou o primeiro positivo da linha
HUNT/Tide: no H4, V0 (+0,03R) e V_ep (+0,05R) passam os 4 recortes. Mas os
gatilhos se sobrepõem (várias retomadas por perna = várias posições abertas
no mesmo símbolo), o que infla n e t. Aqui a população do K8 é reusada (sem
novo snapshot: os braços e a HTF ao vivo vêm do JSON) e cada trade é
re-simulado com saída explícita.

Passo 1 -- uma posição por vez por símbolo
    gatilho ignorado enquanto o trade anterior do mesmo símbolo está aberto
    (aberto = até o candle de saída, inclusive).
Passo 2 -- por dia
    R somado por dia no universo, dias sem trade contam zero; Sharpe diário
    anualizado (sqrt 365) e R total por recorte.
Passo 3 -- saída, fixadas ANTES de rodar
    X2    alvo 2R (a régua do K8)
    X15   alvo 1,5R
    X3    alvo 3R
    XV    sem alvo: sai no 1º fechamento do lado CONTRA da VWAP (candle
          seguinte ao gatilho em diante); stop no extremo do recuo
    XV3   XV com alvo 3R
    Todas: stop no extremo do recuo, stop+alvo no mesmo candle = stop,
    horizonte 60 candles (fecha a mercado), custo 0,13% ida e volta.

Escolha sem espiar
------------------
Saída escolhida só em EARLY+SEARCH (maior Sharpe diário, braço V0); o
veredito é dela em LATE e em HOLDOUT. Um setup passa se, com uma posição por
vez, tiver net > 0, R total > 0 e Sharpe diário > 0 em LATE e em HOLDOUT
(n >= 30 em cada). V_ep reportado ao lado; só substitui V0 se bater V0 em
Sharpe diário nos dois recortes de confirmação.

Run:
    poetry run python -m research.tide_reclaim_h4_trades
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import fmean, pstdev

from liquidity_hunter.app.dashboard_data import _VWAP_ANCHOR_PERIOD
from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.indicators import vwap
from research.block_reclaim_hunt_context import LIMIT
from research.quality_features import CachedProvider
from research.tide_reclaim_setup import MAX_PULLBACK

BASELINE = Path(__file__).parent / "tide_reclaim_setup_baseline.json"
TF = TimeFrame.H4
HORIZON = 60
COST = 0.0013
EXITS = ("X2", "X15", "X3", "XV", "XV3")
ARMS = ("V0", "V_ep")
MIN_N = 30


def simulate(
    candles: list[Candle], vw: list[float | None], i: int, up: bool, stop: float, exit_rule: str
) -> tuple[float, int] | None:
    entry = candles[i].close
    risk = (entry - stop) if up else (stop - entry)
    if risk <= 0 or i + HORIZON >= len(candles):
        return None
    target = {"X2": 2.0, "X15": 1.5, "X3": 3.0, "XV3": 3.0}.get(exit_rule)
    goal = None if target is None else (entry + target * risk if up else entry - target * risk)
    by_vwap = exit_rule in ("XV", "XV3")
    for j in range(i + 1, i + 1 + HORIZON):
        c = candles[j]
        if (c.low <= stop) if up else (c.high >= stop):
            return -1.0 - COST * entry / risk, j
        if goal is not None and ((c.high >= goal) if up else (c.low <= goal)):
            return target - COST * entry / risk, j
        v = vw[j]
        if by_vwap and v is not None and ((c.close < v) if up else (c.close > v)):
            r = ((c.close - entry) if up else (entry - c.close)) / risk
            return r - COST * entry / risk, j
    last = candles[i + HORIZON].close
    return ((last - entry) if up else (entry - last)) / risk - COST * entry / risk, i + HORIZON


def rebuild(symbol: str, rows: list[dict]) -> list[dict]:
    """Recalcula o stop do K8 (extremo do recuo) e anexa séries para simular."""
    candles = CachedProvider().get_ohlcv(symbol, TF, LIMIT)
    tide = vwap(candles, symbol=symbol, timeframe=TF, anchor=_VWAP_ANCHOR_PERIOD[TF])
    pts = {p.timestamp: p for p in tide.points} if tide else {}
    vw = [pts[c.timestamp].value if c.timestamp in pts else None for c in candles]
    idx = {c.timestamp: k for k, c in enumerate(candles)}
    out = []
    for r in rows:
        i = idx.get(datetime.fromisoformat(r["ts"]))
        if i is None:
            continue
        seg = candles[max(0, i - r["run"]) : i + 1][-(MAX_PULLBACK + 1):]
        stop = min(c.low for c in seg) if r["up"] else max(c.high for c in seg)
        trades = {}
        for x in EXITS:
            res = simulate(candles, vw, i, r["up"], stop, x)
            if res is not None:
                trades[x] = res
        if trades:
            out.append({**r, "i": i, "trades": trades,
                        "day": candles[i].timestamp.date().isoformat()})
    return out


def one_at_a_time(rows: list[dict], exit_rule: str) -> list[dict]:
    taken = []
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if exit_rule in r["trades"]:
            by_symbol[r["symbol"]].append(r)
    for rs in by_symbol.values():
        free_at = -1
        for r in sorted(rs, key=lambda x: x["i"]):
            if r["i"] <= free_at:
                continue
            net, exit_i = r["trades"][exit_rule]
            free_at = exit_i
            taken.append({**r, "net_x": net})
    return taken


def stats(rows: list[dict], all_days: list[str]) -> dict[str, float] | None:
    if len(rows) < 1:
        return None
    daily: dict[str, float] = defaultdict(float)
    for r in rows:
        daily[r["day"]] += r["net_x"]
    span = [d for d in all_days if min(daily) <= d <= max(daily)]
    series = [daily.get(d, 0.0) for d in span]
    sd = pstdev(series) if len(series) > 1 else 0.0
    return {
        "n": len(rows),
        "net": fmean(r["net_x"] for r in rows),
        "total": sum(r["net_x"] for r in rows),
        "hit": fmean(1.0 if r["net_x"] > 0 else 0.0 for r in rows),
        "sr": fmean(series) / sd * math.sqrt(365) if sd > 0 else 0.0,
        "months": len(span) / 30.4,
    }


def fmt(st: dict[str, float] | None) -> str:
    if st is None:
        return "-"
    return (f"n={st['n']:5d} net {st['net']:+.3f}R total {st['total']:+7.1f}R "
            f"ganho {st['hit']:.0%} SR/dia {st['sr']:+.2f} ~{st['n'] / st['months']:.0f}/mes")


def main() -> None:
    payload = json.loads(BASELINE.read_text())
    h4 = [r for r in payload["rows"] if r["tf"] == "4h"]
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for r in h4:
        by_symbol[r["symbol"]].append(r)
    rows: list[dict] = []
    for symbol, rs in by_symbol.items():
        rows.extend(rebuild(symbol, rs))
    all_days = sorted({r["day"] for r in rows})
    print(f"\nK9 - RETOMADA DA VWAP H4 COMO OPERACAO | {len(rows)} gatilhos, "
          f"{len(by_symbol)} simbolos")

    cuts = {
        "early+search": lambda r: r["time"] == "early" and r["sample"] == "search",
        "late": lambda r: r["time"] == "late",
        "holdout": lambda r: r["sample"] == "holdout",
        "tudo": lambda r: True,
    }
    chosen: dict[str, dict] = {}
    for arm in ARMS:
        pop = [r for r in rows if arm in r["arms"]]
        print(f"\n=== {arm} ===")
        overlap = one_at_a_time(pop, "X2")
        print(f"  sobreposicao: {len(pop)} gatilhos -> {len(overlap)} com 1 posicao por vez (X2)")
        for x in EXITS:
            taken = one_at_a_time(pop, x)
            print(f"  {x}")
            for name, f in cuts.items():
                st = stats([r for r in taken if f(r)], all_days)
                print(f"     [{name:12s}] {fmt(st)}")
                chosen.setdefault(arm, {})[(x, name)] = st

    best = max(EXITS, key=lambda x: (chosen["V0"][(x, "early+search")] or {"sr": -9})["sr"])
    print(f"\n  saida escolhida em early+search (V0): {best}")
    for arm in ARMS:
        ok = True
        for name in ("late", "holdout"):
            st = chosen[arm][(best, name)]
            if st is None or st["n"] < MIN_N or st["net"] <= 0 or st["total"] <= 0 or st["sr"] <= 0:
                ok = False
            if arm == "V_ep":
                base = chosen["V0"][(best, name)]
                if st is None or base is None or st["sr"] <= base["sr"]:
                    ok = False
        print(f"  {arm}: {'APROVADO' if ok else 'reprovado'} ({best})")


if __name__ == "__main__":
    main()
