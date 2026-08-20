"""Sweep Confluence — o sweep vale mais quando cai onde EQ e OB se encaixam?

A observação sob teste
----------------------
Um ``LIQUIDITY_SWEEP`` hoje é uma categoria *residual*: um pivô contra a
tendência que rompeu o `active_<side>` trailing e não sustentou. Ele não sabe
o que varreu. A medição de auditoria mostrou que "coincide com algum pool"
é quase vazio (94% vs 89% de um pivô aleatório) — mas isso testou os canais
*isolados*. A hipótese do usuário é outra: o que vale é a **confluência**,
o sweep caindo onde uma poça de equal levels e um order block se encaixam.

O que é medido
--------------
Cada sweep não-provisional é classificado num arm por confluência:

    bare   — nem EQ nem OB no nível varrido
    eq     — só uma poça de equal highs/lows
    ob     — só um order block
    eq+ob  — os dois encaixados

Enquadramento (o de ``sweep_cluster.py``): entrada no fecho do candle do
sweep, R = entrada → extremo varrido, direção = a que a varrida argumenta
(▼ pega fundos → alta). Reporta ``held@H`` (o extremo nunca foi *fechado*
atravessado), MFE/MAE em R e ``hit 2R`` antes de −1R.

Controle casado em direção e no mesmo R (a lição de ``raid_reversal.py``),
e os arms de tier são a dose: a pergunta honesta é "o **encaixe** acrescenta
algo ao sweep nu?", não "sweep é seguido de movimento?".

**Sem lookahead**: só pools cujo ``formed_at``/``created_at`` precede o
candle do sweep entram na classificação.

Uso
---
    poetry run python research/sweep_confluence.py
    poetry run python research/sweep_confluence.py --ob-mode edge \
        --symbols BTCUSDT ETHUSDT --timeframes 15m 1h
"""

from __future__ import annotations

import argparse
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

