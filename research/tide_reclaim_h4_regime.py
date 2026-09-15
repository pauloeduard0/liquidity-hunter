"""K11: o que um observador via no gatilho que separa 2023-2024 na retomada VWAP H4?

De onde vem
-----------
K10 (`research/tide_reclaim_h4_refine.py`): V0 + alvo 3R, uma posição por vez,
é positivo em 5 de 7 anos, mas 2023 (-278R) e 2024 (-144R) são negativos. O
calendário não pode ser filtro; aqui só entram leituras conhecidas no
fechamento do candle do gatilho.

Features, fixadas ANTES de rodar
--------------------------------
    vol     ATR14/close do símbolo, percentil dentro dos seus 500 candles anteriores
    eff     eficiência de Kaufman do símbolo em 60 candles
            |close[i]-close[i-60]| / soma |close[k]-close[k-1]|  (tendência vs range)
    btc     BTC H4 a favor do trade: close do BTC acima (compra) / abaixo (venda)
            da EMA200 do BTC no último candle fechado <= gatilho
    curve   soma do R dos últimos 30 trades do setup (universo) já FECHADOS
            antes da entrada (filtro de curva de capital)

Método
------
1. Descritivo: cada feature em tercis (cortes calculados SÓ em early+search,
   `btc` é binário), net / R total por tercil em late, holdout e 2023-24.
2. Um corte por feature, escolhido em early+search: descartar o tercil de pior
   net ali (btc: descartar o lado "contra"; curve: descartar curve <= 0).
3. O corte PASSA se: o grupo descartado tem R total < 0 em late E holdout; o
   grupo mantido tem SR diário >= o conjunto inteiro em late E holdout; e o
   mantido perde menos em 2023+2024 somados do que o inteiro.
Uma posição por vez é refeita depois de cada corte (quem é descartado não ocupa
posição). Saída X3.

Predição escrita antes: `eff` (range) é o candidato mais provável; `curve`
tende a passar por construção em regimes longos mas custa os primeiros meses
de cada recuperação; `vol` não passa.

Run:
    poetry run python -m research.tide_reclaim_h4_regime
"""

from __future__ import annotations

import bisect
import json
from collections import defaultdict
from statistics import fmean

from liquidity_hunter.indicators import ema_series
from research.block_reclaim_hunt_context import LIMIT
from research.hunt_knowability import causal_atr
from research.quality_features import CachedProvider
from research.tide_reclaim_h4_trades import BASELINE, TF, fmt, one_at_a_time, rebuild, stats

EXIT = "X3"
VOL_WINDOW = 500
EFF_WINDOW = 60
CURVE_N = 30
CUTS = {
    "late": lambda r: r["time"] == "late",
    "holdout": lambda r: r["sample"] == "holdout",
    "23-24": lambda r: r["day"][:4] in ("2023", "2024"),
    "tudo": lambda r: True,
}


def annotate(rows: list[dict]) -> None:
    provider = CachedProvider()
    btc = provider.get_ohlcv("BTCUSDT", TF, LIMIT)
    btc_ts = [c.timestamp for c in btc]
    btc_ema = ema_series(btc, 200)
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_symbol[r["symbol"]].append(r)
    for symbol, rs in by_symbol.items():
        candles = provider.get_ohlcv(symbol, TF, LIMIT)
        atrs = causal_atr(candles)
        pct = [a / c.close if a else None for a, c in zip(atrs, candles, strict=True)]
        for r in rs:
            i = r["i"]
            c = candles[i]
            past = [p for p in pct[max(0, i - VOL_WINDOW) : i] if p is not None]
            r["vol"] = (sum(p < pct[i] for p in past) / len(past)) if past and pct[i] else None
            if i >= EFF_WINDOW:
                path = sum(abs(candles[k].close - candles[k - 1].close)
                           for k in range(i - EFF_WINDOW + 1, i + 1))
                r["eff"] = abs(c.close - candles[i - EFF_WINDOW].close) / path if path else None
            else:
                r["eff"] = None
            k = bisect.bisect_right(btc_ts, c.timestamp) - 1
            e = btc_ema[k] if k >= 200 else None
            r["btc"] = None if e is None else float((btc[k].close > e) == r["up"])
            r["exit_ts"] = candles[r["trades"][EXIT][1]].timestamp.isoformat() \
                if EXIT in r["trades"] else None
            r["entry_ts"] = c.timestamp.isoformat()


