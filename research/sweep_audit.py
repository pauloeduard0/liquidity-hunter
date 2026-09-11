"""SWEEP — auditoria do conceito de *sweep* no projeto inteiro.

Nao e um backtest de entrada. E uma medicao descritiva do evento
``StructureEvent.LIQUIDITY_SWEEP`` (o sweep que aparece no grafico) e das suas
relacoes com os outros objetos que o projeto tambem chama de sweep/grab/raid.

Usa a implementacao de producao: `_run_internal_structure` (o mesmo caminho do
`load_dashboard_data`, com buffer e ancora estrutural), `mark_swept_zones`,
`POIDetector`, `build_liquidity_grabs`, `build_sweep_contexts`. Nada e
reimplementado aqui -- o que e reimplementado nao e o detector, e a *leitura*
sobre a saida dele.

Dados: `research/.klines_cache` via `research/_offline.py` (sem rede).

Uso
---
    poetry run python research/sweep_audit.py --limit 12          # painel curto
    poetry run python research/sweep_audit.py --timeframes 15m 1h 4h
    poetry run python research/sweep_audit.py --replay --limit 6  # causalidade
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import fmean

from liquidity_hunter.app.dashboard_data import (
    _equal_high_detector,
    _equal_low_detector,
    _run_internal_structure,
)
from liquidity_hunter.app.liquidity_grabs import build_liquidity_grabs
from liquidity_hunter.app.sweep_context import build_sweep_contexts
from liquidity_hunter.core.domain import (
    Candle,
    LiquidityZone,
    LiquidityZoneType,
    MarketDirection,
    MarketStructure,
    POIZone,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.core.domain.enums import LiquiditySide
from liquidity_hunter.data.exceptions import DataProviderError
from liquidity_hunter.indicators.supertrend import true_range_series
from liquidity_hunter.liquidity.detectors import (
    POIDetector,
    SwingHighDetector,
    SwingLowDetector,
)
from liquidity_hunter.liquidity.mitigation import mark_swept_zones
from research._offline import OfflineKlinesProvider, cached_symbols

BULL = MarketDirection.BULLISH
BEAR = MarketDirection.BEARISH

HORIZONS = (5, 10, 20, 40)

#: Quantos candles depois do candle datado a reacao passa a ser medida.
#: 0 = o candle do evento (o que qualquer leitura ingenua faz). O evento so e
#: emitido depois que o pivo confirmador se forma, e o pivo so confirma porque
#: os `swing_lookback` candles seguintes nao superaram o extremo -- ou seja, em
#: `offset=0` a excursao adversa dos primeiros 5 candles esta limitada *por
#: construcao*. Medir a partir de `swing_lookback` remove esse limite.
ENTRY_OFFSET = 0
BASELINE = Path(__file__).parent / "sweep_audit_baseline.json"


# --------------------------------------------------------------------------
# pipeline de producao, uma passada por (simbolo, timeframe)
# --------------------------------------------------------------------------


@dataclass
class Run:
    symbol: str
    timeframe: TimeFrame
    candles: list[Candle]
    events: list[MarketStructure]
    zones: list[LiquidityZone]
    poi: list[POIZone]
    grabs: list
    contexts: list
    atr: float


def run_pipeline(provider, symbol: str, timeframe: TimeFrame, limit: int) -> Run:
    """A composicao de producao relevante para sweeps, sem rede nem futuros."""
    internal = _run_internal_structure(provider, symbol, timeframe, limit, False)
    candles = internal.candles
    zones = mark_swept_zones(
        [
            *SwingHighDetector().detect(candles),
            *SwingLowDetector().detect(candles),
            *_equal_high_detector().detect(candles),
            *_equal_low_detector().detect(candles),
        ],
        candles,
    )
    visible_start, visible_end = candles[0].timestamp, candles[-1].timestamp
    poi = [
        z
        for z in POIDetector().detect(internal.internal_candles)
        if visible_start <= z.created_at <= visible_end
    ]
    grabs = build_liquidity_grabs(
        symbol=symbol,
        timeframe=timeframe,
        liquidity_zones=zones,
        poi_zones=poi,
        candles=candles,
    )
    contexts = build_sweep_contexts(
        symbol=symbol,
        timeframe=timeframe,
        structure_events=internal.events,
        poi_zones=poi,
        candles=candles,
    )
    tr = true_range_series(candles)
    return Run(
        symbol=symbol,
        timeframe=timeframe,
        candles=candles,
        events=internal.events,
        zones=zones,
        poi=poi,
        grabs=grabs,
        contexts=contexts,
        atr=fmean(tr) if tr else 0.0,
    )


# --------------------------------------------------------------------------
# features de um sweep
# --------------------------------------------------------------------------


@dataclass
class Sweep:
    arm: str  # "sweep" | "control"
    symbol: str
    timeframe: str
    index: int
    timestamp: str
    direction: str            # direcao do evento: lado que o pavio alcancou
    provisional: bool
    # --- geometria ---
    has_ref: bool
    penetration_atr: float | None     # extremo alem da referencia, em ATR
    reclaim_atr: float | None         # quanto o close voltou aquem do nivel
    close_beyond: bool | None         # o candle fechou ALEM do nivel?
    wick_class: str                   # A..E (ver report)
    wick_body: float | None
    wick_range: float | None
    body_range: float | None
    # --- referencia ---
    ref_kind: str                     # eq | swing | none | unmatched
    ref_age: int | None               # candles entre formacao do nivel e o sweep
    levels_consumed: int              # niveis distintos que o mesmo pavio cruzou
    nth_sweep: int                    # 1 = primeiro sweep deste nivel
    # --- contexto estrutural ---
    trend_at: str                     # trend do detector no momento
    against_trend: bool | None
    # --- coocorrencia (distancia em candles, None = nao houve) ---
    co_grab: int | None
    co_zone: int | None
    co_raid: int | None
    co_bos_choch_before: int | None
    # --- consequencia estrutural ---
    next_struct: dict = field(default_factory=dict)   # h -> "bos_bull"/"choch_bear"/...
    # --- reacao ---
    mfe: dict = field(default_factory=dict)
    mae: dict = field(default_factory=dict)
    block: int = 0                    # quartil temporal 0..3


def _atr_tol(atr: float) -> float:
    return 0.1 * atr


def classify_wick(candle: Candle, level: float, *, bullish: bool) -> str:
    """A..E — como o candle do sweep se relaciona com o nivel varrido.

    bullish = o sweep alcancou para CIMA (tomou maximas).
    """
    if bullish:
        crossed = candle.high > level
        closed_beyond = candle.close > level
    else:
        crossed = candle.low < level
        closed_beyond = candle.close < level
    if not crossed:
        return "D"  # tocou sem atravessar
    if closed_beyond:
        # C se o corpo inteiro atravessou (abriu alem tambem), senao B
        opened_beyond = (candle.open > level) if bullish else (candle.open < level)
        return "C" if opened_beyond else "B"
    return "A"  # pavio atravessa, close volta


def match_reference(
    level: float, timestamp: datetime, zones: list[LiquidityZone], atr: float, *, bullish: bool
) -> tuple[str, int | None, datetime | None]:
    """Que objeto mapeado o nivel varrido representa, e ha quanto tempo existia."""
    tol = _atr_tol(atr)
    side = LiquiditySide.BUY_SIDE if bullish else LiquiditySide.SELL_SIDE
    # EQ pools ganham do pivo solto no empate: se o nivel varrido coincide com
    # um pool de liquidez mapeado, *isso* e a leitura interessante -- o pivo
    # trailing do detector esta la por construcao.
    best: tuple[int, float, LiquidityZone] | None = None
    for zone in zones:
        if zone.side is not side or zone.formed_at > timestamp:
            continue
        edge = zone.price_high if bullish else zone.price_low
        d = abs(edge - level)
        if d > tol:
            continue
        rank = (
            0
            if zone.zone_type
            in (LiquidityZoneType.EQUAL_HIGHS, LiquidityZoneType.EQUAL_LOWS)
            else 1
        )
        if best is None or (rank, d) < (best[0], best[1]):
            best = (rank, d, zone)
    if best is None:
        return "unmatched", None, None
    zone = best[2]
    return ("eq" if best[0] == 0 else "swing"), None, zone.formed_at


def _nearest(stamps: list[datetime], target_index: int, index_of: dict) -> int | None:
    best = None
    for ts in stamps:
        i = index_of.get(ts)
        if i is None:
            continue
        d = i - target_index
        if best is None or abs(d) < abs(best):
            best = d
    return best


def _excursions(
    candles: list[Candle], index: int, entry: float, atr: float, *, expect_bullish: bool
) -> tuple[dict, dict]:
    """MFE/MAE em ATR, na direcao que o sweep argumenta (rejeicao)."""
    mfe: dict[int, float] = {}
    mae: dict[int, float] = {}
    if atr <= 0:
        return mfe, mae
    for h in HORIZONS:
        window = candles[index + 1 : index + 1 + h]
        if len(window) < h:
            continue
        hi = max(c.high for c in window)
        lo = min(c.low for c in window)
        if expect_bullish:
            mfe[h] = (hi - entry) / atr
            mae[h] = (entry - lo) / atr
        else:
            mfe[h] = (entry - lo) / atr
            mae[h] = (hi - entry) / atr
    return mfe, mae


def build_sweeps(run: Run, rng: random.Random) -> list[Sweep]:
    candles = run.candles
    index_of = {c.timestamp: i for i, c in enumerate(candles)}
    atr = run.atr
    n = len(candles)

    grab_stamps = [g.timestamp for g in run.grabs]
    zone_stamps = [z.invalidated_at for z in run.zones if z.invalidated_at is not None]

    # niveis de pool equal-level ja formados, para a assinatura "raid" do HUNT
    eq_levels = [
        (z.price_high if z.side is LiquiditySide.BUY_SIDE else z.price_low, z.formed_at, z.side)
        for z in run.zones
        if z.zone_type in (LiquidityZoneType.EQUAL_HIGHS, LiquidityZoneType.EQUAL_LOWS)
    ]

    def raid_at(i: int, bullish: bool) -> bool:
        c = candles[i]
        side = LiquiditySide.BUY_SIDE if bullish else LiquiditySide.SELL_SIDE
        for level, formed_at, zside in eq_levels:
            if zside is not side or formed_at > c.timestamp:
                continue
            if bullish and c.high > level and c.close < level:
                return True
            if not bullish and c.low < level and c.close > level:
                return True
        return False

    struct_events = [
        e
        for e in run.events
        if e.event in (StructureEvent.BREAK_OF_STRUCTURE, StructureEvent.CHANGE_OF_CHARACTER)
        and not e.provisional
    ]

    def trend_at(i: int) -> str:
        last = None
        for e in struct_events:
            j = index_of.get(e.timestamp)
            if j is not None and j <= i:
                last = e
        return "none" if last is None else last.direction.value

    out: list[Sweep] = []
    sweeps = [
        e for e in run.events if e.event is StructureEvent.LIQUIDITY_SWEEP
    ]
    seen_levels: list[tuple[float, str]] = []

    for event in sweeps:
        i = index_of.get(event.timestamp)
        if i is None:
            continue
        c = candles[i]
        bullish = event.direction is BULL
        ref = event.reference_price_level
        extreme = c.high if bullish else c.low
        body = abs(c.close - c.open)
        rng_ = c.high - c.low
        wick = (c.high - max(c.open, c.close)) if bullish else (min(c.open, c.close) - c.low)

        if ref is not None and atr > 0:
            pen = max(0.0, (extreme - ref) if bullish else (ref - extreme)) / atr
            rec = ((ref - c.close) if bullish else (c.close - ref)) / atr
            closed_beyond = (c.close > ref) if bullish else (c.close < ref)
            wclass = classify_wick(c, ref, bullish=bullish)
            kind, _, formed_at = match_reference(ref, c.timestamp, run.zones, atr, bullish=bullish)
            age = None
            if formed_at is not None:
                j = index_of.get(formed_at)
                age = i - j if j is not None else None
            # quantos niveis mapeados distintos o mesmo pavio cruzou
            tol = _atr_tol(atr)
            crossed = set()
            for z in run.zones:
                if z.formed_at >= c.timestamp:
                    continue
                # um nivel ja consumido antes deste candle nao e liquidez
                # em repouso: contar nao mede quanto o pavio colheu.
                if z.invalidated_at is not None and z.invalidated_at < c.timestamp:
                    continue
                edge = z.price_high if bullish else z.price_low
                if bullish and c.high > edge >= c.open - tol:
                    crossed.add(round(edge / max(tol, 1e-12)))
                if not bullish and c.low < edge <= c.open + tol:
                    crossed.add(round(edge / max(tol, 1e-12)))
            levels = max(1, len(crossed))
            # n-esimo sweep do mesmo nivel
            key = (round(ref / max(tol, 1e-12)), event.direction.value)
            nth = 1 + sum(1 for k in seen_levels if k == key)
            seen_levels.append(key)
        else:
            pen = rec = None
            closed_beyond = None
            wclass = "none"
            kind, age, levels, nth = "none", None, 1, 1

        trend = trend_at(i)
        against = None if trend == "none" else (trend != event.direction.value)

        co_bos = None
        for e in struct_events:
            j = index_of.get(e.timestamp)
            if j is not None and j <= i:
                co_bos = i - j

        # consequencia estrutural: primeiro BOS/CHoCH em ate h candles
        nxt: dict[str, str] = {}
        for h in HORIZONS:
            label = "none"
            for e in struct_events:
                j = index_of.get(e.timestamp)
                if j is not None and i < j <= i + h:
                    tag = "bos" if e.event is StructureEvent.BREAK_OF_STRUCTURE else "choch"
                    label = f"{tag}_{e.direction.value}"
                    break
            nxt[str(h)] = label

        # a rejeicao argumenta pela direcao OPOSTA ao lado varrido
        expect_bull = not bullish
        entry_i = min(i + ENTRY_OFFSET, n - 1)
        mfe, mae = _excursions(
            candles, entry_i, candles[entry_i].close, atr, expect_bullish=expect_bull
        )

        out.append(
            Sweep(
                arm="sweep",
                symbol=run.symbol,
                timeframe=run.timeframe.value,
                index=i,
                timestamp=event.timestamp.isoformat(),
                direction=event.direction.value,
                provisional=event.provisional,
                has_ref=ref is not None,
                penetration_atr=pen,
                reclaim_atr=rec,
                close_beyond=closed_beyond,
                wick_class=wclass,
                wick_body=(wick / body) if body > 0 else None,
                wick_range=(wick / rng_) if rng_ > 0 else None,
                body_range=(body / rng_) if rng_ > 0 else None,
                ref_kind=kind,
                ref_age=age,
                levels_consumed=levels,
                nth_sweep=nth,
                trend_at=trend,
                against_trend=against,
                co_grab=_nearest(grab_stamps, i, index_of),
                co_zone=_nearest(zone_stamps, i, index_of),
                co_raid=(0 if raid_at(i, bullish) else None),
                co_bos_choch_before=co_bos,
                next_struct=nxt,
                mfe={str(k): v for k, v in mfe.items()},
                mae={str(k): v for k, v in mae.items()},
                block=min(3, int(4 * i / max(1, n))),
            )
        )

        # controle casado por simbolo/TF/direcao: um candle aleatorio da mesma
        # serie, mesma direcao esperada, mesma normalizacao por ATR.
        ci = rng.randrange(50, max(51, n - max(HORIZONS) - 1))
        cmfe, cmae = _excursions(candles, ci, candles[ci].close, atr, expect_bullish=expect_bull)
        out.append(
            Sweep(
                arm="control",
                symbol=run.symbol,
                timeframe=run.timeframe.value,
                index=ci,
                timestamp=candles[ci].timestamp.isoformat(),
                direction=event.direction.value,
                provisional=False,
                has_ref=False,
                penetration_atr=None,
                reclaim_atr=None,
                close_beyond=None,
                wick_class="none",
                wick_body=None,
                wick_range=None,
                body_range=None,
                ref_kind="none",
                ref_age=None,
                levels_consumed=1,
                nth_sweep=1,
                trend_at=trend,
                against_trend=against,
                co_grab=None,
                co_zone=None,
                co_raid=None,
                co_bos_choch_before=None,
                next_struct={},
                mfe={str(k): v for k, v in cmfe.items()},
                mae={str(k): v for k, v in cmae.items()},
                block=min(3, int(4 * ci / max(1, n))),
            )
        )
    return out


# --------------------------------------------------------------------------
# relatorio
# --------------------------------------------------------------------------


def _ratio(rows: list[Sweep], h: int) -> tuple[int, float, float]:
    """n, MFE/MAE mediano, fracao MFE>MAE."""
    pairs = [
        (r.mfe[str(h)], r.mae[str(h)])
        for r in rows
        if str(h) in r.mfe and str(h) in r.mae
    ]
    if not pairs:
        return 0, float("nan"), float("nan")
    ratios = [f / a if a > 0 else float("inf") for f, a in pairs]
    finite = [x for x in ratios if math.isfinite(x)]
    med = statistics.median(finite) if finite else float("nan")
    win = sum(1 for f, a in pairs if f > a) / len(pairs)
    return len(pairs), med, win


def _bucket(value: float | None, edges: list[float]) -> str:
    if value is None:
        return "n/a"
    prev = 0.0
    for e in edges:
        if value < e:
            return f"{prev:g}-{e:g}"
        prev = e
    return f">{edges[-1]:g}"


def report(rows: list[Sweep], out: list[str]) -> dict:
    sw = [r for r in rows if r.arm == "sweep" and not r.provisional]
    ct = [r for r in rows if r.arm == "control"]
    summary: dict = {}

    def line(s: str) -> None:
        out.append(s)

    line(f"\n{'='*78}\nSWEEP AUDIT — {len(sw)} sweeps confirmados, {len(ct)} controles")
    line(f"{'='*78}")

    # --- 4. wick vs close ---
    line("\n[4] WICK vs CLOSE — classe do candle do sweep sobre o nivel varrido")
    line("    A=pavio atravessa+close volta  B=close alem (abriu aquem)  "
         "C=corpo inteiro alem  D=nao atravessou")
    cls = Counter(r.wick_class for r in sw)
    for k in sorted(cls):
        line(f"    {k}: {cls[k]:6d}  ({cls[k]/max(1,len(sw)):6.1%})")
    summary["wick_class"] = dict(cls)

    # --- 3. referencia ---
    line("\n[3] REFERENCIA — que objeto mapeado o nivel varrido representa")
    ref = Counter(r.ref_kind for r in sw)
    for k, v in ref.most_common():
        line(f"    {k:12s}: {v:6d}  ({v/max(1,len(sw)):6.1%})")
    summary["ref_kind"] = dict(ref)

    # --- 8. coocorrencia ---
    line("\n[8] DUPLICIDADE — mesmo wick nomeado por outro detector (dist. em candles)")
    for label, attr in (("liquidity_grab", "co_grab"), ("zone invalidation", "co_zone"),
                        ("HUNT raid", "co_raid")):
        d = [getattr(r, attr) for r in sw]
        for w in (0, 1, 2, 5):
            hit = sum(1 for x in d if x is not None and abs(x) <= w)
            line(f"    {label:18s} +-{w}: {hit:6d}  ({hit/max(1,len(sw)):6.1%})")
        summary.setdefault("cooccurrence", {})[attr] = {
            str(w): sum(1 for x in d if x is not None and abs(x) <= w) for w in (0, 1, 2, 5)
        }

    # --- 9. multi-level ---
    line("\n[9] MULTI-LEVEL — niveis mapeados cruzados pelo mesmo pavio")
    lv = Counter(min(3, r.levels_consumed) for r in sw)
    for k in sorted(lv):
        tag = "3+" if k == 3 else str(k)
        line(f"    {tag}: {lv[k]:6d}  ({lv[k]/max(1,len(sw)):6.1%})")
    summary["levels"] = {str(k): v for k, v in lv.items()}

    # --- 10/11/12 buckets x qualidade ---
    def bucket_table(title: str, key, edges: list[float], h: int = 5) -> None:
        line(f"\n{title}  (h={h})")
        line(f"    {'bucket':>10s}  {'n':>6s}  {'MFE/MAE':>8s}  {'MFE>MAE':>8s}")
        groups: dict[str, list[Sweep]] = defaultdict(list)
        for r in sw:
            groups[_bucket(key(r), edges)].append(r)
        for b in sorted(groups, key=lambda s: (s == "n/a", s)):
            n, med, win = _ratio(groups[b], h)
            line(f"    {b:>10s}  {n:6d}  {med:8.2f}  {win:8.1%}")
        summary.setdefault("buckets", {})[title] = {
            b: list(_ratio(groups[b], h)) for b in groups
        }

    bucket_table("[10] PENETRACAO (ATR)", lambda r: r.penetration_atr,
                 [0.1, 0.25, 0.5, 1.0])
    bucket_table("[11] RECLAIM (ATR, >0 = fechou aquem do nivel)",
                 lambda r: r.reclaim_atr, [0.0, 0.25, 0.5, 1.0])
    bucket_table("[12] WICK/RANGE", lambda r: r.wick_range, [0.25, 0.5, 0.75])
    bucket_table("[16] IDADE DO NIVEL (candles)", lambda r: r.ref_age,
                 [10, 30, 100])

    # --- 15. first vs re-sweep ---
    line("\n[15] PRIMEIRO SWEEP vs RE-SWEEP (h=5)")
    for tag, pred in (("1o", lambda r: r.nth_sweep == 1), ("2o", lambda r: r.nth_sweep == 2),
                      ("3o+", lambda r: r.nth_sweep >= 3)):
        n, med, win = _ratio([r for r in sw if pred(r)], 5)
        line(f"    {tag:4s}  n={n:6d}  MFE/MAE={med:6.2f}  MFE>MAE={win:6.1%}")

    # --- 17. contexto estrutural ---
    line("\n[17] CONTEXTO ESTRUTURAL (h=5)")
    for tag, pred in (("a favor", lambda r: r.against_trend is False),
                      ("contra", lambda r: r.against_trend is True),
                      ("sem trend", lambda r: r.against_trend is None)):
        n, med, win = _ratio([r for r in sw if pred(r)], 5)
        line(f"    {tag:10s} n={n:6d}  MFE/MAE={med:6.2f}  MFE>MAE={win:6.1%}")

    # --- 18. BOS/CHoCH depois ---
    line("\n[18] ESTRUTURA APOS O SWEEP — primeiro evento em h candles")
    for h in HORIZONS:
        c = Counter(r.next_struct.get(str(h), "none") for r in sw)
        tot = max(1, sum(c.values()))
        top = ", ".join(f"{k}={v/tot:.0%}" for k, v in c.most_common(4))
        line(f"    h={h:3d}: {top}")
    summary["next_struct"] = {
        str(h): dict(Counter(r.next_struct.get(str(h), "none") for r in sw))
        for h in HORIZONS
    }

    # --- 19. EQ vs outros ---
    line("\n[19] EQH/EQL vs OUTROS NIVEIS (h=5)")
    for tag in ("eq", "swing", "unmatched"):
        n, med, win = _ratio([r for r in sw if r.ref_kind == tag], 5)
        line(f"    {tag:10s} n={n:6d}  MFE/MAE={med:6.2f}  MFE>MAE={win:6.1%}")

    # --- 23. qualidade vs controle ---
    line("\n[23] REACAO POS-EVENTO vs CONTROLE CASADO (direcao da rejeicao)")
    line(f"    {'h':>4s}  {'n':>6s}  {'sweep MFE/MAE':>14s}  {'MFE>MAE':>8s}   "
         f"{'ctrl MFE/MAE':>13s}  {'MFE>MAE':>8s}")
    for h in HORIZONS:
        n, med, win = _ratio(sw, h)
        cn, cmed, cwin = _ratio(ct, h)
        line(f"    {h:4d}  {n:6d}  {med:14.2f}  {win:8.1%}   {cmed:13.2f}  {cwin:8.1%}")
        summary.setdefault("quality", {})[str(h)] = {
            "n": n, "sweep_ratio": med, "sweep_win": win,
            "control_ratio": cmed, "control_win": cwin,
        }

    # --- 25/26 direcao e timeframe ---
    line("\n[25/26] POR DIRECAO E TIMEFRAME (h=5)")
    line(f"    {'tf':>5s} {'dir':>8s}  {'n':>6s}  {'MFE/MAE':>8s} {'MFE>MAE':>8s}   "
         f"{'ctrl':>7s} {'ctrlWin':>8s}")
    for tf in sorted({r.timeframe for r in sw}):
        for d in ("bullish", "bearish"):
            g = [r for r in sw if r.timeframe == tf and r.direction == d]
            gc = [r for r in ct if r.timeframe == tf and r.direction == d]
            n, med, win = _ratio(g, 5)
            _, cmed, cwin = _ratio(gc, 5)
            line(f"    {tf:>5s} {d:>8s}  {n:6d}  {med:8.2f} {win:8.1%}   "
                 f"{cmed:7.2f} {cwin:8.1%}")
            summary.setdefault("by_tf_dir", {})[f"{tf}/{d}"] = [n, med, win, cmed, cwin]

    # --- 27. robustez temporal ---
    line("\n[27] ROBUSTEZ TEMPORAL — 4 blocos (h=5)")
    for b in range(4):
        n, med, win = _ratio([r for r in sw if r.block == b], 5)
        _, cmed, cwin = _ratio([r for r in ct if r.block == b], 5)
        line(f"    bloco {b}: n={n:6d}  MFE/MAE={med:6.2f} win={win:6.1%}   "
             f"ctrl={cmed:5.2f} win={cwin:6.1%}")

    # --- 28. robustez por simbolo ---
    line("\n[28] ROBUSTEZ POR SIMBOLO (h=5, MFE>MAE vs 50%)")
    per: dict[str, float] = {}
    for s in sorted({r.symbol for r in sw}):
        g = [r for r in sw if r.symbol == s]
        n, _m, win = _ratio(g, 5)
        if n >= 20:
            per[s] = win
    if per:
        vals = sorted(per.values())
        pos = sum(1 for v in per.values() if v > 0.5)
        line(f"    simbolos com n>=20: {len(per)}   >50%: {pos} ({pos/len(per):.0%})   "
             f"mediana={statistics.median(vals):.1%}")
        top = sorted(per.items(), key=lambda kv: -kv[1])[:5]
        bot = sorted(per.items(), key=lambda kv: kv[1])[:5]
        line("    top5:    " + ", ".join(f"{k} {v:.0%}" for k, v in top))
        line("    bottom5: " + ", ".join(f"{k} {v:.0%}" for k, v in bot))
        summary["per_symbol"] = {"n": len(per), "pct_positive": pos / len(per),
                                 "median": statistics.median(vals)}
    return summary


# --------------------------------------------------------------------------
# causalidade: replay por prefixo
# --------------------------------------------------------------------------


class _Prefix(OfflineKlinesProvider):
    """Dublê que serve um prefixo ja em memoria (nao toca o disco)."""

    max_fetch_limit = 100_000

    def __init__(self, candles: list[Candle]) -> None:
        self._candles = candles

    def get_ohlcv(self, symbol=None, timeframe=None, limit: int = 500):
        return self._candles[-limit:]


def birth_indices(
    provider, symbol: str, timeframe: TimeFrame, limit: int, step: int
) -> tuple[Run, dict[tuple, int], set[tuple]]:
    """Quando cada sweep confirmado passou a EXISTIR, rodando a pipeline em prefixos.

    Devolve a passada final, o indice de nascimento por evento e o conjunto de
    sweeps que apareceram em alguma passada e sumiram na final (repaint).
    """
    full = run_pipeline(provider, symbol, timeframe, limit)
    all_candles = provider.get_ohlcv(symbol, timeframe, limit + 300)

    def keys_of(run: Run) -> set[tuple]:
        return {
            (e.timestamp, e.direction)
            for e in run.events
            if e.event is StructureEvent.LIQUIDITY_SWEEP and not e.provisional
        }

    final = keys_of(full)
    born: dict[tuple, int] = {}
    seen: set[tuple] = set()
    for cut in range(limit // 2, limit + 1, step):
        run = run_pipeline(_Prefix(all_candles[: 300 + cut]), symbol, timeframe, cut)
        live = keys_of(run)
        seen |= live
        # o indice de nascimento e relativo a serie final: o ultimo candle do
        # prefixo e `all_candles[300 + cut - 1]`, que na serie final esta em
        # `len(full.candles) - (limit - cut) - 1`.
        idx = len(full.candles) - (limit - cut) - 1
        for k in live:
            born.setdefault(k, idx)
    return full, {k: v for k, v in born.items() if k in final}, seen - final


def causal_arm(
    provider, symbol: str, timeframe: TimeFrame, limit: int, step: int
) -> list[tuple[str, int, float, float]]:
    """Pares (arm, h, MFE, MAE) para os bracos naive e conhecivel."""
    full, born, _vanished = birth_indices(provider, symbol, timeframe, limit, step)
    index_of = {c.timestamp: i for i, c in enumerate(full.candles)}
    rows: list[tuple[str, int, float, float]] = []
    for (ts, direction), birth in born.items():
        i = index_of.get(ts)
        if i is None or full.atr <= 0:
            continue
        expect_bull = direction is not BULL
        for arm, start in (("naive", i), ("knowable", max(i, birth))):
            if start >= len(full.candles) - 1:
                continue
            mfe, mae = _excursions(
                full.candles, start, full.candles[start].close, full.atr,
                expect_bullish=expect_bull,
            )
            for h in HORIZONS:
                if h in mfe:
                    rows.append((arm, h, mfe[h], mae[h]))
    return rows


def replay_audit(provider, symbol: str, timeframe: TimeFrame, limit: int,
                 step: int, out: list[str]) -> dict:
    """O sweep visto no fim da serie existia, com o mesmo timestamp, ao vivo?"""
    full = run_pipeline(provider, symbol, timeframe, limit)
    final = {
        (e.timestamp, e.direction)
        for e in full.events
        if e.event is StructureEvent.LIQUIDITY_SWEEP and not e.provisional
    }
    all_candles = provider.get_ohlcv(symbol, timeframe, limit + 300)

    class Prefix(OfflineKlinesProvider):
        max_fetch_limit = 100_000

        def __init__(self, candles):
            self.c = candles

        def get_ohlcv(self, *a, **k):
            return self.c[-k.get("limit", a[-1] if a else 500):]

    born: dict[tuple, int] = {}
    seen_ever: set[tuple] = set()
    cuts = list(range(limit // 2, limit + 1, step))
    for cut in cuts:
        sub = all_candles[: 300 + cut]
        run = run_pipeline(Prefix(sub), symbol, timeframe, cut)
        live = {
            (e.timestamp, e.direction)
            for e in run.events
            if e.event is StructureEvent.LIQUIDITY_SWEEP and not e.provisional
        }
        seen_ever |= live
        for k in live:
            born.setdefault(k, cut)
    vanished = seen_ever - final
    res = {
        "symbol": symbol, "timeframe": timeframe.value,
        "final": len(final), "seen_ever": len(seen_ever),
        "vanished": len(vanished),
        "vanish_rate": len(vanished) / max(1, len(seen_ever)),
    }
    out.append(
        f"    {symbol:10s} {timeframe.value:>4s}  final={len(final):4d}  "
        f"vistos={len(seen_ever):4d}  sumiram={len(vanished):4d} "
        f"({res['vanish_rate']:5.1%})"
    )
    return res


# --------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframes", nargs="+", default=["15m", "1h", "4h"])
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=40, help="quantos simbolos")
    ap.add_argument("--candles", type=int, default=2400)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--causal", action="store_true",
                    help="mede a reacao a partir do candle em que o sweep "
                         "passou a existir (replay), nao do candle datado")
    ap.add_argument("--causal-candles", type=int, default=800)
    ap.add_argument("--offset", type=int, default=0,
                    help="candles apos o candle datado em que a reacao comeca "
                         "a ser medida (5 = o swing_lookback de producao)")
    ap.add_argument("--replay-step", type=int, default=25)
    args = ap.parse_args()

    global ENTRY_OFFSET
    ENTRY_OFFSET = args.offset
    tfs = [TimeFrame(t) for t in args.timeframes]
    provider = OfflineKlinesProvider()
    symbols = args.symbols or cached_symbols(tfs)[: args.limit]
    rng = random.Random(args.seed)
    out: list[str] = []

    if args.causal:
        out.append("\n[5/23] BRACO CAUSAL — reacao medida do candle CONHECIVEL")
        rows: list[tuple[str, int, float, float]] = []
        lags: list[int] = []
        for s_ in symbols:
            for tf in tfs:
                try:
                    full, born, vanished = birth_indices(
                        provider, s_, tf, args.causal_candles, args.replay_step
                    )
                except (DataProviderError, ValueError, IndexError) as exc:
                    out.append(f"    ! {s_} {tf.value}: {exc}")
                    continue
                idx = {c.timestamp: i for i, c in enumerate(full.candles)}
                for (ts, direction), birth in born.items():
                    i = idx.get(ts)
                    if i is None or full.atr <= 0:
                        continue
                    lags.append(max(0, birth - i))
                    expect_bull = direction is not BULL
                    for arm, start in (("naive", i), ("knowable", max(i, birth))):
                        if start >= len(full.candles) - 1:
                            continue
                        mfe, mae = _excursions(
                            full.candles, start, full.candles[start].close,
                            full.atr, expect_bullish=expect_bull,
                        )
                        for h in HORIZONS:
                            if h in mfe:
                                rows.append((arm, h, mfe[h], mae[h]))
                        # controle casado, ancorado no mesmo braco
                        ci = rng.randrange(50, max(51, len(full.candles) - 45))
                        cmfe, cmae = _excursions(
                            full.candles, ci, full.candles[ci].close, full.atr,
                            expect_bullish=expect_bull,
                        )
                        for h in HORIZONS:
                            if h in cmfe:
                                rows.append((f"ctrl-{arm}", h, cmfe[h], cmae[h]))
                out.append(f"    {s_:10s} {tf.value:>4s}  sweeps={len(born):4d}  "
                           f"repaint={len(vanished):3d}")
        if lags:
            out.append(f"\n    ATRASO ate ser conhecivel (candles): "
                       f"mediana={statistics.median(lags):.0f} "
                       f"p90={sorted(lags)[int(0.9*len(lags))]} "
                       f"max={max(lags)}  n={len(lags)}")
        out.append(f"\n    {'arm':>14s} {'h':>4s} {'n':>6s} {'MFE/MAE':>8s} {'MFE>MAE':>8s}")
        summ: dict = {}
        for arm in ("naive", "ctrl-naive", "knowable", "ctrl-knowable"):
            for h in HORIZONS:
                g = [(f, a) for ar, hh, f, a in rows if ar == arm and hh == h]
                if not g:
                    continue
                fin = [f / a for f, a in g if a > 0]
                med = statistics.median(fin) if fin else float("nan")
                win = sum(1 for f, a in g if f > a) / len(g)
                out.append(f"    {arm:>14s} {h:4d} {len(g):6d} {med:8.2f} {win:8.1%}")
                summ[f"{arm}/{h}"] = [len(g), med, win]
        print("\n".join(out))
        BASELINE.write_text(json.dumps({"causal": summ}, indent=2, default=str))
        return

    if args.replay:
        out.append("\n[5] CAUSALIDADE — replay por prefixo (sweeps confirmados)")
        res = []
        for s in symbols:
            for tf in tfs:
                try:
                    res.append(replay_audit(provider, s, tf, 800, args.replay_step, out))
                except (DataProviderError, ValueError, IndexError) as exc:
                    out.append(f"    ! {s} {tf.value}: {exc}")
        if res:
            tot_seen = sum(r["seen_ever"] for r in res)
            tot_van = sum(r["vanished"] for r in res)
            out.append(f"\n    TOTAL: vistos={tot_seen} sumiram={tot_van} "
                       f"({tot_van/max(1,tot_seen):.1%})")
        print("\n".join(out))
        BASELINE.write_text(json.dumps({"replay": res}, indent=2, default=str))
        return

    rows: list[Sweep] = []
    skipped: list[str] = []
    for s in symbols:
        for tf in tfs:
            try:
                run = run_pipeline(provider, s, tf, args.candles)
            except (DataProviderError, ValueError, IndexError) as exc:
                skipped.append(f"{s}/{tf.value}: {exc}")
                continue
            rows.extend(build_sweeps(run, rng))

    out.append(f"amostra: {len(symbols)} simbolos x {len(tfs)} TFs "
               f"x {args.candles} candles   (pulados: {len(skipped)})")
    summary = report(rows, out)
    print("\n".join(out))
    BASELINE.write_text(json.dumps(
        {"symbols": symbols, "timeframes": args.timeframes,
         "candles": args.candles, "summary": summary},
        indent=2, default=str))
    print(f"\nbaseline -> {BASELINE}")


if __name__ == "__main__":
    main()
