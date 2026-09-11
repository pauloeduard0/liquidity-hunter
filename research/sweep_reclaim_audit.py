"""SWEEP S2 — `liquidity_sweep` mistura duas geometrias diferentes?

O S0 mediu que ~43,8% dos eventos rotulados `LIQUIDITY_SWEEP` **fecham alem**
do nivel varrido. Mas `narrative.py:164` descreve todo sweep como::

    "wick pierced {ref} {side} but failed to hold"

Para quase metade dos eventos isso e falso: nao foi so o pavio, foi o corpo.

Este estudo classifica a geometria real de cada sweep contra o
`reference_price_level`, sem tocar no dominio, e pergunta se os dois grupos se
comportam como fenomenos diferentes.

S2.1 — por que um close-through vira sweep (lido do detector)
-------------------------------------------------------------
`internal_structure.py:66` define o evento::

    `LIQUIDITY_SWEEP`: a counter-trend pivot that breaks the trailing
    reference but is not a confirmed reversal

E a confirmacao (`_common.is_sustained_break`) e **persistencia**: o candle que
quebra **e** os `persistence_candles` seguintes tem de fechar todos alem da
referencia. Em producao `persistence_candles = 2`, ou seja, exige-se **3
fechamentos consecutivos** alem do nivel.

Logo um candle pode fechar alem do nivel e o evento ainda ser um
`LIQUIDITY_SWEEP` -- basta que o terceiro fechamento nao segure. **O rotulo e
uma afirmacao sobre a maquina de estados (o CHoCH nao confirmou), nao sobre a
geometria do candle.** A docstring do proprio detector simplifica isso para "a
single candle that pokes through the reference and reverts", e e essa
simplificacao que vazou para a narrativa.

Uso
---
    poetry run python research/sweep_reclaim_audit.py --limit 112
    poetry run python research/sweep_reclaim_audit.py --symbols BTCUSDT --cases
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from liquidity_hunter.app.dashboard_data import DashboardData, load_dashboard_data
from liquidity_hunter.app.structure_confluence import (
    _SWEEP_LOOKBACK,
    StructureConfluenceEngine,
)
from liquidity_hunter.core.domain import (
    LiquiditySide,
    MarketDirection,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.indicators import true_range_series
from research._offline import OfflineKlinesProvider, cached_symbols
from research._paginated import NoFuturesProvider
from research.sweep_audit import HORIZONS, _excursions
from research.sweep_causality import _find_extreme_index, lookback_of

BULL = MarketDirection.BULLISH
SWEEP = StructureEvent.LIQUIDITY_SWEEP
BOS = StructureEvent.BREAK_OF_STRUCTURE
CHOCH = StructureEvent.CHANGE_OF_CHARACTER
BASELINE = Path(__file__).parent / "sweep_s2_baseline.json"
BLOCKS = 4

RECLAIM = "RECLAIM"
CLOSE_THROUGH = "CLOSE_THROUGH"
TOUCH_ONLY = "TOUCH_ONLY"
AMBIGUOUS = "AMBIGUOUS"
NO_REF = "NO_REF"


def classify(
    *, bullish: bool, high: float, low: float, close: float, level: float | None
) -> str:
    """S2.0 — a geometria de UM candle de sweep contra o nivel varrido.

    A leitura e a de `mitigation.py`, que ja separa as duas no projeto: o pavio
    atravessa (sweep) e o fechamento volta (rejeitado), contra o fechamento
    alem (breach). Aqui ela e aplicada ao candle que o evento estrutural data.

    - ``RECLAIM``       pavio alem do nivel, close de volta aquem
    - ``CLOSE_THROUGH`` close alem do nivel
    - ``TOUCH_ONLY``    o extremo tocou o nivel exatamente, sem atravessar
    - ``AMBIGUOUS``     close exatamente NO nivel (nem alem, nem aquem)
    - ``NO_REF``        o evento nao carrega `reference_price_level`
    """
    if level is None:
        return NO_REF
    wick = high if bullish else low
    crossed = wick > level if bullish else wick < level
    if not crossed:
        return TOUCH_ONLY if wick == level else AMBIGUOUS
    if close == level:
        return AMBIGUOUS
    beyond = close > level if bullish else close < level
    return CLOSE_THROUGH if beyond else RECLAIM


@dataclass
class Sweep:
    symbol: str
    timeframe: str
    direction: str
    timestamp: str
    idx: int
    klass: str
    #: quanto o EXTREMO passou do nivel, em ATR (>=0)
    penetration_atr: float | None
    #: quanto o CLOSE ficou alem do nivel, em ATR (so CLOSE_THROUGH)
    close_beyond_atr: float | None
    #: quanto o CLOSE voltou aquem do nivel, em ATR (so RECLAIM)
    reclaim_atr: float | None
    block: int
    #: `known_at` do S1 (piso analitico), para a medicao causal do S2.6
    known_idx: int
    # --- mitigation (S2.2) ---
    mit_sweep: bool | None
    mit_rejected: bool | None
    mit_breach: bool | None
    # --- estrutura posterior (S2.7) ---
    next_bos: int | None
    next_choch: int | None
    #: o preco voltou a atravessar o nivel nos 20 candles seguintes?
    returned: bool | None
    # --- consumidores (S2.9/S2.10) ---
    in_confluence: str | None      # "bos" | "choch" | None
    # --- outcome (S2.6) ---
    mfe: dict = field(default_factory=dict)
    mae: dict = field(default_factory=dict)
    #: braco de controle: mesmo simbolo/TF/direcao, indice deslocado
    ctrl_mfe: dict = field(default_factory=dict)
    ctrl_mae: dict = field(default_factory=dict)


def extract(data: DashboardData, rng_offset: int = 37) -> list[Sweep]:
    candles = data.candles
    events = data.internal_structure_events
    idx_by_ts = {c.timestamp: i for i, c in enumerate(candles)}
    n = len(candles)
    tr = true_range_series(candles)
    atr = statistics.fmean(tr) if tr else 0.0
    lb = lookback_of(data.timeframe)

    flow = sorted(
        ((idx_by_ts[e.timestamp], e) for e in events
         if e.timestamp in idx_by_ts and e.event in (BOS, CHOCH)),
        key=lambda p: p[0],
    )
    # confluencia: a qual evento (se algum) este sweep e creditado
    credited: dict[int, str] = {}
    last_idx = n - 1
    for ev in events:
        if ev.event not in (BOS, CHOCH):
            continue
        ei = idx_by_ts.get(ev.timestamp)
        if ei is None:
            continue
        if ev.event is CHOCH:
            origin, _p = StructureConfluenceEngine._reversal_origin(
                ev, ei, candles, idx_by_ts)
            hi = ei if ev.provisional else (
                StructureConfluenceEngine._choch_forward_bound(ev, ei, flow, last_idx))
            lo = origin
            tag = "choch"
        else:
            lo, hi, tag = ei - _SWEEP_LOOKBACK, ei - 1, "bos"
        for s in range(max(0, lo), min(last_idx, hi) + 1):
            credited.setdefault(s, tag)

    # mitigation: as zonas que este nivel representa
    zones = data.liquidity_zones

    out: list[Sweep] = []
    for ev in events:
        if ev.event is not SWEEP or ev.provisional:
            continue
        i = idx_by_ts.get(ev.timestamp)
        if i is None:
            continue
        c = candles[i]
        bullish = ev.direction is BULL
        level = ev.reference_price_level
        klass = classify(bullish=bullish, high=c.high, low=c.low,
                         close=c.close, level=level)

        pen = beyond = recl = None
        if level is not None and atr > 0:
            wick = c.high if bullish else c.low
            pen = max(0.0, (wick - level) if bullish else (level - wick)) / atr
            d = (c.close - level) if bullish else (level - c.close)
            if d > 0:
                beyond = d / atr
            elif d < 0:
                recl = -d / atr

        # S2.2 — a zona de liquidez correspondente ao nivel varrido
        ms = mr = mb = None
        if level is not None:
            side = LiquiditySide.BUY_SIDE if bullish else LiquiditySide.SELL_SIDE
            near = [
                z for z in zones
                if z.side is side
                and abs((z.price_high if bullish else z.price_low) - level)
                <= (0.001 * level)
            ]
            if near:
                z = near[0]
                ms = bool(z.is_mitigated)
                mr = None if z.sweep_rejected is None else bool(z.sweep_rejected)
                mb = z.breached_at is not None

        # S2.7 — o que a estrutura fez depois
        nb = next((j - i for j, e2 in flow if j > i and e2.event is BOS), None)
        nc = next((j - i for j, e2 in flow if j > i and e2.event is CHOCH), None)
        ret = None
        if level is not None and i + 1 < n:
            w = candles[i + 1: i + 21]
            ret = any((cc.low < level) if bullish else (cc.high > level) for cc in w)

        # S2.6 — outcome medido do `known_at`, nunca do timestamp back-datado
        pivot = _find_extreme_index(candles, ev.price_level, i, high=bullish,
                                    forward=True)
        known = (i if pivot is None else pivot) + lb
        start = min(known, n - 2)
        # A direcao que o evento argumenta: um sweep e uma REJEICAO, entao o
        # movimento esperado e contra o lado que o pavio alcancou.
        expect_bull = not bullish
        mfe, mae = _excursions(candles, start, candles[start].close, atr,
                               expect_bullish=expect_bull)
        # controle casado: mesmo simbolo/TF/direcao/periodo, deslocado um
        # numero fixo de candles -- um ponto qualquer da mesma fita, com a
        # MESMA direcao esperada (a licao do raid_reversal).
        cs = start + rng_offset
        cm, ca = ({}, {})
        if cs < n - 1:
            cm, ca = _excursions(candles, cs, candles[cs].close, atr,
                                 expect_bullish=expect_bull)

        out.append(
            Sweep(
                symbol=data.symbol, timeframe=data.timeframe.value,
                direction=ev.direction.value, timestamp=ev.timestamp.isoformat(),
                idx=i, klass=klass, penetration_atr=pen, close_beyond_atr=beyond,
                reclaim_atr=recl, block=min(BLOCKS - 1, i * BLOCKS // max(1, n)),
                known_idx=known, mit_sweep=ms, mit_rejected=mr, mit_breach=mb,
                next_bos=nb, next_choch=nc, returned=ret,
                in_confluence=credited.get(i),
                mfe=mfe, mae=mae, ctrl_mfe=cm, ctrl_mae=ca,
            )
        )
    return out


# ---------------------------------------------------------------------------


def _pct(vals: list[float], q: float) -> float:
    if not vals:
        return float("nan")
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * len(s)))]


def _bucket(v: float, edges: tuple[float, ...]) -> str:
    lo = 0.0
    for e in edges:
        if v < e:
            return f"{lo:g}-{e:g}"
        lo = e
    return f">{edges[-1]:g}"


def _outcome(rows: list[Sweep], h: int, ctrl: bool = False) -> tuple | None:
    f = "ctrl_mfe" if ctrl else "mfe"
    a = "ctrl_mae" if ctrl else "mae"
    g = [(r, getattr(r, f)[h], getattr(r, a)[h]) for r in rows
         if h in getattr(r, f) and h in getattr(r, a)]
    if not g:
        return None
    fin = [x / y for _r, x, y in g if y > 0]
    return (len(g), statistics.median(fin) if fin else float("nan"),
            sum(1 for _r, x, y in g if x > y) / len(g))


def report(rows: list[Sweep], out: list[str]) -> dict:
    summary: dict[str, Any] = {}
    n = max(1, len(rows))

    # --- S2.0 / S2.3 ------------------------------------------------------
    out.append(f"\n{'='*78}\nS2.0/S2.3 — GEOMETRIA REAL ({len(rows)} sweeps "
               f"confirmados)\n{'='*78}")
    c = Counter(r.klass for r in rows)
    out.append("")
    for k in (RECLAIM, CLOSE_THROUGH, TOUCH_ONLY, AMBIGUOUS, NO_REF):
        out.append(f"    {k:>14s}: {c[k]:6d}  ({c[k]/n:6.1%})")
    summary["classes"] = dict(c)

    out.append("\n  por corte (so RECLAIM vs CLOSE_THROUGH)")
    out.append(f"    {'corte':>12s} {'n':>7s} {'RECLAIM':>9s} {'CLOSE_THR':>10s}")
    groups: dict[str, list[Sweep]] = defaultdict(list)
    for r in rows:
        groups["TOTAL"].append(r)
        groups[r.timeframe].append(r)
        groups[r.direction].append(r)
        groups[f"bloco {r.block}"].append(r)
    for k in ("TOTAL", "15m", "1h", "4h", "bullish", "bearish",
              "bloco 0", "bloco 1", "bloco 2", "bloco 3"):
        g = [x for x in groups.get(k, []) if x.klass in (RECLAIM, CLOSE_THROUGH)]
        if not g:
            continue
        rc = sum(1 for x in g if x.klass == RECLAIM)
        out.append(f"    {k:>12s} {len(g):7d} {rc/len(g):9.1%} "
                   f"{1-rc/len(g):10.1%}")
        summary.setdefault("by_cut", {})[k] = [len(g), rc]

    # por simbolo (S2.17)
    per: dict[str, list[Sweep]] = defaultdict(list)
    for r in rows:
        if r.klass in (RECLAIM, CLOSE_THROUGH):
            per[r.symbol].append(r)
    elig = {k: v for k, v in per.items() if len(v) >= 10}
    if elig:
        rates = {k: sum(1 for x in v if x.klass == CLOSE_THROUGH) / len(v)
                 for k, v in elig.items()}
        vals = sorted(rates.values())
        out.append(f"\n  por simbolo ({len(elig)} com >=10): close-through "
                   f"p25={_pct(vals,.25):.1%} p50={statistics.median(vals):.1%} "
                   f"p75={_pct(vals,.75):.1%}  min={vals[0]:.1%} max={vals[-1]:.1%}")
        out.append(f"    simbolos com close-through >30%: "
                   f"{sum(1 for v in vals if v > .3)}/{len(vals)}")
        summary["per_symbol"] = {"n": len(elig), "p50": statistics.median(vals),
                                 "p25": _pct(vals, .25), "p75": _pct(vals, .75)}

    # --- S2.4 / S2.5 ------------------------------------------------------
    out.append(f"\n{'='*78}\nS2.4/S2.5 — MAGNITUDE\n{'='*78}")
    ct = [r for r in rows if r.klass == CLOSE_THROUGH and r.close_beyond_atr is not None]
    rc = [r for r in rows if r.klass == RECLAIM and r.reclaim_atr is not None]
    for title, g, attr, edges in (
        ("S2.4 — CLOSE_THROUGH: quanto o CLOSE ficou alem do nivel",
         ct, "close_beyond_atr", (0.1, 0.25, 0.5, 1.0)),
        ("S2.5 — RECLAIM: quanto o CLOSE voltou aquem do nivel",
         rc, "reclaim_atr", (0.1, 0.25, 0.5)),
    ):
        if not g:
            continue
        out.append(f"\n  {title}  (n={len(g)})")
        b = Counter(_bucket(getattr(r, attr), edges) for r in g)
        order = [f"{lo:g}-{hi:g}" for lo, hi in zip((0.0,) + edges[:-1], edges, strict=False)]
        order.append(f">{edges[-1]:g}")
        for k in order:
            if b[k]:
                out.append(f"      {k:>10s} ATR: {b[k]:6d}  ({b[k]/len(g):6.1%})")
        v = [getattr(r, attr) for r in g]
        out.append(f"      p50={statistics.median(v):.2f} p90={_pct(v,.9):.2f} "
                   f"max={max(v):.2f} ATR")
        summary[attr] = dict(b) | {"p50": statistics.median(v), "max": max(v)}

    pen = [r.penetration_atr for r in rows if r.penetration_atr is not None]
    if pen:
        out.append(f"\n  penetracao do EXTREMO (todos): p50={statistics.median(pen):.2f} "
                   f"p90={_pct(pen,.9):.2f} max={max(pen):.2f} ATR")

    # --- S2.2 -------------------------------------------------------------
    out.append(f"\n{'='*78}\nS2.2 — CONTRA A TAXONOMIA DE `mitigation.py`\n{'='*78}")
    m = [r for r in rows if r.mit_sweep is not None]
    out.append(f"\n  {len(m)}/{len(rows)} sweeps casam com uma zona mapeada "
               f"(tolerancia 0,1%)")
    if m:
        out.append(f"\n    {'detector':>14s} {'mit.rejected':>13s} "
                   f"{'mit.breached':>13s} {'n':>7s}")
        tbl: Counter = Counter()
        for r in m:
            tbl[(r.klass, r.mit_rejected, r.mit_breach)] += 1
        for (k, rej, br), v in sorted(tbl.items(), key=lambda kv: -kv[1])[:10]:
            out.append(f"    {k:>14s} {str(rej):>13s} {str(br):>13s} {v:7d}")
        summary["mitigation_matrix"] = {f"{k}|{rej}|{br}": v
                                        for (k, rej, br), v in tbl.items()}
        agree = sum(1 for r in m if r.mit_rejected is not None
                    and (r.klass == RECLAIM) == r.mit_rejected)
        known = sum(1 for r in m if r.mit_rejected is not None)
        if known:
            out.append(f"\n    concordancia RECLAIM <-> `sweep_rejected`: "
                       f"{agree}/{known} ({agree/known:.1%})")
            out.append("    (as duas leituras nao tem de bater: `mitigation` le a\n"
                       "     PRIMEIRA vela que cruzou a zona; o detector data o\n"
                       "     pavio que quebrou a referencia ESTRUTURAL)")
            summary["mitigation_agreement"] = [known, agree]

    # --- S2.6 -------------------------------------------------------------
    out.append(f"\n{'='*78}\nS2.6 — OUTCOME CAUSAL (medido do `known_at`)\n{'='*78}")
    out.append("\n  MFE/MAE em ATR, a partir do candle em que o evento passa a\n"
               "  existir (S1), na direcao que um sweep argumenta (rejeicao:\n"
               "  contra o lado que o pavio alcancou). O controle e o MESMO\n"
               "  simbolo/TF/direcao/periodo, deslocado 37 candles.")
    out.append(f"\n    {'grupo':>14s} {'h':>4s} {'n':>6s} {'MFE/MAE':>8s} "
               f"{'MFE>MAE':>8s} {'ctrl':>8s} {'delta':>7s}")
    for name, g in (("RECLAIM", rc), ("CLOSE_THROUGH", ct)):
        for h in HORIZONS:
            a = _outcome(g, h)
            b = _outcome(g, h, ctrl=True)
            if a is None:
                continue
            cw = b[2] if b else float("nan")
            out.append(f"    {name:>14s} {h:4d} {a[0]:6d} {a[1]:8.2f} "
                       f"{a[2]:8.1%} {cw:8.1%} {a[2]-cw:+7.1%}")
            summary.setdefault("outcome", {})[f"{name}/{h}"] = [
                a[0], a[1], a[2], cw]

    # estratificado
    out.append("\n  o mesmo contraste por TF e direcao (h=20, MFE>MAE menos "
               "controle):")
    out.append(f"    {'estrato':>16s} {'RECLAIM':>18s} {'CLOSE_THROUGH':>18s}")
    for tf in ("15m", "1h", "4h"):
        for d in ("bullish", "bearish"):
            cells = []
            for k in (RECLAIM, CLOSE_THROUGH):
                g = [r for r in rows if r.klass == k and r.timeframe == tf
                     and r.direction == d]
                a, b = _outcome(g, 20), _outcome(g, 20, ctrl=True)
                cells.append(f"{a[2]-b[2]:+6.1%} (n={a[0]:4d})"
                             if a and b else "—")
            out.append(f"    {tf+'/'+d:>16s} {cells[0]:>18s} {cells[1]:>18s}")
            summary.setdefault("strata", {})[f"{tf}/{d}"] = cells

    # blocos
    out.append("\n  por bloco temporal (h=20, MFE>MAE menos controle):")
    for b in range(BLOCKS):
        cells = []
        for k in (RECLAIM, CLOSE_THROUGH):
            g = [r for r in rows if r.klass == k and r.block == b]
            a, cc = _outcome(g, 20), _outcome(g, 20, ctrl=True)
            cells.append(f"{a[2]-cc[2]:+6.1%} (n={a[0]:4d})" if a and cc else "—")
        out.append(f"    bloco {b}: RECLAIM {cells[0]:>18s}   "
                   f"CLOSE_THROUGH {cells[1]:>18s}")
        summary.setdefault("blocks", {})[str(b)] = cells

    # --- S2.7 -------------------------------------------------------------
    out.append(f"\n{'='*78}\nS2.7 — ESTRUTURA POSTERIOR\n{'='*78}")
    out.append(f"\n    {'grupo':>14s} {'n':>6s} {'BOS<=20':>9s} {'CHoCH<=20':>10s} "
               f"{'volta o nivel':>14s}")
    for name, g in (("RECLAIM", rc), ("CLOSE_THROUGH", ct)):
        if not g:
            continue
        nb = sum(1 for r in g if r.next_bos is not None and r.next_bos <= 20)
        nc = sum(1 for r in g if r.next_choch is not None and r.next_choch <= 20)
        rt = [r for r in g if r.returned is not None]
        rr = sum(1 for r in rt if r.returned)
        out.append(f"    {name:>14s} {len(g):6d} {nb/len(g):9.1%} "
                   f"{nc/len(g):10.1%} {rr/max(1,len(rt)):14.1%}")
        summary.setdefault("structure_after", {})[name] = [
            len(g), nb, nc, len(rt), rr]

    # --- S2.9 / S2.10 -----------------------------------------------------
    out.append(f"\n{'='*78}\nS2.10 — QUEM CARREGA O CREDITO DE CONFLUENCIA\n{'='*78}")
    out.append(f"\n    {'grupo':>14s} {'n':>6s} {'credita BOS':>12s} "
               f"{'credita CHoCH':>14s} {'nenhum':>8s}")
    for name, g in (("RECLAIM", rc), ("CLOSE_THROUGH", ct)):
        if not g:
            continue
        cb = sum(1 for r in g if r.in_confluence == "bos")
        cc2 = sum(1 for r in g if r.in_confluence == "choch")
        out.append(f"    {name:>14s} {len(g):6d} {cb/len(g):12.1%} "
                   f"{cc2/len(g):14.1%} {1-(cb+cc2)/len(g):8.1%}")
        summary.setdefault("confluence_share", {})[name] = [len(g), cb, cc2]
    return summary


def cases(rows: list[Sweep], out: list[str]) -> None:
    out.append(f"\n{'='*78}\nS2.16 — CASOS (BTC/ETH/SOL)\n{'='*78}")
    majors = {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    pool = [r for r in rows if r.symbol in majors]
    picks = [
        ("A", "reclaim limpo (>=0.25 ATR de volta)",
         lambda r: r.klass == RECLAIM and (r.reclaim_atr or 0) >= 0.25),
        ("B", "close-through pequeno (<0.1 ATR)",
         lambda r: r.klass == CLOSE_THROUGH and (r.close_beyond_atr or 9) < 0.1),
        ("C", "close-through grande (>1 ATR)",
         lambda r: r.klass == CLOSE_THROUGH and (r.close_beyond_atr or 0) > 1.0),
        ("D", "sweep creditado a um BOS", lambda r: r.in_confluence == "bos"),
        ("E", "sweep creditado a um CHoCH", lambda r: r.in_confluence == "choch"),
        ("F", "narrative descreve ERRADO (close-through)",
         lambda r: r.klass == CLOSE_THROUGH),
    ]
    for tag, title, pred in picks:
        g = [r for r in pool if pred(r)]
        out.append(f"\n  {tag}) {title} — {len(g)} casos nos majors")
        if not g:
            out.append("      (nenhum)")
            continue
        r = sorted(g, key=lambda x: (x.symbol, x.timestamp))[0]
        out.append(f"      {r.symbol} {r.timeframe} {r.timestamp}  "
                   f"sweep {r.direction}  [{r.klass}]")
        beyond = "—" if r.close_beyond_atr is None else f"{r.close_beyond_atr:.2f}"
        back = "—" if r.reclaim_atr is None else f"{r.reclaim_atr:.2f}"
        out.append(f"      penetracao {r.penetration_atr:.2f} ATR   "
                   f"close alem {beyond}   reclaim {back}")
        out.append(f"      confluencia: {r.in_confluence or '—'}   "
                   f"BOS em +{r.next_bos}  CHoCH em +{r.next_choch}  "
                   f"volta o nivel: {r.returned}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframes", nargs="+", default=["15m", "1h", "4h"])
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=112)
    ap.add_argument("--candles", type=int, default=1200)
    ap.add_argument("--cases", action="store_true")
    ap.add_argument("--json", default=str(BASELINE))
    args = ap.parse_args()

    tfs = [TimeFrame(t) for t in args.timeframes]
    provider = OfflineKlinesProvider()
    symbols = args.symbols or cached_symbols(tfs)[: args.limit]
    rows: list[Sweep] = []
    errors: list[str] = []
    for s in symbols:
        for tf in tfs:
            try:
                data = load_dashboard_data(
                    provider=provider, symbol=s, timeframe=tf, limit=args.candles,
                    futures_provider=NoFuturesProvider(),
                )
                rows.extend(extract(data))
            except Exception as exc:  # noqa: BLE001 - um simbolo morto nao para o painel
                errors.append(f"{s} {tf.value}: {type(exc).__name__}: {exc}")

    out = [f"painel: {len(symbols)} simbolos x {len(tfs)} TFs x {args.candles} "
           f"candles; {len(rows)} sweeps, {len(errors)} falhas"]
    summary = report(rows, out)
    if args.cases:
        cases(rows, out)
    print("\n".join(out))
    if errors:
        print(f"\nfalhas ({len(errors)}):")
        for e in errors[:10]:
            print(f"  ! {e}")
    Path(args.json).write_text(json.dumps(summary, indent=1, default=str))


if __name__ == "__main__":
    main()