from liquidity_hunter.app import load_dashboard_data
from liquidity_hunter.core.domain import (
    Candle,
    LiquidityZone,
    LiquidityZoneType,
    MarketDirection,
    POIZone,
    POIZoneKind,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.indicators.supertrend import true_range_series

BULL = MarketDirection.BULLISH
BEAR = MarketDirection.BEARISH

#: Só poças agrupadas contam como liquidez em repouso -- um swing isolado é um
#: pivô, não um pool (a mesma correção do `LiquidityGrab`).
_POOL_TYPES = frozenset({LiquidityZoneType.EQUAL_HIGHS, LiquidityZoneType.EQUAL_LOWS})

ARMS = ("bare", "eq", "ob", "eq+ob", "random")


@dataclass
class Ev:
    arm: str
    symbol: str
    timeframe: str
    direction: MarketDirection
    index: int
    entry: float
    extreme: float
    r: float
    held: dict[int, bool] = field(default_factory=dict)
    mfe: dict[int, float] = field(default_factory=dict)
    mae: dict[int, float] = field(default_factory=dict)
    hit_2r: bool | None = None


def _atr(candles: Sequence[Candle]) -> float:
    tr = true_range_series(list(candles))
    return statistics.fmean(tr) if tr else 0.0


def _measure(ev: Ev, candles: Sequence[Candle], horizons: Sequence[int]) -> Ev:
    bull = ev.direction is BULL
    for h in horizons:
        seg = candles[ev.index + 1 : ev.index + 1 + h]
        if len(seg) < h:
            continue
        ev.held[h] = all(
            (c.close > ev.extreme) if bull else (c.close < ev.extreme) for c in seg
        )
        best = max(c.high for c in seg) if bull else min(c.low for c in seg)
        worst = min(c.low for c in seg) if bull else max(c.high for c in seg)
        ev.mfe[h] = abs(best - ev.entry) / ev.r
        ev.mae[h] = abs(ev.entry - worst) / ev.r
    tgt = ev.entry + 2 * ev.r if bull else ev.entry - 2 * ev.r
    stop = ev.extreme
    ev.hit_2r = False
    for c in candles[ev.index + 1 :]:
        if (c.low <= stop) if bull else (c.high >= stop):
            break
        if (c.high >= tgt) if bull else (c.low <= tgt):
            ev.hit_2r = True
            break
    return ev


def _eq_hit(level: float, zones: Sequence[LiquidityZone], tol: float) -> bool:
    """Uma poça de equal levels cujo nível defendido está a `tol` do varrido."""
    for z in zones:
        edge = z.price_high if z.zone_type is LiquidityZoneType.EQUAL_HIGHS else z.price_low
        if abs(edge - level) <= tol:
            return True
    return False


def _ob_hit(
    level: float,
    obs: Sequence[POIZone],
    tol: float,
    *,
    mode: str,
    bull: bool,
) -> bool:
    """Um order block encaixado no nível varrido.

    ``inside`` — o nível cai dentro da caixa (a definição larga, que a
    auditoria mostrou ser quase vazia: as caixas cobrem meia tela).
    ``edge`` — o nível está a `tol` da *borda distante* da caixa, a que o
    sweep teria que perfurar: o fundo de uma caixa de demanda para um ▼.
    """
    for z in obs:
        # a caixa relevante é a do lado que a varrida ataca: um ▼ pega fundos,
        # logo a demanda embaixo.
        if bull and z.direction is not BULL:
            continue
        if not bull and z.direction is not BEAR:
            continue
        if mode == "inside":
            if z.price_low - tol <= level <= z.price_high + tol:
                return True
        else:
            far = z.price_low if bull else z.price_high
            if abs(far - level) <= tol:
                return True
    return False


def run_combo(
    symbol: str,
    timeframe: TimeFrame,
    *,
    limit: int,
    horizons: Sequence[int],
    min_r_atr: float,
    tol_atr: float,
    ob_mode: str,
    ob_gate: str,
    ob_kinds: frozenset[POIZoneKind],
    random_reps: int,
    rng: random.Random,
) -> list[Ev]:
    data = load_dashboard_data(symbol=symbol, timeframe=timeframe, limit=limit)
    candles = data.candles
    if len(candles) < 100:
        return []
    idx_of = {c.timestamp: i for i, c in enumerate(candles)}
    atr = _atr(candles)
    if atr <= 0:
        return []
    tol = tol_atr * atr
    min_r = min_r_atr * atr
    max_h = max(horizons)

    pools = [z for z in data.liquidity_zones if z.zone_type in _POOL_TYPES]
    obs = [z for z in data.poi_zones if z.kind in ob_kinds]

    out: list[Ev] = []
    for e in data.internal_structure_events:
        if e.provisional or e.event is not StructureEvent.LIQUIDITY_SWEEP:
            continue
        i = idx_of.get(e.timestamp)
        if i is None or i + max_h >= len(candles):
            continue
        # a varrida argumenta o lado oposto ao pavio
        bull = e.direction is BEAR
        d = BULL if bull else BEAR
        cd = candles[i]
        extreme = cd.low if bull else cd.high
        entry = cd.close
        r = abs(entry - extreme)
        if r < min_r:
            continue
        # SEM LOOKAHEAD: só o que já existia quando o sweep aconteceu
        ts = cd.timestamp
        eq = _eq_hit(extreme, [z for z in pools if z.formed_at < ts], tol)
        if ob_gate == "created":
            # A box only exists once the MSB confirms it; the anchor candle is
            # picked in hindsight, and price moving through that level before
            # confirmation broke nothing (the `liquidity_grabs` lesson). Also
            # require the box to still be on the board: FIFO retirement takes
            # it off the chart, and a level nobody is holding is not a pool.
            live = [
                z
                for z in obs
                if z.created_at < ts
                and (z.invalidated_at is None or ts <= z.invalidated_at)
            ]
        else:
            live = [z for z in obs if z.ob_candle_timestamp < ts]
        ob = _ob_hit(extreme, live, tol, mode=ob_mode, bull=bull)
        arm = "eq+ob" if eq and ob else "eq" if eq else "ob" if ob else "bare"
        out.append(
            _measure(Ev(arm, symbol, timeframe.value, d, i, entry, extreme, r),
                     candles, horizons)
        )
        for _ in range(random_reps):
            ri = rng.randrange(50, len(candles) - max_h - 1)
            e2 = candles[ri].close
            s2 = e2 - r if d is BULL else e2 + r
            out.append(
                _measure(Ev("random", symbol, timeframe.value, d, ri, e2, s2, r),
                         candles, horizons)
            )
    return out


def report(events: Sequence[Ev], horizons: Sequence[int]) -> None:
    print(f"\n{'arm':>7} {'N':>5} " + " ".join(
        f"{'held@' + str(h):>9} {'MFE@' + str(h):>8} {'MAE@' + str(h):>8}"
        for h in horizons
    ) + f" {'hit2R':>7}")
    for arm in ARMS:
        rows = [e for e in events if e.arm == arm]
        if not rows:
            continue
        cells = []
        for h in horizons:
            held = [e.held[h] for e in rows if h in e.held]
            mfe = [e.mfe[h] for e in rows if h in e.mfe]
            mae = [e.mae[h] for e in rows if h in e.mae]
            cells.append(
                f"{(sum(held) / len(held) * 100 if held else 0):>8.0f}% "
                f"{(statistics.mean(mfe) if mfe else 0):>8.2f} "
                f"{(statistics.mean(mae) if mae else 0):>8.2f}"
            )
        res = [e.hit_2r for e in rows if e.hit_2r is not None]
        hit = f"{sum(res) / len(res) * 100:>6.0f}%" if res else "     --"
        print(f"{arm:>7} {len(rows):>5} " + " ".join(cells) + f" {hit}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="+", default=[
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT",
        "AVAXUSDT", "LINKUSDT", "NEARUSDT", "AAVEUSDT", "INJUSDT", "SUIUSDT",
    ])
    p.add_argument("--timeframes", nargs="+", default=["15m", "1h", "4h"])
    p.add_argument("--limit", type=int, default=1200)
    p.add_argument("--horizons", nargs="+", type=int, default=[5, 10, 20, 40])
    p.add_argument("--min-r-atr", type=float, default=0.1)
    p.add_argument("--tol-atr", type=float, default=0.25)
    p.add_argument("--ob-mode", choices=["inside", "edge"], default="edge")
    p.add_argument("--ob-gate", choices=["created", "anchor"], default="created")
    p.add_argument("--ob-kind", choices=["order_block", "all"], default="order_block")
    p.add_argument("--random-reps", type=int, default=3)
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args()
    rng = random.Random(a.seed)
    kinds = (
        frozenset({POIZoneKind.ORDER_BLOCK})
        if a.ob_kind == "order_block"
        else frozenset(POIZoneKind)
    )

    allev: list[Ev] = []
    for s in a.symbols:
        for tf in a.timeframes:
            try:
                evs = run_combo(
                    s, TimeFrame(tf), limit=a.limit, horizons=a.horizons,
                    min_r_atr=a.min_r_atr, tol_atr=a.tol_atr, ob_mode=a.ob_mode, ob_gate=a.ob_gate,
                    ob_kinds=kinds, random_reps=a.random_reps, rng=rng,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  {s} {tf}: {exc}")
                continue
            real = [e for e in evs if e.arm != "random"]
            tiers = {arm: sum(1 for e in real if e.arm == arm) for arm in ARMS[:-1]}
            print(f"  {s} {tf}: {len(real)} sweeps {tiers}")
            allev.extend(evs)
    print(f"\nob-mode={a.ob_mode} ob-gate={a.ob_gate} ob-kind={a.ob_kind} tol={a.tol_atr} ATR")
    report(allev, a.horizons)


if __name__ == "__main__":
    main()