def add_curve(taken: list[dict]) -> None:
    """curve = soma dos últimos 30 trades do universo fechados antes da entrada."""
    closed = sorted(taken, key=lambda r: r["exit_ts"])
    exits = [r["exit_ts"] for r in closed]
    for r in taken:
        k = bisect.bisect_left(exits, r["entry_ts"])
        window = closed[max(0, k - CURVE_N) : k]
        r["curve"] = sum(x["net_x"] for x in window) if len(window) == CURVE_N else None


def show(label: str, taken: list[dict], all_days: list[str]) -> dict[str, dict | None]:
    out = {c: stats([r for r in taken if f(r)], all_days) for c, f in CUTS.items()}
    for c in CUTS:
        print(f"   {label:18s} [{c:7s}] {fmt(out[c])}")
    return out


def main() -> None:
    payload = json.loads(BASELINE.read_text())
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for r in payload["rows"]:
        if r["tf"] == "4h" and "V0" in r["arms"]:
            by_symbol[r["symbol"]].append(r)
    rows = [x for s, rs in by_symbol.items() for x in rebuild(s, rs)]
    rows = [r for r in rows if EXIT in r["trades"]]
    annotate(rows)
    all_days = sorted({r["day"] for r in rows})
    print(f"\nK11 | V0 H4 X3 | {len(rows)} gatilhos")

    full = one_at_a_time(rows, EXIT)
    add_curve(full)
    print("\n== conjunto inteiro ==")
    full_st = show("inteiro", full, all_days)
    curve_of = {(r["symbol"], r["i"]): r["curve"] for r in full}
    for r in rows:
        r["curve"] = curve_of.get((r["symbol"], r["i"]))

    disc = [r for r in full if r["time"] == "early" and r["sample"] == "search"]
    for feat in ("vol", "eff", "btc", "curve"):
        print(f"\n== {feat} ==")
        vals = sorted(r[feat] for r in disc if r[feat] is not None)
        if feat == "btc":
            groups = {"contra": lambda v: v == 0.0, "a favor": lambda v: v == 1.0}
        elif feat == "curve":
            groups = {"<=0": lambda v: v <= 0, ">0": lambda v: v > 0}
        else:
            t1, t2 = vals[len(vals) // 3], vals[2 * len(vals) // 3]
            print(f"   tercis (early+search): {t1:.3f} | {t2:.3f}")
            groups = {
                "baixo": lambda v, t1=t1: v <= t1,
                "medio": lambda v, t1=t1, t2=t2: t1 < v <= t2,
                "alto": lambda v, t2=t2: v > t2,
            }
        disc_net = {}
        for g, f in groups.items():
            sel = [r for r in full if r[feat] is not None and f(r[feat])]
            d = [r for r in sel if r in disc]
            disc_net[g] = fmean(r["net_x"] for r in d) if d else 0.0
            print(f"   grupo {g} (early+search net {disc_net[g]:+.3f}R)")
            show(g, sel, all_days)
        if feat == "btc":
            drop = "contra"
        elif feat == "curve":
            drop = "<=0"
        else:
            drop = min(disc_net, key=disc_net.get)
        f_drop = groups[drop]
        dropped = [r for r in full if r[feat] is not None and f_drop(r[feat])]
        kept_pop = [r for r in rows if not (r[feat] is not None and f_drop(r[feat]))]
        kept = one_at_a_time(kept_pop, EXIT)
        print(f"   corte: descartar '{drop}'")
        kept_st = show("mantido", kept, all_days)
        drop_st = {c: stats([r for r in dropped if CUTS[c](r)], all_days) for c in CUTS}
        neg = all(drop_st[c] is None or drop_st[c]["total"] < 0 for c in ("late", "holdout"))
        better = all(kept_st[c] and full_st[c] and kept_st[c]["sr"] >= full_st[c]["sr"]
                     for c in ("late", "holdout"))
        less_bad = bool(kept_st["23-24"] and full_st["23-24"]
                        and kept_st["23-24"]["total"] > full_st["23-24"]["total"])
        ok = neg and better and less_bad
        print(f"   => {'PASSA' if ok else 'reprovado'} (descartado negativo late+holdout: {neg}; "
              f"SR mantido >= inteiro: {better}; perde menos em 23-24: {less_bad})")


if __name__ == "__main__":
    main()
