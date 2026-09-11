"""SWEEP S3 — tres sweeps em sequencia contem algo que um sweep isolado nao tem?

A observacao e visual: **tres sweeps seguidos parecem preceder uma expansao forte
na direcao da tendencia**. O S0/S1 ja mostraram que o sweep isolado nao tem edge
causal; a hipotese aqui e de *sequencia*, nao de evento.

O que este estudo NAO faz
-------------------------
Nao altera o detector, o dominio, a API, a narrativa ou a UI. Nao cria marcador.
Nao inventa threshold depois de ver o outcome. As quatro definicoes de "tres em
sequencia" (S3.0) sao **pre-registradas** aqui, no topo do arquivo, e a
PRIMARY e declarada antes de qualquer medicao.

Causalidade (S3.1/S3.2)
-----------------------
Nada e medido do timestamp back-datado. Cada evento tem um ``known_at``:

* ``LIQUIDITY_SWEEP`` -- piso analitico do S1: o pivo cujo extremo e o
  ``price_level``, mais o ``swing_lookback`` que o confirma.
* ``BOS``/``CHoCH`` -- o candle da quebra mais ``persistence_candles`` (a
  confirmacao de `_common.is_sustained_break`, 2 em producao).

A ordem da sequencia e a ordem de ``known_at``, nao a dos timestamps: uma
sequencia so e "tres sweeps" quando o **terceiro** e conhecivel, e o relogio de
outcome comeca ai. A tendencia usada e reconstruida so com eventos ja
conheciveis no candle do terceiro sweep (nunca `final_trend` do fim da serie,
que le o futuro).

Sobrevivencia (S3.4)
--------------------
Um sweep so vira "#1 de uma sequencia de tres" retrospectivamente. Por isso o
outcome de cada estagio e medido **no proprio estagio** (no #1 ninguem sabe que
havera tres), e a unica leitura tradavel e a do terceiro.

Uso
---
    poetry run python research/sweep_sequence_audit.py --limit 112
    poetry run python research/sweep_sequence_audit.py --symbols BTCUSDT --cases
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
from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.indicators import true_range_series
from research._offline import OfflineKlinesProvider, cached_symbols
from research._paginated import NoFuturesProvider
from research.sweep_audit import HORIZONS, _excursions
from research.sweep_causality import _find_extreme_index, lookback_of
from research.sweep_reclaim_audit import CLOSE_THROUGH, RECLAIM, classify

BULL = MarketDirection.BULLISH
BEAR = MarketDirection.BEARISH
NEUTRAL = MarketDirection.NEUTRAL
SWEEP = StructureEvent.LIQUIDITY_SWEEP
BOS = StructureEvent.BREAK_OF_STRUCTURE
CHOCH = StructureEvent.CHANGE_OF_CHARACTER

BASELINE = Path(__file__).parent / "sweep_s3_baseline.json"
BLOCKS = 4

#: `persistence_candles` de producao -- o quanto um BOS/CHoCH espera para
#: confirmar (`_common.is_sustained_break`). Usado como `known_at` desses
#: eventos. Se producao mudar isso, a aritmetica causal do S3 muda junto.
PERSISTENCE = 2

#: Deslocamento fixo do CONTROLE A (candle comum casado). Um ponto qualquer da
#: MESMA fita, com a MESMA direcao esperada -- a licao do `raid_reversal`.
CTRL_OFFSET = 37
CTRL_SEARCH = 120

# --------------------------------------------------------------------------
# S3.0 — as quatro definicoes, PRE-REGISTRADAS
# --------------------------------------------------------------------------

#: A run "quebra" (a contagem volta a 1) conforme a definicao:
#:
#: ``event``  EVENT_CONSECUTIVE   -- nunca quebra; qualquer trio adjacente no
#:                                   stream causal conta. E a leitura mais
#:                                   frouxa, e serve de teto.
#: ``leg``    SAME_STRUCTURAL_LEG -- quebra em qualquer BOS ou CHoCH conhecido
#:                                   entre dois sweeps (nova perna estrutural).
#: ``side``   SAME_PHYSICAL_SIDE  -- quebra quando o lado FISICO da liquidez
#:                                   muda (topo varrido vs fundo varrido),
#:                                   derivado do nivel, nao de `direction`.
#: ``trend``  TREND_CONTEXT       -- quebra quando a tendencia causal muda.
DEFS = ("event", "leg", "side", "trend")

#: A hipotese principal, declarada ANTES da analise (pedido do usuario): tres
#: sweeps consecutivos dentro da mesma tendencia estrutural, sem inversao
#: confirmada entre o primeiro e o terceiro.
PRIMARY = "trend"


def physical_side(event: MarketStructure) -> str:
    """S3.7 — o lado fisico da liquidez varrida, lido da geometria.

    O S7 mostrou que ``direction`` e a tendencia vigente invertida, nao um lado
    independente. Aqui o lado sai do preco: o pavio foi ACIMA da referencia
    (topo varrido = buy-side) ou ABAIXO (fundo varrido = sell-side).
    """
    ref = event.reference_price_level
    if ref is None:
        return "high" if event.direction is BULL else "low"
    return "high" if event.price_level >= ref else "low"


# --------------------------------------------------------------------------
# S3.1/S3.2 — disponibilidade e tendencia causal
# --------------------------------------------------------------------------


def known_at_all(
    candles: list[Candle], events: list[MarketStructure], timeframe: TimeFrame
) -> dict[int, int]:
    """``known_at`` (indice de candle) por indice de evento na lista `events`.

    Sweeps usam o piso analitico do S1 (pivo + lookback). BOS/CHoCH usam o
    candle da quebra + `PERSISTENCE`, que e o que a confirmacao sustentada
    exige. Eventos provisionais e sem candle correspondente ficam de fora.
    """
    index_of = {c.timestamp: i for i, c in enumerate(candles)}
    lb = lookback_of(timeframe)
    out: dict[int, int] = {}
    for k, event in enumerate(events):
        if event.provisional or event.event not in (SWEEP, BOS, CHOCH):
            continue
        i = index_of.get(event.timestamp)
        if i is None:
            continue
        if event.event is SWEEP:
            pivot = _find_extreme_index(
                candles, event.price_level, i, high=event.direction is BULL,
                forward=True,
            )
            out[k] = (i if pivot is None else pivot) + lb
        else:
            out[k] = i + PERSISTENCE
    return out


def causal_trend_series(
    events: list[MarketStructure], known: dict[int, int], n: int
) -> list[MarketDirection]:
    """A tendencia conhecivel em cada candle, so com eventos ja disponiveis.

    Reconstruida do stream de producao: a direcao do ultimo BOS/CHoCH cujo
    ``known_at`` ja passou. Antes do primeiro, NEUTRAL. Nunca usa `final_trend`
    (que e o estado no FIM da serie, ou seja, o futuro).
    """
    marks: list[tuple[int, MarketDirection]] = sorted(
        (known[k], events[k].direction)
        for k in known
        if events[k].event in (BOS, CHOCH) and events[k].direction is not NEUTRAL
    )
    out: list[MarketDirection] = []
    cur = NEUTRAL
    j = 0
    for i in range(n):
        while j < len(marks) and marks[j][0] <= i:
            cur = marks[j][1]
            j += 1
        out.append(cur)
    return out


# --------------------------------------------------------------------------


@dataclass
class Row:
    """Um sweep, com a sua posicao na sequencia sob cada definicao."""

    symbol: str
    timeframe: str
    timestamp: str
    idx: int
    known: int
    block: int
    direction: str
    side: str            # S3.7 — lado fisico
    klass: str           # S3.16 — RECLAIM / CLOSE_THROUGH / ...
    trend: str           # S3.2 — tendencia causal no `known_at`
    level: float | None
    #: posicao na run sob cada definicao (1, 2, 3, 4, ...)
    nth: dict[str, int] = field(default_factory=dict)
    #: gap em candles desde o sweep anterior da run PRIMARY
    gap: int | None = None
    #: span da run PRIMARY ate aqui, em candles (known do #1 -> known deste)
    span: int | None = None
    #: niveis distintos consumidos pela run PRIMARY ate aqui (S3.17)
    levels: int | None = None
    #: geometrias da run PRIMARY ate aqui, ex. "RRC" (S3.16)
    shape: str | None = None
    #: lados fisicos da run PRIMARY ate aqui, ex. "HHH" (S3.7)
    sides: str | None = None
    #: idade da perna estrutural no `known_at` (S3.11/S3.15)
    leg_age: int | None = None
    #: a run PRIMARY atravessou BOS / CHoCH? (S3.14)
    crossed_bos: bool = False
    crossed_choch: bool = False
    #: deslocamento do nivel entre #1 e este, em ATR e com sinal pro-tendencia
    level_drift_atr: float | None = None
    # --- outcome, medido do `known_at`, na direcao da TENDENCIA (S3.9) ---
    mfe: dict = field(default_factory=dict)
    mae: dict = field(default_factory=dict)
    ctrl_mfe: dict = field(default_factory=dict)
    ctrl_mae: dict = field(default_factory=dict)
    #: CONTROLE D — terceiro evento qualquer na mesma tendencia, casado por
    #: idade da perna (so preenchido para nth>=3 da PRIMARY)
    legctrl_mfe: dict = field(default_factory=dict)
    legctrl_mae: dict = field(default_factory=dict)
    #: S3.10 — alcancou N ATR a favor ANTES de 1 ATR contra / mudanca estrutural
    reach: dict = field(default_factory=dict)
    #: S3.20/S3.21 — candles ate o proximo BOS pro-tendencia / CHoCH contra
    to_bos: int | None = None
    to_choch: int | None = None
    #: S3.9 — candles ate o MFE de h=20
    time_to_mfe: int | None = None


def _reach(
    candles: list[Candle], start: int, atr: float, *, bullish: bool,
    stop_idx: int | None,
) -> dict[str, bool]:
    """S3.10 — chegou a N ATR a favor antes de 1 ATR contra (ou da estrutura).

    O limite e o que vier primeiro: 1 ATR adverso, a mudanca estrutural
    confirmada (`stop_idx`, o proximo BOS/CHoCH conhecido) ou o fim da serie.
    Descritivo: nao vira setup, nao escolhe um N vencedor.
    """
    out = {f"{k:g}ATR": False for k in (1.0, 2.0, 3.0)}
    if atr <= 0 or start + 1 >= len(candles):
        return out
    entry = candles[start].close
    end = len(candles) if stop_idx is None else min(len(candles), stop_idx + 1)
    best = 0.0
    for c in candles[start + 1: end]:
        adverse = (entry - c.low) if bullish else (c.high - entry)
        favor = (c.high - entry) if bullish else (entry - c.low)
        best = max(best, favor / atr)
        if adverse / atr >= 1.0:
            break
    for k in (1.0, 2.0, 3.0):
        out[f"{k:g}ATR"] = best >= k
    return out


def _time_to_mfe(candles: list[Candle], start: int, h: int, *, bullish: bool) -> int | None:
    window = candles[start + 1: start + 1 + h]
    if len(window) < h:
        return None
    if bullish:
        return 1 + max(range(h), key=lambda j: window[j].high)
    return 1 + max(range(h), key=lambda j: -window[j].low)


def extract(data: DashboardData) -> list[Row]:
    candles = data.candles
    events = data.internal_structure_events
    n = len(candles)
    index_of = {c.timestamp: i for i, c in enumerate(candles)}
    tr = true_range_series(candles)
    atr = statistics.fmean(tr) if tr else 0.0

    known = known_at_all(candles, events, data.timeframe)
    trend = causal_trend_series(events, known, n)

    # fronteiras de perna: todo BOS/CHoCH conhecido (S3.11/S3.14)
    breaks = sorted(known[k] for k in known if events[k].event in (BOS, CHOCH))
    chochs = sorted(known[k] for k in known if events[k].event is CHOCH)
    bos_marks = sorted(
        (known[k], events[k].direction)
        for k in known
        if events[k].event is BOS
    )
    choch_marks = sorted(
        (known[k], events[k].direction)
        for k in known
        if events[k].event is CHOCH
    )

    def leg_start(i: int) -> int:
        prev = [b for b in breaks if b <= i]
        return prev[-1] if prev else 0

    # o stream causal de sweeps: ordenado por QUANDO ficam conheciveis
    stream = sorted(
        (
            (known[k], index_of[events[k].timestamp], events[k])
            for k in known
            if events[k].event is SWEEP
        ),
        key=lambda t: (t[0], t[1]),
    )

    # --- contagem sob cada definicao (S3.0/S3.3) --------------------------
    counters: dict[str, int] = dict.fromkeys(DEFS, 0)
    prev_known: int | None = None
    prev_side: str | None = None
    prev_trend: MarketDirection | None = None
    run: list[tuple[int, int, MarketStructure, str, str]] = []  # a run PRIMARY

    rows: list[Row] = []
    for kn, i, ev in stream:
        c = candles[i]
        bullish_ev = ev.direction is BULL
        side = physical_side(ev)
        klass = classify(
            bullish=bullish_ev, high=c.high, low=c.low, close=c.close,
            level=ev.reference_price_level,
        )
        tr_now = trend[min(kn, n - 1)]

        broke_leg = prev_known is not None and any(
            prev_known < b <= kn for b in breaks
        )
        resets = {
            "event": False,
            "leg": broke_leg,
            "side": prev_side is not None and side != prev_side,
            "trend": prev_trend is not None and tr_now is not prev_trend,
        }
        for d in DEFS:
            counters[d] = 1 if resets[d] else counters[d] + 1

        if resets[PRIMARY]:
            run = []
        run.append((kn, i, ev, side, klass))

        first_kn = run[0][0]
        span = kn - first_kn
        gap = None if prev_known is None or resets[PRIMARY] else kn - prev_known
        levels = len({
            round(e.reference_price_level, 8)
            for _k, _i, e, _s, _c in run
            if e.reference_price_level is not None
        })
        shape = "".join(
            "R" if cl == RECLAIM else ("C" if cl == CLOSE_THROUGH else "?")
            for _k, _i, _e, _s, cl in run
        )
        sides = "".join("H" if s == "high" else "L" for _k, _i, _e, s, _c in run)
        crossed_bos = any(first_kn < b <= kn for b in (m for m, _d in bos_marks))
        crossed_choch = any(first_kn < b <= kn for b in chochs)

        ls = leg_start(kn)
        leg_age = kn - ls

        drift = None
        ref0 = run[0][2].reference_price_level
        if atr > 0 and ref0 is not None and ev.reference_price_level is not None:
            d = ev.reference_price_level - ref0
            if tr_now is BEAR:
                d = -d
            drift = d / atr

        # --- outcome: do `known_at`, na direcao da TENDENCIA CAUSAL --------
        start = min(kn, n - 2)
        pro_bull = tr_now is BULL
        mfe = mae = cm = ca = lm = la = {}
        reach: dict[str, bool] = {}
        t2m = None
        stop_idx = next((b for b in breaks if b > start), None)
        if tr_now is not NEUTRAL and start + 1 < n:
            mfe, mae = _excursions(
                candles, start, candles[start].close, atr, expect_bullish=pro_bull
            )
            reach = _reach(candles, start, atr, bullish=pro_bull, stop_idx=stop_idx)
            t2m = _time_to_mfe(candles, start, 20, bullish=pro_bull)
            # CONTROLE A: candle comum, mesma fita, MESMA tendencia causal
            cs = next(
                (
                    j for j in range(start + CTRL_OFFSET,
                                     min(n - 1, start + CTRL_OFFSET + CTRL_SEARCH))
                    if trend[j] is tr_now
                ),
                None,
            )
            if cs is not None:
                cm, ca = _excursions(
                    candles, cs, candles[cs].close, atr, expect_bullish=pro_bull
                )
            # CONTROLE D: mesma idade de perna, na PROXIMA perna de mesma
            # tendencia -- um "terceiro evento qualquer" casado por idade.
            nxt = next((b for b in breaks if b > kn), None)
            while nxt is not None:
                cand = nxt + leg_age
                if cand >= n - 1:
                    break
                if trend[cand] is tr_now and cand > kn:
                    lm, la = _excursions(
                        candles, cand, candles[cand].close, atr,
                        expect_bullish=pro_bull,
                    )
                    break
                nxt = next((b for b in breaks if b > nxt), None)

        to_bos = next(
            (m - kn for m, d in bos_marks if m > kn and d is tr_now), None
        )
        to_choch = next(
            (m - kn for m, d in choch_marks if m > kn and d is not tr_now
             and d is not NEUTRAL),
            None,
        )

        rows.append(
            Row(
                symbol=data.symbol, timeframe=data.timeframe.value,
                timestamp=ev.timestamp.isoformat(), idx=i, known=kn,
                block=min(BLOCKS - 1, kn * BLOCKS // max(1, n)),
                direction=ev.direction.value, side=side, klass=klass,
                trend=tr_now.value, level=ev.reference_price_level,
                nth=dict(counters), gap=gap, span=span, levels=levels,
                shape=shape, sides=sides, leg_age=leg_age,
                crossed_bos=crossed_bos, crossed_choch=crossed_choch,
                level_drift_atr=drift,
                mfe=mfe, mae=mae, ctrl_mfe=cm, ctrl_mae=ca,
                legctrl_mfe=lm, legctrl_mae=la, reach=reach,
                to_bos=to_bos, to_choch=to_choch, time_to_mfe=t2m,
            )
        )
        prev_known, prev_side, prev_trend = kn, side, tr_now
    return rows


# ---------------------------------------------------------------------------
# relatorio
# ---------------------------------------------------------------------------


def stage(row: Row, d: str = PRIMARY) -> str:
    k = row.nth.get(d, 0)
    return "4+" if k >= 4 else str(k)


def _outcome(rows: list[Row], h: int, arm: str = "") -> tuple | None:
    g = [
        (getattr(r, f"{arm}mfe")[h], getattr(r, f"{arm}mae")[h])
        for r in rows
        if h in getattr(r, f"{arm}mfe") and h in getattr(r, f"{arm}mae")
    ]
    if not g:
        return None
    fin = [x / y for x, y in g if y > 0]
    return (
        len(g),
        statistics.median(fin) if fin else float("nan"),
        sum(1 for x, y in g if x > y) / len(g),
    )


def _line(name: str, rows: list[Row], h: int, out: list[str]) -> list | None:
    a = _outcome(rows, h)
    b = _outcome(rows, h, "ctrl_")
    if a is None:
        return None
    delta = (a[2] - b[2]) if b else float("nan")
    out.append(
        f"    {name:>22s} {a[0]:6d} {a[1]:8.2f} {a[2]:8.1%} "
        f"{(b[2] if b else float('nan')):8.1%} {delta:+8.1%}"
    )
    return [name, a[0], a[1], a[2], (b[2] if b else None)]


_HEAD = (f"    {'grupo':>22s} {'n':>6s} {'MFE/MAE':>8s} {'MFE>MAE':>8s} "
         f"{'ctrlA':>8s} {'delta':>8s}")


def _pct(vals: list[float], q: float) -> float:
    if not vals:
        return float("nan")
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * len(s)))]


def report(rows: list[Row], panels: int, candles_seen: int, out: list[str]) -> dict:
    summary: dict[str, Any] = {}
    live = [r for r in rows if r.trend != "neutral"]

    # --- S3.0 / S3.2 ------------------------------------------------------
    out.append(f"\n{'='*78}\nS3.0 — O QUE 'TRES EM SEQUENCIA' DA NOS DADOS\n{'='*78}")
    out.append(f"\n  {len(rows)} sweeps confirmados; {len(live)} com tendencia "
               f"causal definida ({len(live)/max(1,len(rows)):.1%})")
    out.append(f"\n  {'definicao':>10s} {'#1':>7s} {'#2':>7s} {'#3':>7s} "
               f"{'#4+':>7s} {'>=3':>8s}")
    for d in DEFS:
        c = Counter(stage(r, d) for r in rows)
        tri = c["3"] + c["4+"]
        flag = "  <- PRIMARY" if d == PRIMARY else ""
        out.append(f"  {d:>10s} {c['1']:7d} {c['2']:7d} {c['3']:7d} "
                   f"{c['4+']:7d} {tri/max(1,len(rows)):8.1%}{flag}")
        summary.setdefault("defs", {})[d] = dict(c)

    # concordancia entre definicoes no rotulo ">=3"
    out.append("\n  concordancia do rotulo '>=3' entre definicoes")
    for d in DEFS:
        if d == PRIMARY:
            continue
        agree = sum(
            1 for r in rows
            if (r.nth[d] >= 3) == (r.nth[PRIMARY] >= 3)
        )
        out.append(f"    {PRIMARY} vs {d:>6s}: {agree/max(1,len(rows)):6.1%}")

    # --- S3.26 / S3.27 ----------------------------------------------------
    out.append(f"\n{'='*78}\nS3.26/S3.27 — FREQUENCIA\n{'='*78}")
    triples = [r for r in rows if r.nth[PRIMARY] == 3]
    per_k = 1000 * len(triples) / max(1, candles_seen)
    out.append(f"\n  {len(triples)} terceiros sweeps em {candles_seen} candles de "
               f"{panels} paineis")
    out.append(f"  {per_k:.2f} triples por 1000 candles  "
               f"({len(triples)/max(1,panels):.2f} por painel de 1200)")
    out.append(f"\n  {'corte':>12s} {'triples':>8s} {'/1000 candles':>14s}")
    by: dict[str, int] = Counter()
    for r in triples:
        by[r.timeframe] += 1
        by[r.trend] += 1
    per_tf_candles = candles_seen / max(1, len({r.timeframe for r in rows}))
    for k in sorted(by):
        out.append(f"  {k:>12s} {by[k]:8d} {1000*by[k]/max(1,per_tf_candles):14.2f}")
    summary["frequency"] = {"triples": len(triples), "per_1000": per_k,
                            "panels": panels, "candles": candles_seen}

    # quantos markers num chart de 250/500/1000 candles
    out.append("\n  markers historicos por chart (mediana por painel)")
    per_panel: dict[tuple, list[Row]] = defaultdict(list)
    for r in triples:
        per_panel[(r.symbol, r.timeframe)].append(r)
    for w in (100, 250, 500, 1000):
        counts = [
            sum(1 for r in g if r.known >= 1200 - w)
            for g in per_panel.values()
        ] or [0]
        out.append(f"    ultimos {w:>4d} candles: p50={statistics.median(counts):.0f} "
                   f"p90={_pct([float(c) for c in counts], 0.9):.0f} "
                   f"max={max(counts)}")

    # --- S3.3 / S3.12 / S3.13 --------------------------------------------
    out.append(f"\n{'='*78}\nS3.3/S3.12/S3.13 — O LADDER 1 / 2 / 3 / 4+\n{'='*78}")
    out.append("\n  outcome medido do `known_at` do PROPRIO estagio, na direcao da")
    out.append("  tendencia causal. CONTROLE A = candle comum da mesma fita, mesma")
    out.append("  tendencia, deslocado 37 candles.")
    for h in HORIZONS:
        out.append(f"\n  h={h}")
        out.append(_HEAD)
        for s in ("1", "2", "3", "4+"):
            g = [r for r in live if stage(r) == s]
            res = _line(f"#{s}", g, h, out)
            if res:
                summary.setdefault(f"ladder_h{h}", {})[s] = res

    # o salto: #3 menos #2 e #3 menos #1, por TF
    out.append(f"\n{'='*78}\nS3.5 — EXISTE DESCONTINUIDADE NO TERCEIRO?\n{'='*78}")
    out.append(f"\n  {'corte':>12s} {'h':>3s} {'#1':>8s} {'#2':>8s} {'#3':>8s} "
               f"{'#4+':>8s} {'3-2':>8s} {'3-1':>8s}")
    for cut in ("TOTAL", "15m", "1h", "4h", "bullish", "bearish"):
        for h in (10, 20, 40):
            g = [
                r for r in live
                if cut == "TOTAL" or r.timeframe == cut or r.trend == cut
            ]
            vals = {}
            for s in ("1", "2", "3", "4+"):
                o = _outcome([r for r in g if stage(r) == s], h)
                vals[s] = o[2] if o else float("nan")
            out.append(
                f"  {cut:>12s} {h:3d} {vals['1']:8.1%} {vals['2']:8.1%} "
                f"{vals['3']:8.1%} {vals['4+']:8.1%} "
                f"{vals['3']-vals['2']:+8.1%} {vals['3']-vals['1']:+8.1%}"
            )
            summary.setdefault("jump", {})[f"{cut}_h{h}"] = vals

    # --- S3.11 — CONTROLE D ----------------------------------------------
    out.append(f"\n{'='*78}\nS3.11 — CONTROLE D (mesma idade de perna, outra perna)"
               f"\n{'='*78}")
    out.append(f"\n  {'grupo':>22s} {'n':>6s} {'triple':>8s} {'ctrlD':>8s} "
               f"{'delta':>8s}")
    for s in ("2", "3", "4+"):
        g = [r for r in live if stage(r) == s]
        for h in (20,):
            a = _outcome(g, h)
            d = _outcome(g, h, "legctrl_")
            if a and d:
                out.append(f"  {f'#{s} h={h}':>22s} {min(a[0],d[0]):6d} "
                           f"{a[2]:8.1%} {d[2]:8.1%} {a[2]-d[2]:+8.1%}")
                summary.setdefault("ctrlD", {})[s] = [a[0], a[2], d[0], d[2]]

    # --- S3.10 ------------------------------------------------------------
    out.append(f"\n{'='*78}\nS3.10 — 'EXPLODE' (N ATR a favor antes de 1 ATR contra)"
               f"\n{'='*78}")
    out.append(f"\n  {'estagio':>10s} {'n':>6s} {'>=1ATR':>8s} {'>=2ATR':>8s} "
               f"{'>=3ATR':>8s} {'t->MFE20':>9s}")
    for s in ("1", "2", "3", "4+"):
        g = [r for r in live if stage(r) == s and r.reach]
        if not g:
            continue
        t = [r.time_to_mfe for r in g if r.time_to_mfe is not None]
        out.append(
            f"  {f'#{s}':>10s} {len(g):6d} "
            + "".join(f"{sum(1 for r in g if r.reach[k])/len(g):8.1%} "
                      for k in ("1ATR", "2ATR", "3ATR"))
            + f"{statistics.median(t) if t else float('nan'):9.1f}"
        )
        summary.setdefault("reach", {})[s] = [
            len(g), *[sum(1 for r in g if r.reach[k]) for k in ("1ATR", "2ATR", "3ATR")]
        ]

    # --- S3.5/S3.6 span ---------------------------------------------------
    out.append(f"\n{'='*78}\nS3.5/S3.6 — DISTANCIA E COMPACTACAO\n{'='*78}")
    gaps = [float(r.gap) for r in rows if r.gap is not None and r.nth[PRIMARY] in (2, 3)]
    spans = [float(r.span) for r in triples if r.span is not None]
    out.append(f"\n  gap entre sweeps consecutivos (candles, n={len(gaps)}): "
               f"p25={_pct(gaps,0.25):.0f} p50={_pct(gaps,0.5):.0f} "
               f"p75={_pct(gaps,0.75):.0f} p90={_pct(gaps,0.9):.0f}")
    out.append(f"  span #1->#3 (candles, n={len(spans)}): "
               f"p25={_pct(spans,0.25):.0f} p50={_pct(spans,0.5):.0f} "
               f"p75={_pct(spans,0.75):.0f} p90={_pct(spans,0.9):.0f}")
    if spans:
        edges = [_pct(spans, q) for q in (0.25, 0.5, 0.75)]
        out.append("\n  outcome por bucket NATURAL do span (h=20)")
        out.append(_HEAD)
        names = [f"span<{edges[0]:.0f}", f"{edges[0]:.0f}-{edges[1]:.0f}",
                 f"{edges[1]:.0f}-{edges[2]:.0f}", f">{edges[2]:.0f}"]
        for k, name in enumerate(names):
            lo = 0.0 if k == 0 else edges[k - 1]
            hi = edges[k] if k < 3 else float("inf")
            g = [r for r in live if stage(r) == "3" and r.span is not None
                 and lo <= r.span < hi]
            res = _line(name, g, 20, out)
            if res:
                summary.setdefault("span_buckets", {})[name] = res

    # --- S3.7 lados -------------------------------------------------------
    out.append(f"\n{'='*78}\nS3.7 — LADO FISICO DA SEQUENCIA\n{'='*78}")
    out.append(f"\n  {'padrao':>10s} {'n':>6s} {'share':>7s}")
    shapes = Counter(r.sides for r in triples)
    for k, v in shapes.most_common(8):
        out.append(f"  {k:>10s} {v:6d} {v/max(1,len(triples)):7.1%}")
    out.append("\n  outcome same-side vs mixed (h=20)")
    out.append(_HEAD)
    for name, pred in (
        ("same-side (HHH/LLL)", lambda r: r.sides in ("HHH", "LLL")),
        ("mixed", lambda r: r.sides not in ("HHH", "LLL")),
    ):
        g = [r for r in live if stage(r) == "3" and pred(r)]
        res = _line(name, g, 20, out)
        if res:
            summary.setdefault("sides", {})[name] = res

    # --- S3.8 sequencia x tendencia --------------------------------------
    out.append(f"\n{'='*78}\nS3.8 — CONFIGURACAO vs TENDENCIA\n{'='*78}")
    out.append("\n  (lado fisico do TERCEIRO contra a tendencia causal; h=20)")
    out.append(_HEAD)
    for tname in ("bullish", "bearish"):
        for sname in ("high", "low"):
            g = [r for r in live if stage(r) == "3" and r.trend == tname
                 and r.side == sname]
            res = _line(f"{tname}/{sname}", g, 20, out)
            if res:
                summary.setdefault("config", {})[f"{tname}/{sname}"] = res

    # --- S3.14/S3.11 perna ------------------------------------------------
    out.append(f"\n{'='*78}\nS3.14 — MESMA PERNA ESTRUTURAL\n{'='*78}")
    out.append(_HEAD)
    for name, pred in (
        ("A) mesma leg", lambda r: not r.crossed_bos and not r.crossed_choch),
        ("B) atravessa BOS", lambda r: r.crossed_bos and not r.crossed_choch),
        ("C) atravessa CHoCH", lambda r: r.crossed_choch),
    ):
        g = [r for r in live if stage(r) == "3" and pred(r)]
        res = _line(name, g, 20, out)
        if res:
            summary.setdefault("leg", {})[name] = res
    ages = [float(r.leg_age) for r in triples if r.leg_age is not None]
    out.append(f"\n  idade da perna no terceiro sweep: p25={_pct(ages,0.25):.0f} "
               f"p50={_pct(ages,0.5):.0f} p90={_pct(ages,0.9):.0f} candles")

    # --- S3.16/S3.17/S3.18 ------------------------------------------------
    out.append(f"\n{'='*78}\nS3.16/S3.17/S3.18 — GEOMETRIA, NIVEIS, PROGRESSAO"
               f"\n{'='*78}")
    out.append("\n  geometrias da sequencia (R=reclaim, C=close-through)")
    gshapes = Counter(r.shape for r in triples)
    for k, v in gshapes.most_common(8):
        out.append(f"    {k:>6s} {v:6d} {v/max(1,len(triples)):7.1%}")
    out.append("\n  outcome por geometria (h=20)")
    out.append(_HEAD)
    for name, pred in (
        ("RRR", lambda r: r.shape == "RRR"),
        ("CCC", lambda r: r.shape == "CCC"),
        ("mistos", lambda r: r.shape not in ("RRR", "CCC")),
    ):
        g = [r for r in live if stage(r) == "3" and pred(r)]
        res = _line(name, g, 20, out)
        if res:
            summary.setdefault("shape", {})[name] = res

    out.append("\n  niveis distintos consumidos pelo trio")
    lv = Counter(r.levels for r in triples)
    for k in sorted(x for x in lv if x is not None):
        out.append(f"    {k} nivel(is): {lv[k]:6d} ({lv[k]/max(1,len(triples)):6.1%})")
    out.append("\n  outcome por niveis distintos (h=20)")
    out.append(_HEAD)
    for k in (1, 2, 3):
        g = [r for r in live if stage(r) == "3" and r.levels == k]
        res = _line(f"{k} nivel(is)", g, 20, out)
        if res:
            summary.setdefault("levels", {})[str(k)] = res

    drifts = [r.level_drift_atr for r in triples if r.level_drift_atr is not None]
    if drifts:
        out.append("\n  S3.18 — deslocamento do nivel #1->#3, em ATR e com sinal")
        out.append(f"  PRO-tendencia (n={len(drifts)}): p25={_pct(drifts,0.25):+.2f} "
                   f"p50={_pct(drifts,0.5):+.2f} p75={_pct(drifts,0.75):+.2f}")
        out.append(f"  fracao com deslocamento pro-tendencia: "
                   f"{sum(1 for d in drifts if d > 0)/len(drifts):.1%}")

    # --- S3.20/S3.21 ------------------------------------------------------
    out.append(f"\n{'='*78}\nS3.20/S3.21 — ESTRUTURA DEPOIS\n{'='*78}")
    out.append(f"\n  {'estagio':>10s} {'n':>6s} {'BOS pro<=20':>12s} "
               f"{'CHoCH contra<=20':>17s} {'p50 ->BOS':>10s}")
    for s in ("1", "2", "3", "4+"):
        g = [r for r in live if stage(r) == s]
        if not g:
            continue
        nb = sum(1 for r in g if r.to_bos is not None and r.to_bos <= 20)
        nc = sum(1 for r in g if r.to_choch is not None and r.to_choch <= 20)
        tb = [float(r.to_bos) for r in g if r.to_bos is not None]
        out.append(f"  {f'#{s}':>10s} {len(g):6d} {nb/len(g):12.1%} "
                   f"{nc/len(g):17.1%} "
                   f"{statistics.median(tb) if tb else float('nan'):10.0f}")
        summary.setdefault("structure_after", {})[s] = [len(g), nb, nc]

    # --- S3.24 temporal / holdout ----------------------------------------
    out.append(f"\n{'='*78}\nS3.24 — ESTABILIDADE NO TEMPO\n{'='*78}")
    out.append(f"\n  {'bloco':>10s} " + " ".join(f"{f'#{s}':>8s}" for s in
                                                 ("1", "2", "3", "4+")))
    for b in range(BLOCKS):
        vals = []
        for s in ("1", "2", "3", "4+"):
            o = _outcome([r for r in live if r.block == b and stage(r) == s], 20)
            vals.append(o[2] if o else float("nan"))
        out.append(f"  {f'bloco {b}':>10s} " + " ".join(f"{v:8.1%}" for v in vals))
        summary.setdefault("blocks", {})[b] = vals
    disc = [r for r in live if r.block < 3]
    hold = [r for r in live if r.block == 3]
    out.append(f"\n  discovery (blocos 0-2, {len(disc)}) vs holdout (bloco 3, "
               f"{len(hold)}), h=20")
    out.append(_HEAD)
    for name, g in (("discovery #3", [r for r in disc if stage(r) == "3"]),
                    ("holdout #3", [r for r in hold if stage(r) == "3"])):
        res = _line(name, g, 20, out)
        if res:
            summary.setdefault("holdout", {})[name] = res

    # --- S3.25 robustez por simbolo ---------------------------------------
    out.append(f"\n{'='*78}\nS3.25 — ROBUSTEZ POR SIMBOLO\n{'='*78}")
    per_sym: dict[str, list[Row]] = defaultdict(list)
    for r in live:
        per_sym[r.symbol].append(r)
    deltas: list[tuple[str, float, int]] = []
    for sym, g in per_sym.items():
        t = [r for r in g if stage(r) == "3"]
        if len(t) < 5:
            continue
        a = _outcome(t, 20)
        b = _outcome(t, 20, "ctrl_")
        if a and b:
            deltas.append((sym, a[2] - b[2], a[0]))
    if deltas:
        vals = [d for _s, d, _n in deltas]
        better = sum(1 for v in vals if v > 0)
        out.append(f"\n  {len(per_sym)} simbolos com sweeps; {len(deltas)} com >=5 "
                   f"triples")
        out.append(f"  triple melhora o controle em {better}/{len(deltas)} "
                   f"({better/len(deltas):.1%}); piora em "
                   f"{len(deltas)-better}/{len(deltas)}")
        out.append(f"  delta por simbolo: p25={_pct(vals,0.25):+.1%} "
                   f"p50={_pct(vals,0.5):+.1%} p75={_pct(vals,0.75):+.1%}")
        top = sorted(deltas, key=lambda t2: -t2[1])
        out.append("  top 3: " + ", ".join(f"{s} {d:+.1%} (n={m})"
                                           for s, d, m in top[:3]))
        out.append("  bottom 3: " + ", ".join(f"{s} {d:+.1%} (n={m})"
                                              for s, d, m in top[-3:]))
        summary["by_symbol"] = {"n": len(deltas), "better": better,
                                "median": _pct(vals, 0.5)}

    # --- S3.13 3 vs 4+ ----------------------------------------------------
    out.append(f"\n{'='*78}\nS3.13 — 3 vs 4+ (o numero 3 e especial?)\n{'='*78}")
    out.append(f"\n  {'h':>3s} {'#3':>8s} {'#4+':>8s} {'4+ menos 3':>11s}")
    for h in HORIZONS:
        a = _outcome([r for r in live if stage(r) == "3"], h)
        b = _outcome([r for r in live if stage(r) == "4+"], h)
        if a and b:
            out.append(f"  {h:3d} {a[2]:8.1%} {b[2]:8.1%} {b[2]-a[2]:+11.1%}")
    return summary


def cases(rows: list[Row], out: list[str]) -> None:
    out.append(f"\n{'='*78}\nS3.28 — CASOS (BTC / ETH / SOL)\n{'='*78}")
    majors = {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    pool = [r for r in rows if r.symbol in majors and r.trend != "neutral"]

    def shows(r: Row) -> None:
        mfe = r.mfe.get(20)
        mae = r.mae.get(20)
        out.append(f"      {r.symbol} {r.timeframe} {r.timestamp}  "
                   f"trend {r.trend}  #{stage(r)}  [{r.klass}]")
        out.append(f"      known_at=+{r.known - r.idx} candles depois do timestamp; "
                   f"span={r.span} lados={r.sides} geom={r.shape} "
                   f"niveis={r.levels}")
        out.append(f"      h=20 MFE={mfe if mfe is None else round(mfe,2)} ATR  "
                   f"MAE={mae if mae is None else round(mae,2)} ATR  "
                   f"->BOS {r.to_bos}  ->CHoCH {r.to_choch}")

    picks = [
        ("A", "triple -> expansao forte (>=2 ATR a favor)",
         lambda r: stage(r) == "3" and r.reach.get("2ATR")),
        ("B", "triple -> falha (MAE > MFE em h=20)",
         lambda r: stage(r) == "3" and 20 in r.mfe and r.mfe[20] < r.mae[20]),
        ("C", "so dois sweeps na tendencia",
         lambda r: stage(r) == "2"),
        ("D", "quatro ou mais",
         lambda r: stage(r) == "4+"),
        ("E", "triple same-side (HHH/LLL)",
         lambda r: stage(r) == "3" and r.sides in ("HHH", "LLL")),
        ("F", "triple mixed",
         lambda r: stage(r) == "3" and r.sides not in ("HHH", "LLL")),
        ("G", "triple antes de BOS pro-tendencia (<=10)",
         lambda r: stage(r) == "3" and r.to_bos is not None and r.to_bos <= 10),
        ("H", "triple antes de CHoCH contra (<=10)",
         lambda r: stage(r) == "3" and r.to_choch is not None and r.to_choch <= 10),
    ]
    for tag, title, pred in picks:
        g = [r for r in pool if pred(r)]
        out.append(f"\n  {tag}) {title} — {len(g)} casos nos majors")
        if not g:
            out.append("      (nenhum)")
            continue
        shows(sorted(g, key=lambda x: (x.symbol, x.timestamp))[0])


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
    rows: list[Row] = []
    errors: list[str] = []
    panels = 0
    seen = 0
    for s in symbols:
        for tf in tfs:
            try:
                data = load_dashboard_data(
                    provider=provider, symbol=s, timeframe=tf, limit=args.candles,
                    compute_narrative=False, futures_provider=NoFuturesProvider(),
                )
                rows.extend(extract(data))
                panels += 1
                seen += len(data.candles)
            except Exception as exc:  # noqa: BLE001 - simbolo morto nao para o painel
                errors.append(f"{s} {tf.value}: {type(exc).__name__}: {exc}")

    out = [f"painel: {len(symbols)} simbolos x {len(tfs)} TFs x {args.candles} "
           f"candles; {panels} paineis, {len(rows)} sweeps, {len(errors)} falhas"]
    summary = report(rows, panels, seen, out)
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
