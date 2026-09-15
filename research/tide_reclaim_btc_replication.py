"""K12: o filtro "BTC a favor" replica fora do H4 onde foi achado?

De onde vem
-----------
K11 (`research/tide_reclaim_h4_regime.py`): na retomada da VWAP H4 (alvo 3R,
uma posição por vez), os trades com o BTC H4 contra a EMA200 perderam −604R
em 2023–24 e o grupo "a favor" foi positivo em todos os recortes. Foi achado
olhando essa população, então vale só como hipótese. Aqui ele é aplicado,
com a regra escrita ANTES, à mesma retomada em M30 e H1 (populações do K8
que não participaram da descoberta). M15 é informativo.

Definição (idêntica ao K11)
---------------------------
    btc a favor  close do último candle H4 do BTC FECHADO até o fechamento do
                 gatilho está acima (compra) / abaixo (venda) da EMA200 H4 do BTC.
    trade        V0 do K8, stop no extremo do recuo, saída X3 (3R, a do H4) e
                 X2 (informativo), horizonte 60, custo 0,13%, uma posição por
                 vez por símbolo, refeita dentro de cada grupo.

Critério de replicação, por TF (M30 e H1)
-----------------------------------------
    1. grupo "a favor": R total > 0 e net > 0 em late E holdout (X3);
    2. grupo "a favor": SR diário > conjunto inteiro em late E holdout;
    3. grupo "contra": R total < 0 no conjunto todo.
Replica se passar em pelo menos um dos dois TFs e, no outro, "a favor" tiver
net > "contra" no conjunto todo. Predição escrita antes: replica no H1, não no
M30 (custo em R maior no TF curto).

Run:
    poetry run python -m research.tide_reclaim_btc_replication
"""

from __future__ import annotations

import bisect
import json
from collections import defaultdict
from datetime import datetime, timedelta

from liquidity_hunter.app.dashboard_data import _VWAP_ANCHOR_PERIOD, _VWAP_DEFAULT_ANCHOR_PERIOD
from liquidity_hunter.core.domain import TimeFrame
from liquidity_hunter.indicators import ema_series, vwap
from research.block_reclaim_hunt_context import LIMIT, TF_MINUTES
from research.quality_features import CachedProvider
from research.tide_reclaim_h4_trades import BASELINE, EXITS, fmt, one_at_a_time, simulate, stats
from research.tide_reclaim_setup import MAX_PULLBACK

CUTS = {"late": lambda r: r["time"] == "late", "holdout": lambda r: r["sample"] == "holdout",
        "23-24": lambda r: r["day"][:4] in ("2023", "2024"), "tudo": lambda r: True}


def rebuild_tf(symbol: str, tf: TimeFrame, rows: list[dict]) -> list[dict]:
    candles = CachedProvider().get_ohlcv(symbol, tf, LIMIT)
    tide = vwap(candles, symbol=symbol, timeframe=tf,
                anchor=_VWAP_ANCHOR_PERIOD.get(tf, _VWAP_DEFAULT_ANCHOR_PERIOD))
    pts = {p.timestamp: p.value for p in tide.points} if tide else {}
    vw = [pts.get(c.timestamp) for c in candles]
    idx = {c.timestamp: k for k, c in enumerate(candles)}
    out = []
    for r in rows:
        i = idx.get(datetime.fromisoformat(r["ts"]))
        if i is None:
            continue
        seg = candles[max(0, i - r["run"]) : i + 1][-(MAX_PULLBACK + 1):]
        stop = min(c.low for c in seg) if r["up"] else max(c.high for c in seg)
        trades = {x: res for x in EXITS if (res := simulate(candles, vw, i, r["up"], stop, x))}
        if trades:
            out.append({**r, "i": i, "trades": trades,
                        "day": candles[i].timestamp.date().isoformat(),
                        "close_ts": candles[i].timestamp
                        + timedelta(minutes=TF_MINUTES.get(r["tf"], 1440))})
    return out


def main() -> None:
    payload = json.loads(BASELINE.read_text())
    btc = CachedProvider().get_ohlcv("BTCUSDT", TimeFrame.H4, LIMIT)
    btc_close_ts = [c.timestamp + timedelta(hours=4) for c in btc]
    btc_ema = ema_series(btc, 200)
    verdict = {}
    for tf_name in ("30m", "1h", "15m"):
        tf = TimeFrame(tf_name)
        by_symbol: dict[str, list[dict]] = defaultdict(list)
        for r in payload["rows"]:
            if r["tf"] == tf_name and "V0" in r["arms"]:
                by_symbol[r["symbol"]].append(r)
        rows = [x for s, rs in by_symbol.items() for x in rebuild_tf(s, tf, rs)]
        for r in rows:
            k = bisect.bisect_right(btc_close_ts, r["close_ts"]) - 1
            e = btc_ema[k] if k >= 200 else None
            r["btc"] = None if e is None else ((btc[k].close > e) == r["up"])
        all_days = sorted({r["day"] for r in rows})
        print(f"\n=== {tf_name} === {len(rows)} gatilhos")
        res = {}
        for x in ("X3", "X2"):
            for label, pop in (("inteiro", rows),
                               ("a favor", [r for r in rows if r["btc"] is True]),
                               ("contra", [r for r in rows if r["btc"] is False])):
                taken = one_at_a_time(pop, x)
                for c, f in CUTS.items():
                    st = stats([r for r in taken if f(r)], all_days)
                    res[(x, label, c)] = st
                    print(f"  {x} {label:8s} [{c:7s}] {fmt(st)}")
        fav = lambda c, res=res: res[("X3", "a favor", c)]  # noqa: E731
        full = lambda c, res=res: res[("X3", "inteiro", c)]  # noqa: E731
        c1 = all(fav(c) and fav(c)["total"] > 0 and fav(c)["net"] > 0 for c in ("late", "holdout"))
        c2 = all(fav(c) and full(c) and fav(c)["sr"] > full(c)["sr"] for c in ("late", "holdout"))
        con = res[("X3", "contra", "tudo")]
        c3 = con is not None and con["total"] < 0
        ordered = bool(fav("tudo") and con and fav("tudo")["net"] > con["net"])
        verdict[tf_name] = (c1 and c2 and c3, ordered)
        print(f"  => a favor positivo late+holdout: {c1}; SR > inteiro: {c2}; contra negativo: {c3}"
              f" | PASSA: {c1 and c2 and c3} | a favor > contra: {ordered}")
    m30, h1 = verdict["30m"], verdict["1h"]
    replica = (h1[0] and m30[1]) or (m30[0] and h1[1])
    print(f"\nREPLICACAO (M30/H1): {'REPLICA' if replica else 'NAO replica'}  (M15 informativo: "
          f"passa={verdict['15m'][0]}, a favor>contra={verdict['15m'][1]})")


if __name__ == "__main__":
    main()
