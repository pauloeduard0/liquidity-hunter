"""Profundidade da visita como FEATURE, com o stop de producao fixo.

Nasce do achado de 2026-09-02 (ver `deep_reclaim.py` e as quatro rejeicoes do
stop profundo): dentro do gate de producao `r_atr<=1.0`, os gatilhos cuja
visita ao bloco foi **rasa** acertaram 64,5% contra 37,7% dos de visita
profunda. O gap de 27pp era grande demais para ser ignorado -- mas estava
**confundido**, porque naquela rodada os dois grupos usavam stops diferentes:
o grupo raso usava um stop identico ao de producao (98,5% de identidade) e o
grupo profundo usava o stop da visita. Parte do gap podia ser "stop fundo
perde", que ja e um negativo conhecido, e nao "visita rasa preve".

Este arquivo remove a confusao com UMA mudanca: **o stop e sempre o de
producao** (`test_extreme`), para os dois grupos. A profundidade da visita
deixa de ser o stop e passa a ser so uma **medida descritiva** do gatilho:

    depth_atr = (quanto a visita afundou ALEM do extremo do teste) / ATR

`depth_atr == 0` e a visita que nao passou do extremo do teste -- o bloco
segurou na primeira tentativa. `depth_atr` alto e o preco passeando dentro do
bloco antes de reagir: a mesma reacao, mas depois de o bloco ja ter sido
machucado.

Tres coisas que este arquivo NAO faz, ditas para nao serem lidas como se
fizesse. Nao mede stop nenhum -- esse eixo esta encerrado em quatro medicoes.
Nao usa o `r_atr` da visita para gatear: o gate e o de producao, sobre o stop
de producao, exatamente como em `app/block_reclaim.py`. E nao decide o corte:
o relatorio sai por faixa de `depth_atr` para o limiar ser escolhido com o
numero na frente, e a faixa `0` sai separada porque e um caso qualitativamente
distinto (nao houve mergulho), nao o primeiro balde de um continuo.

Os tres filtros do `deep_reclaim` (EMA9 a favor, cor exigida no `l2`, cauda de
65%) sao **desligaveis** aqui, porque a rodada anterior os tinha ligados junto
com tudo e nenhum deles pode ser creditado sem ser medido a parte. O padrao e
LIGADO, para a comparacao com o achado de 02/09 ser direta; `--plain` desliga
os tres de uma vez.

Controle aleatorio casado em simbolo E direcao, com o R deste braco -- sem
casar a direcao, qualquer periodo que tendeu parece preditivo.

Run:
    poetry run python -m research.visit_depth --out /tmp/visit_depth.json
    poetry run python -m research.visit_depth --report-only /tmp/visit_depth.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections.abc import Sequence
from pathlib import Path
from statistics import fmean

from liquidity_hunter.app.block_reclaim import (
    STRICT_WICK_FRACTION,
    detect_block_reclaims,
)
from liquidity_hunter.app.dashboard_data import load_dashboard_data
from liquidity_hunter.core.domain import MarketDirection, TimeFrame
from liquidity_hunter.data.exceptions import DataProviderError
from liquidity_hunter.indicators import ema_series
from pydantic import ValidationError
from research._paginated import NoFuturesProvider, PaginatedFuturesProvider
from research._symbols import UNIVERSE, sample_of
from research.deep_reclaim import DEFAULT_GAP, _atr, outcome, visit_start

COST_PCT = 0.0010
TARGETS = (2.0, 2.5, 3.0)
HORIZONS = (40, 120)
MIN_VWAP_CANDLES = 4
#: O gate de producao, sobre o stop de producao.
PROD_GATE = 1.0
#: Faixas de profundidade. O `0` sai sozinho de proposito: "nao mergulhou" e
#: um caso distinto, nao o primeiro balde de um continuo.
DEPTH_BANDS = ((0.0, 0.0), (0.0, 0.25), (0.25, 0.5), (0.5, 1.0), (1.0, math.inf))
RANDOM_REPS = 1
MIN_N = 30


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def run(symbols, timeframe, limit, out, *, gap, require_ema9, pinbar_color,
        min_tail, min_vwap=MIN_VWAP_CANDLES, gate=PROD_GATE, drop_pierced=True):
    provider, futures = PaginatedFuturesProvider(), NoFuturesProvider()
    rng = random.Random(7)
    rows: list[dict] = []
    dropped = {"ema9": 0, "vwap": 0, "atravessou": 0, "gate": 0}
    for n, symbol in enumerate(symbols, 1):
        try:
            data = load_dashboard_data(
                provider=provider, symbol=symbol, timeframe=timeframe, limit=limit,
                futures_provider=futures, compute_narrative=False,
            )
        except (DataProviderError, ValidationError) as exc:
            line = str(exc).splitlines()
            detail = line[1].strip() if len(line) > 1 else (line[0] if line else "")
            print(f"  ! {symbol} pulado: {type(exc).__name__}: {detail[:120]}", flush=True)
            continue
        candles = data.candles
        if len(candles) < 400 or data.vwap is None:
            continue
        idx = {c.timestamp: i for i, c in enumerate(candles)}
        e9 = ema_series(candles, 9)
        reclaims = detect_block_reclaims(
            candles, data.poi_zones, data.vwap, symbol=symbol,
            timeframe=timeframe, ema=e9, require_pinbar_color=pinbar_color,
            min_tail_fraction=min_tail,
        )
        kept = 0
        for rec in reclaims:
            if rec.provisional:
                continue
            i0 = idx[rec.timestamp]
            if i0 + max(HORIZONS) >= len(candles):
                continue
            atr = _atr(candles, i0)
            if not atr:
                continue
            if rec.vwap_candles < min_vwap:
                dropped["vwap"] += 1
                continue
            bull = rec.direction is MarketDirection.BULLISH
            pierced = (
                rec.test_extreme < rec.block_price_low if bull
                else rec.test_extreme > rec.block_price_high
            )
            if pierced and drop_pierced:
                dropped["atravessou"] += 1
                continue
            sign = 1.0 if bull else -1.0
            slope = (
                None if i0 - 1 < 10 or e9[i0 - 1] is None or e9[i0 - 10] is None
                else sign * (e9[i0 - 1] - e9[i0 - 10]) / atr
            )
            if require_ema9 and not (slope is not None and slope > 0):
                dropped["ema9"] += 1
                continue

            # O STOP E O DE PRODUCAO, para os dois grupos. E a unica diferenca
            # que importa em relacao ao `deep_reclaim`.
            entry = rec.reclaim_price
            stop = rec.test_extreme
            r = (entry - stop) if bull else (stop - entry)
            if r <= 0:
                continue
            r_atr = r / atr
            if r_atr > gate:
                dropped["gate"] += 1
                continue

            # A profundidade da visita, agora so descritiva.
            touches = [
                j for j, c in enumerate(candles[: i0 + 1])
                if c.low <= rec.block_price_high and c.high >= rec.block_price_low
            ]
            w = candles[visit_start(touches, i0, gap) : i0 + 1]
            visit_ext = min(c.low for c in w) if bull else max(c.high for c in w)
            beyond = (stop - visit_ext) if bull else (visit_ext - stop)
            depth_atr = max(0.0, beyond) / atr

            row = {
                "symbol": symbol, "sample": sample_of(symbol),
                "timestamp": rec.timestamp.isoformat(),
                "direction": rec.direction.value, "arm": "real",
                "entry": entry, "stop": stop,
                "r_pct": r / entry, "r_atr": r_atr,
                "depth_atr": depth_atr,
                "visit_candles": i0 - visit_start(touches, i0, gap) + 1,
                "ema9_slope_lag1": slope,
                "vwap_candles": rec.vwap_candles,
                "first_test": rec.first_test,
                "pinbar_grade": rec.pinbar_grade,
                "pierced": pierced,
                "trigger_line": rec.trigger_line,
                "block_atr": (rec.block_price_high - rec.block_price_low) / atr,
            }
            for target in TARGETS:
                tag = str(target).replace(".", "").rstrip("0") or "0"
                for h in HORIZONS:
                    row[f"r{tag}_h{h}"] = outcome(
                        candles, i0, entry, stop, r, bull=bull, target=target, horizon=h)
            rows.append(row)
            kept += 1

            for _ in range(RANDOM_REPS):
                j = rng.randrange(20, len(candles) - max(HORIZONS) - 1)
                centry = candles[j].close
                cstop = centry - r if bull else centry + r
                crow = {
                    "symbol": symbol, "sample": sample_of(symbol),
                    "timestamp": candles[j].timestamp.isoformat(),
                    "direction": rec.direction.value, "arm": "aleatorio",
                    "r_pct": r / centry, "r_atr": r_atr, "depth_atr": depth_atr,
                }
                for target in TARGETS:
                    tag = str(target).replace(".", "").rstrip("0") or "0"
                    for h in HORIZONS:
                        crow[f"r{tag}_h{h}"] = outcome(
                            candles, j, centry, cstop, r, bull=bull,
                            target=target, horizon=h)
                rows.append(crow)
        print(f"[{n}/{len(symbols)}] {symbol:11s} {kept} entradas", flush=True)
    Path(out).write_text(json.dumps(rows))
    print(f"\ngravado {len(rows)} linhas -> {out}", flush=True)
    print(f"descartados: EMA9 {dropped['ema9']}, vwap jovem {dropped['vwap']}, "
          f"atravessou {dropped['atravessou']}, fora do gate {dropped['gate']}")
    report(rows)


def _line(tag: str, rs: Sequence[dict], key: str, target: float) -> str:
    if len(rs) < MIN_N:
        return f"  {tag:26s} n={len(rs):5d} (poucos)"
    k = sum(1 for r in rs if r[key] >= target - 0.01)
    lo, hi = _wilson(k, len(rs))
    net = fmean(r[key] - COST_PCT / r["r_pct"] for r in rs)
    tot = sum(r[key] - COST_PCT / r["r_pct"] for r in rs)
    return (f"  {tag:26s} n={len(rs):5d}  acerto {k/len(rs):5.1%} "
            f"IC[{lo:4.0%},{hi:4.0%}]  liq {net:+.3f}  total {tot:+7.1f}R")


def report(rows: Sequence[dict], horizon: int = 120, target: float = 2.0) -> None:
    tag = str(target).replace(".", "").rstrip("0") or "0"
    key = f"r{tag}_h{horizon}"
    for sample in ("search", "holdout"):
        S = [r for r in rows if r["sample"] == sample]
        real = [r for r in S if r["arm"] == "real"]
        ctrl = [r for r in S if r["arm"] == "aleatorio"]
        if len(real) < MIN_N:
            continue
        print(f"\n=== {sample} · alvo {target:g}R · h{horizon} · "
              f"stop de PRODUCAO, gate r_atr<={PROD_GATE:g}")
        print(_line("tudo", real, key, target))
        print(_line("  controle aleatorio", ctrl, key, target))
        for lo, hi in DEPTH_BANDS:
            if lo == hi == 0.0:
                sub = [r for r in real if r["depth_atr"] <= 1e-9]
                csb = [r for r in ctrl if r["depth_atr"] <= 1e-9]
                lbl = "depth 0 (nao mergulhou)"
            else:
                sub = [r for r in real if lo < r["depth_atr"] <= hi]
                csb = [r for r in ctrl if lo < r["depth_atr"] <= hi]
                lbl = f"depth {lo:g}-{hi:g}".replace("-inf", "+")
            print(_line(lbl, sub, key, target))
            print(_line("  (aleatorio)", csb, key, target))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+", default=list(UNIVERSE))
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--limit", type=int, default=60_000)
    p.add_argument("--gap", type=int, default=DEFAULT_GAP)
    p.add_argument("--gate", type=float, default=PROD_GATE)
    p.add_argument("--plain", action="store_true",
                   help="desliga os TRES filtros do deep (EMA9, cor l2, cauda 65%%)")
    p.add_argument("--no-ema9", action="store_true")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--no-strict-tail", action="store_true")
    p.add_argument("--horizon", type=int, default=120, choices=HORIZONS)
    p.add_argument("--target", type=float, default=2.0, choices=TARGETS)
    p.add_argument("--out", default="/tmp/visit_depth.json")
    p.add_argument("--keep-pierced", action="store_true",
                   help="manter o teste que atravessou o bloco (o que o "
                        "deep_stop.py fazia, por omissao)")
    p.add_argument("--min-vwap", type=int, default=MIN_VWAP_CANDLES)
    p.add_argument("--report-only", default=None)
    a = p.parse_args()
    if a.report_only:
        report(json.loads(Path(a.report_only).read_text()), a.horizon, a.target)
        return
    run(a.symbols, TimeFrame(a.timeframe), a.limit, a.out, gap=a.gap,
        require_ema9=not (a.no_ema9 or a.plain),
        pinbar_color=None if (a.no_color or a.plain) else "l2",
        min_tail=0.50 if (a.no_strict_tail or a.plain) else STRICT_WICK_FRACTION,
        gate=a.gate, min_vwap=a.min_vwap, drop_pierced=not a.keep_pierced)


if __name__ == "__main__":
    main()
