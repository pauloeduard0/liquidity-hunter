"""K1: o grab do HUNT/CONT existe no candle em que ele e carimbado?

Por que medir isto antes de qualquer estrategia
-----------------------------------------------
Todos os paineis do hunt (H2 a H10) entram no FECHAMENTO do candle ancora do
episodio (`end_timestamp`). Parte das fontes do grab so passa a existir
depois desse candle:

    - o VSA de pivo (H8/H9) le o candle de um `HIGHER_LOW`/`LOWER_HIGH`, e o
      pivo so e confirmado depois do lookback do detector;
    - o grab de `realignment` e um BOS/CHoCH, cujo atraso mediano ja foi
      medido em 13,5 / 9 candles (`research/event_lag.py`);
    - o `require_vsa` re-ancora o cluster no candle do VSA, que pode ser
      anterior ao sinal que completou o score.

Foi exatamente assim que o MFE/MAE dos eventos de estrutura caiu de 1,9-3,7
para o nivel do controle em `research/control_continuation.py`. Os 61% da
continuation e os 54% do hunt sao, portanto, um TETO ate prova em contrario.

O que o painel faz
------------------
Para cada (simbolo, TF), baixa 1000 candles e roda `load_dashboard_data` +
`LiquidityHuntEngine` sobre cada prefixo que um observador ao vivo teria
(visivel = 500 candles terminando no corte, como o dashboard). Registra, por
episodio, o PRIMEIRO corte em que ele aparece. Tres conjuntos:

    replay    os episodios da passada final (o que os paineis mediam),
              entrando no candle ancora;
    ao vivo   toda primeira aparicao ao vivo, entrando no fechamento do
              candle em que apareceu -- inclui episodios que depois somem;
    controle  indices aleatorios da mesma serie, mesma direcao.

Um episodio ao vivo casa com um do replay se tem o mesmo stream, o mesmo lado
cacado e ancora a no maximo `MATCH_CANDLES` candles.

A previa de trade (nao e o veredito, e o insumo do proximo passo): stop no
extremo do grab (minima/maxima de `anchor-2` ate a entrada), alvo 2R, horizonte
`TRADE_HORIZON`, R bruto sem custo. O controle do trade sorteia indices e usa a
MESMA distancia de stop em ATR causal, entao a geometria do risco e casada.

Hipotese e criterio, fixados ANTES de rodar
-------------------------------------------
K1 e um teste de sobrevivencia, nao de edge novo. O HUNT/CONT e operavel
(segue para o desenho da estrategia) num stream/TF se, entrando no candle
CONHECIVEL, em DISCOVERY e HOLDOUT (70/30 temporal por serie, h=20):
    - o acerto direcional bate o controle casado;
    - o net em ATR e positivo.
Se falhar, o registro e "o edge medido era atraso de confirmacao", e a
estrategia nao e construida sobre aquele stream/TF.

Nada em `liquidity_hunter/` e tocado.

Run:
    poetry run python -m research.hunt_knowability \\
        --out research/hunt_knowability_baseline.json
    poetry run python -m research.hunt_knowability \\
        --report-only research/hunt_knowability_baseline.json
"""

from __future__ import annotations

import argparse
import json
import random
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean, median
from typing import Any

from liquidity_hunter.app.dashboard_data import _HIGHER_TIMEFRAME_MAP, load_dashboard_data
from liquidity_hunter.app.liquidity_hunt import LiquidityHuntEngine
from liquidity_hunter.core.domain import Candle, RetailPositioning, TimeFrame
from liquidity_hunter.indicators.supertrend import true_range_series
from research._symbols import UNIVERSE
from research.hunt_htf_causality import TFS, NoFuturesProvider, WindowProvider, fetch_series
from research.hunt_score_variants import _excursion

VISIBLE = 500
#: Um episodio ancorado antes disto nao foi observavel desde o inicio da perna
#: pelo scan ao vivo (que so comeca no corte VISIBLE); fica de fora dos dois lados.
FIRST_ANCHOR = VISIBLE + 50
MATCH_CANDLES = 3
HORIZONS = (10, 20, 40)
CONTROL_DRAWS = 20
TRADE_HORIZON = 60
ATR_PERIOD = 14
DISCOVERY_SHARE = 0.7
CACHE_DIR = Path(__file__).parent / ".replay_cache" / "hunt_knowability"


# ---------------------------------------------------------------------------
# 1. Replay ao vivo de uma serie
# ---------------------------------------------------------------------------


def _episodes(data: Any) -> list[tuple[str, bool, datetime]]:
    engine = LiquidityHuntEngine()
    out = []
    for stream, eps in (
        ("hunt", engine.build_history(data)),
        ("continuation", engine.build_continuation_history(data)),
    ):
        for e in eps:
            out.append((stream, e.hunted_side is RetailPositioning.SHORT, e.end_timestamp))
    return out


def _load(series: dict[TimeFrame, list[Candle]], symbol: str, tf: TimeFrame, cut: datetime) -> Any:
    return load_dashboard_data(
        provider=WindowProvider(series, cut=cut),
        symbol=symbol,
        timeframe=tf,
        limit=VISIBLE,
        futures_provider=NoFuturesProvider(),
    )


def replay_series(
    symbol: str, tf_name: str, series: dict[TimeFrame, list[Candle]]
) -> dict[str, Any]:
    """Primeira aparicao ao vivo de cada episodio + o conjunto da passada final."""
    tf = TFS[tf_name]
    candles = series[tf]
    index = {c.timestamp: i for i, c in enumerate(candles)}
    first_seen: dict[tuple[str, bool, int], int] = {}
    errors = 0
    for cut in range(VISIBLE, len(candles)):
        try:
            data = _load(series, symbol, tf, candles[cut].timestamp)
        except Exception:  # noqa: BLE001 - um prefixo quebrado nao para a serie
            errors += 1
            continue
        for stream, up, ts in _episodes(data):
            i = index.get(ts)
            if i is not None:
                first_seen.setdefault((stream, up, i), cut)
    final = [
        (stream, up, index[ts])
        for stream, up, ts in _episodes(_load(series, symbol, tf, candles[-1].timestamp))
        if ts in index
    ]
    return {
        "live": [[s, u, a, c] for (s, u, a), c in first_seen.items()],
        "final": [[s, u, a] for s, u, a in final],
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# 2. Resultado: excursao direcional e previa de trade
# ---------------------------------------------------------------------------


def causal_atr(candles: list[Candle]) -> list[float | None]:
    tr = true_range_series(candles)
    out: list[float | None] = []
    for i in range(len(candles)):
        out.append(fmean(tr[i - ATR_PERIOD + 1 : i + 1]) if i >= ATR_PERIOD else None)
    return out


def excursion_row(
    candles: list[Candle], i: int, up: bool, atr: float, rng: random.Random
) -> dict[str, Any]:
    rec: dict[str, Any] = {}
    for h in HORIZONS:
        real = _excursion(candles, i, up, h, atr)
        if real is None:
            continue
        ctl = [
            e
            for _ in range(CONTROL_DRAWS)
            if (
                e := _excursion(
                    candles, rng.randrange(ATR_PERIOD, len(candles) - h - 1), up, h, atr
                )
            )
        ]
        if not ctl:
            continue
        end = candles[i + h].close
        rec[f"win_{h}"] = real[0] > real[1]
        rec[f"net_{h}"] = ((end - candles[i].close) if up else (candles[i].close - end)) / atr
        rec[f"ctl_win_{h}"] = fmean(1.0 if c[0] > c[1] else 0.0 for c in ctl)
    return rec


def trade_r(
    candles: list[Candle], i: int, up: bool, risk: float, target: float = 2.0
) -> float | None:
    """R bruto de entrar no fechamento de `i` com stop a `risk` e alvo `target`R.

    Stop e alvo no mesmo candle contam como stop (lado adverso primeiro).
    """
    if risk <= 0 or i + TRADE_HORIZON >= len(candles):
        return None
    entry = candles[i].close
    stop = entry - risk if up else entry + risk
    goal = entry + target * risk if up else entry - target * risk
    for c in candles[i + 1 : i + 1 + TRADE_HORIZON]:
        if (c.low <= stop) if up else (c.high >= stop):
            return -1.0
        if (c.high >= goal) if up else (c.low <= goal):
            return target
    last = candles[i + TRADE_HORIZON].close
    return ((last - entry) if up else (entry - last)) / risk


def trade_row(
    candles: list[Candle],
    atrs: list[float | None],
    anchor: int,
    entry: int,
    up: bool,
    rng: random.Random,
) -> dict[str, Any]:
    atr = atrs[entry]
    if atr is None or atr <= 0:
        return {}
    lo = max(0, anchor - 2)
    window = candles[lo : entry + 1]
    stop = min(c.low for c in window) if up else max(c.high for c in window)
    risk = (candles[entry].close - stop) if up else (stop - candles[entry].close)
    r = trade_r(candles, entry, up, risk)
    if r is None or risk <= 0:
        return {}
    r_atr = risk / atr
    ctl = []
    for _ in range(CONTROL_DRAWS):
        j = rng.randrange(ATR_PERIOD + 1, len(candles) - TRADE_HORIZON - 1)
        aj = atrs[j]
        if aj:
            cr = trade_r(candles, j, up, r_atr * aj)
            if cr is not None:
                ctl.append(cr)
    return {"r_atr": r_atr, "trade_r": r, "ctl_trade_r": fmean(ctl) if ctl else None}


def analyse_series(
    symbol: str, tf_name: str, series: dict[TimeFrame, list[Candle]], seed: int
) -> dict[str, Any]:
    own = series[TFS[tf_name]]
    stamp = f"{own[-1].timestamp:%Y%m%d%H%M}"
    cache = CACHE_DIR / f"{symbol}_{tf_name}_{len(own)}_{stamp}.json"
    if cache.exists():
        raw = json.loads(cache.read_text())
    else:
        raw = replay_series(symbol, tf_name, series)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(raw))
    candles = series[TFS[tf_name]]
    rng = random.Random(f"{seed}|{symbol}|{tf_name}")
    atr_full = fmean(true_range_series(candles))
    atrs = causal_atr(candles)
    n = len(candles)
    span = n - FIRST_ANCHOR

    def base(stream: str, up: bool, anchor: int) -> dict[str, Any]:
        pos = (anchor - FIRST_ANCHOR) / span if span > 0 else 0.0
        return {
            "symbol": symbol,
            "tf": tf_name,
            "stream": stream,
            "up": up,
            "anchor": anchor,
            "split": "discovery" if pos < DISCOVERY_SHARE else "holdout",
        }

    final = [(s, u, a) for s, u, a in raw["final"] if a >= FIRST_ANCHOR]
    live = [(s, u, a, c) for s, u, a, c in raw["live"] if a >= FIRST_ANCHOR]

    rows: list[dict[str, Any]] = []
    # replay: o que os paineis mediam, entrando na ancora
    for s, u, a in final:
        matches = [
            c for ls, lu, la, c in live if ls == s and lu == u and abs(la - a) <= MATCH_CANDLES
        ]
        exact = [c for ls, lu, la, c in live if ls == s and lu == u and la == a]
        row = {
            **base(s, u, a),
            "arm": "replay",
            "entry": a,
            "seen_live": bool(matches),
            "lag": (min(matches) - a) if matches else None,
            "lag_exact": (min(exact) - a) if exact else None,
        }
        row.update(excursion_row(candles, a, u, atr_full, rng))
        row.update(trade_row(candles, atrs, a, a, u, rng))
        rows.append(row)
    # ao vivo: cada episodio distinto (agrupado por proximidade) na primeira aparicao
    live_sorted = sorted(live, key=lambda x: (x[0], x[1], x[2]))
    groups: list[list[tuple[str, bool, int, int]]] = []
    for item in live_sorted:
        g = groups[-1] if groups else None
        if (
            g
            and g[-1][0] == item[0]
            and g[-1][1] == item[1]
            and item[2] - g[-1][2] <= MATCH_CANDLES
        ):
            g.append(item)
        else:
            groups.append([item])
    for g in groups:
        s, u = g[0][0], g[0][1]
        first = min(g, key=lambda x: x[3])
        a, cut = first[2], first[3]
        survives = any(
            fs == s and fu == u and abs(fa - la) <= MATCH_CANDLES
            for fs, fu, fa in final
            for _, _, la, _ in g
        )
        row = {**base(s, u, a), "arm": "live", "entry": cut, "lag": cut - a, "survives": survives}
        row.update(excursion_row(candles, cut, u, atr_full, rng))
        row.update(trade_row(candles, atrs, a, cut, u, rng))
        rows.append(row)
    return {"rows": rows, "errors": raw["errors"]}


# ---------------------------------------------------------------------------
# 3. Painel
# ---------------------------------------------------------------------------


def _work(
    args: tuple[str, str, dict[TimeFrame, list[Candle]], int],
) -> tuple[str, str, dict[str, Any]]:
    symbol, tf_name, series, seed = args
    return symbol, tf_name, analyse_series(symbol, tf_name, series, seed)


def run_panel(
    symbols: tuple[str, ...], tf_names: tuple[str, ...], seed: int, workers: int
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    jobs = []
    for symbol in symbols:
        for tf_name in tf_names:
            tf = TFS[tf_name]
            try:
                series = fetch_series(symbol, tf, _HIGHER_TIMEFRAME_MAP[tf])
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{symbol} {tf_name}: {type(exc).__name__}: {exc}")
                continue
            if (
                len(series[tf]) < FIRST_ANCHOR + TRADE_HORIZON + 50
                or not series[_HIGHER_TIMEFRAME_MAP[tf]]
            ):
                errors.append(f"{symbol} {tf_name}: serie insuficiente")
                continue
            jobs.append((symbol, tf_name, series, seed))
    print(f"  {len(jobs)} series baixadas; replay em {workers} processos", flush=True)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_work, j) for j in jobs]
        for done, fut in enumerate(as_completed(futures), 1):
            try:
                symbol, tf_name, res = fut.result()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"worker: {type(exc).__name__}: {exc}")
                continue
            rows.extend(res["rows"])
            if res["errors"]:
                errors.append(f"{symbol} {tf_name}: {res['errors']} prefixos com erro")
            print(
                f"  [{done}/{len(jobs)}] {symbol} {tf_name}: {len(res['rows'])} linhas", flush=True
            )
    return {
        "meta": {
            "generated": datetime.now(tz=UTC).isoformat(),
            "symbols": list(symbols),
            "timeframes": list(tf_names),
            "seed": seed,
            "visible": VISIBLE,
            "first_anchor": FIRST_ANCHOR,
            "match_candles": MATCH_CANDLES,
            "trade_horizon": TRADE_HORIZON,
            "futures": "disabled (NoFuturesProvider)",
        },
        "rows": rows,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# 4. Relatorio
# ---------------------------------------------------------------------------


def _agg(rows: list[dict[str, Any]], h: int = 20) -> str:
    rs = [r for r in rows if f"win_{h}" in r]
    if not rs:
        return f"{'-':>40s}"
    win = fmean(1.0 if r[f"win_{h}"] else 0.0 for r in rs)
    ctl = fmean(r[f"ctl_win_{h}"] for r in rs)
    net = fmean(r[f"net_{h}"] for r in rs)
    return f"n={len(rs):5d} acerto {win:6.1%} (ctl {ctl:5.1%}) {win - ctl:+6.1%}  net {net:+5.2f}"


def _trade(rows: list[dict[str, Any]]) -> str:
    rs = [r for r in rows if r.get("trade_r") is not None and r.get("ctl_trade_r") is not None]
    if not rs:
        return f"{'-':>40s}"
    r = fmean(x["trade_r"] for x in rs)
    c = fmean(x["ctl_trade_r"] for x in rs)
    hit = fmean(1.0 if x["trade_r"] >= 2.0 else 0.0 for x in rs)
    return f"n={len(rs):5d} R bruto {r:+5.2f} (ctl {c:+5.2f}) {r - c:+5.2f}  alvo {hit:5.1%}"


def _verdict(rows: list[dict[str, Any]]) -> bool:
    ok = True
    for split in ("discovery", "holdout"):
        rs = [r for r in rows if r["split"] == split and "win_20" in r]
        if not rs:
            return False
        win = fmean(1.0 if r["win_20"] else 0.0 for r in rs)
        ctl = fmean(r["ctl_win_20"] for r in rs)
        net = fmean(r["net_20"] for r in rs)
        ok = ok and win > ctl and net > 0
    return ok


def report(payload: dict[str, Any]) -> None:  # noqa: PLR0915
    rows = payload["rows"]
    meta = payload["meta"]
    print(f"\n{'=' * 78}\nK1 - CONHECIBILIDADE DO GRAB DO HUNT/CONT\n{'=' * 78}")
    print(
        f"painel: {len(meta['symbols'])} simbolos x {meta['timeframes']} | "
        f"erros: {len(payload['errors'])}"
    )
    for stream in ("hunt", "continuation"):
        for tf in [*meta["timeframes"], "todos"]:
            sel = [r for r in rows if r["stream"] == stream and (tf == "todos" or r["tf"] == tf)]
            rep = [r for r in sel if r["arm"] == "replay"]
            live = [r for r in sel if r["arm"] == "live"]
            if not rep and not live:
                continue
            print(f"\n--- {stream.upper()} {tf} ---")
            seen = [r for r in rep if r["seen_live"]]
            lags = [r["lag"] for r in seen]
            share = len(seen) / max(1, len(rep))
            gone = sum(not r["survives"] for r in live)
            print(
                f"  replay {len(rep)} | vistos ao vivo {len(seen)} ({share:.0%}) | "
                f"ao vivo distintos {len(live)} | somem depois {gone}"
            )
            if lags:
                srt = sorted(lags)
                p75, p90 = srt[int(len(srt) * 0.75)], srt[int(len(srt) * 0.9)]
                zero = sum(x <= 0 for x in lags) / len(lags)
                print(
                    f"  atraso (candles): mediana {median(lags):.0f} | p75 {p75} | "
                    f"p90 {p90} | zero {zero:.0%}"
                )
            print(f"  REPLAY na ancora     {_agg(rep)}")
            print(f"  AO VIVO conhecivel   {_agg(live)}")
            print(f"    [discovery]        {_agg([r for r in live if r['split'] == 'discovery'])}")
            print(f"    [holdout  ]        {_agg([r for r in live if r['split'] == 'holdout'])}")
            print(f"    atraso 0-1         {_agg([r for r in live if r['lag'] <= 1])}")
            print(f"    atraso 2-5         {_agg([r for r in live if 2 <= r['lag'] <= 5])}")
            print(f"    atraso >5          {_agg([r for r in live if r['lag'] > 5])}")
            print(f"    somem depois       {_agg([r for r in live if not r['survives']])}")
            print("  previa de trade (stop no extremo do grab, 2R, sem custo):")
            print(f"    replay             {_trade(rep)}")
            print(f"    ao vivo            {_trade(live)}")
            print(
                f"    ao vivo r_atr<=1.0 {_trade([r for r in live if r.get('r_atr', 99) <= 1.0])}"
            )
            mid = [r for r in live if 1.0 < r.get("r_atr", 99) <= 2.0]
            print(f"    ao vivo r_atr 1-2  {_trade(mid)}")
            print(f"    ao vivo r_atr >2   {_trade([r for r in live if 2.0 < r.get('r_atr', 0)])}")
            if tf != "todos":
                verdict = "OPERAVEL (segue)" if _verdict(live) else "NAO SOBREVIVE"
                print(f"  => {stream} {tf}: {verdict}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report-only", type=Path)
    parser.add_argument("--symbols", type=int, default=len(UNIVERSE))
    parser.add_argument("--tfs", default="M15,H1,H4")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--workers", type=int, default=10)
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
