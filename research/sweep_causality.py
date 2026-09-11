"""SWEEP S1 — quando o sweep passa a existir, e o que o timestamp representa.

O S0 fechou com o achado de que o edge do `LIQUIDITY_SWEEP` desaparece quando
a reacao e medida a partir do momento em que o evento realmente existe. Este
estudo responde *por que* o atraso e o que ha de errado com o timestamp.

Tres tempos, medidos separadamente para cada sweep:

* ``level_time``          -- o candle que formou o nivel varrido
  (`reference_price_level`);
* ``sweep_candle_time``   -- o candle que fisicamente atravessou o nivel. **E o
  timestamp que o evento carrega hoje** (`find_wick_break_index`);
* ``known_at``            -- o primeiro prefixo da serie em que a pipeline de
  producao afirma que o evento existe (replay incremental).

Modos
-----
``--times``      (padrao) decomposicao analitica do atraso, painel completo.
``--replay``     known_at exato por replay de prefixo (passo 1), subconjunto.
``--consumers``  quem consome sweep cedo demais (confluence) e de que lado.

Uso
---
    poetry run python research/sweep_causality.py --limit 112
    poetry run python research/sweep_causality.py --replay --limit 12
    poetry run python research/sweep_causality.py --consumers --limit 112
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from liquidity_hunter.app.dashboard_data import (
    _DEFAULT_INTERNAL_PARAMS,
    _INTERNAL_STRUCTURE_PARAMS,
)
from liquidity_hunter.app.structure_confluence import (
    _SWEEP_LOOKBACK,
    StructureConfluenceEngine,
)
from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.core.domain.enums import TIMEFRAME_PERIOD
from liquidity_hunter.data.exceptions import DataProviderError
from research._offline import OfflineKlinesProvider, cached_symbols
from research.sweep_audit import HORIZONS, Run, _excursions, run_pipeline

BULL = MarketDirection.BULLISH
BASELINE = Path(__file__).parent / "sweep_s1_baseline.json"

#: Identidade fisica de um sweep atraves de prefixos. O `price_level` e o
#: extremo do pivo -- um high/low de candle, exato em float e imutavel -- entao
#: serve de ancora para detectar que o *timestamp* mudou sem confundir isso com
#: "sumiu e nasceu outro".
Ident = tuple[MarketDirection, float]


def lookback_of(timeframe: TimeFrame) -> int:
    return _INTERNAL_STRUCTURE_PARAMS.get(timeframe, _DEFAULT_INTERNAL_PARAMS)[0]


# --------------------------------------------------------------------------
# S1.0 / S1.1 / S1.9 — decomposicao analitica
# --------------------------------------------------------------------------


@dataclass
class Times:
    symbol: str
    timeframe: str
    direction: str
    sweep_idx: int
    #: candle cujo extremo e o `price_level` do evento: o pivo que disparou a
    #: emissao. `None` quando nao ha correspondencia exata.
    pivot_idx: int | None
    #: candle cujo extremo formou o `reference_price_level`.
    level_idx: int | None
    lookback: int

    @property
    def backdate(self) -> int | None:
        """Quantos candles o evento foi datado ANTES do pivo que o disparou."""
        return None if self.pivot_idx is None else self.pivot_idx - self.sweep_idx

    @property
    def pivot_confirm_idx(self) -> int | None:
        """O pivo so existe `lookback` candles depois de se formar."""
        return None if self.pivot_idx is None else self.pivot_idx + self.lookback

    @property
    def analytic_known_idx(self) -> int | None:
        """Piso analitico do `known_at`: o pivo confirmado.

        E um **limite inferior**: a maquina de estados pode demorar mais. O
        replay mede o valor real.
        """
        return self.pivot_confirm_idx

    @property
    def level_known_idx(self) -> int | None:
        """Quando o proprio nivel varrido passou a existir (pivo + lookback)."""
        return None if self.level_idx is None else self.level_idx + self.lookback

    @property
    def level_known_before_sweep(self) -> bool | None:
        """S1.9: o nivel ja era conhecido quando o candle o atravessou?"""
        k = self.level_known_idx
        return None if k is None else k <= self.sweep_idx


def _find_extreme_index(
    candles: list[Candle], price: float, start: int, *, high: bool, forward: bool
) -> int | None:
    """Indice do candle cujo high/low e exatamente `price`."""
    rng = range(start, len(candles)) if forward else range(start, -1, -1)
    for i in rng:
        if (candles[i].high if high else candles[i].low) == price:
            return i
    return None


def measure_times(run: Run) -> list[Times]:
    index_of = {c.timestamp: i for i, c in enumerate(run.candles)}
    lb = lookback_of(run.timeframe)
    rows: list[Times] = []
    for event in run.events:
        if event.event is not StructureEvent.LIQUIDITY_SWEEP or event.provisional:
            continue
        i = index_of.get(event.timestamp)
        if i is None:
            continue
        bullish = event.direction is BULL
        pivot_idx = _find_extreme_index(
            run.candles, event.price_level, i, high=bullish, forward=True
        )
        level_idx = (
            None
            if event.reference_price_level is None
            else _find_extreme_index(
                run.candles, event.reference_price_level, i, high=bullish, forward=False
            )
        )
        rows.append(
            Times(
                symbol=run.symbol,
                timeframe=run.timeframe.value,
                direction=event.direction.value,
                sweep_idx=i,
                pivot_idx=pivot_idx,
                level_idx=level_idx,
                lookback=lb,
            )
        )
    return rows


def _pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


def report_times(rows: list[Times], out: list[str]) -> dict:
    summary: dict = {}
    out.append(f"\n{'='*78}\nS1 — TRES TEMPOS  ({len(rows)} sweeps confirmados)\n{'='*78}")

    resolved = [r for r in rows if r.pivot_idx is not None]
    out.append(f"\n[S1.0] pivo localizado por casamento exato de extremo: "
               f"{len(resolved)}/{len(rows)} ({len(resolved)/max(1,len(rows)):.1%})")

    # --- S1.1: decomposicao do atraso ---
    back = [r.backdate for r in resolved]
    out.append("\n[S1.1] DECOMPOSICAO DO ATRASO (candles)")
    out.append("    delay_analitico = back-dating (pivo - timestamp) + lookback")
    for label, vals in (
        ("back-dating", back),
        ("lookback (fixo)", [r.lookback for r in resolved]),
        ("piso analitico total", [r.analytic_known_idx - r.sweep_idx for r in resolved]),
    ):
        if not vals:
            continue
        out.append(
            f"    {label:22s} p50={statistics.median(vals):6.0f} "
            f"p75={_pct(vals, .75):6.0f} p90={_pct(vals, .90):6.0f} "
            f"max={max(vals):6.0f}  media={statistics.fmean(vals):6.1f}"
        )
    summary["backdate"] = {
        "p50": statistics.median(back) if back else None,
        "p90": _pct(back, 0.90),
        "max": max(back) if back else None,
    }

    out.append("\n    histograma do back-dating (candles entre o timestamp e o pivo)")
    hist = Counter(min(b, 20) for b in back)
    for k in sorted(hist):
        tag = "20+" if k == 20 else str(k)
        out.append(f"      {tag:>4s}: {hist[k]:6d}  ({hist[k]/max(1,len(back)):6.1%})")
    summary["backdate_hist"] = {str(k): v for k, v in hist.items()}

    # --- S1.9: o nivel ja era conhecido? ---
    out.append("\n[S1.9] O NIVEL VARRIDO JA ERA CONHECIVEL NO CANDLE DO SWEEP?")
    known = [r for r in rows if r.level_known_before_sweep is True]
    future = [r for r in rows if r.level_known_before_sweep is False]
    nomatch = [r for r in rows if r.level_known_before_sweep is None]
    tot = max(1, len(rows))
    out.append(f"    A) nivel ja conhecido  : {len(known):6d}  ({len(known)/tot:6.1%})")
    out.append(f"    B) nivel so depois     : {len(future):6d}  ({len(future)/tot:6.1%})")
    out.append(f"    -) nivel nao localizado: {len(nomatch):6d}  ({len(nomatch)/tot:6.1%})")
    summary["level_known"] = {
        "already": len(known), "future": len(future), "unmatched": len(nomatch)
    }
    if future:
        lag = [r.level_known_idx - r.sweep_idx for r in future]
        out.append(f"       quando so depois, o nivel demora p50={statistics.median(lag):.0f} "
                   f"p90={_pct(lag, .9):.0f} candles alem do sweep")

    # --- S1.5: retroatividade por TF/direcao ---
    out.append("\n[S1.5] RETROATIVIDADE (piso analitico; o real so pode ser maior)")
    out.append(f"    {'corte':>12s} {'n':>6s} {'retroativos':>12s} "
               f"{'p50':>5s} {'p75':>5s} {'p90':>5s} {'max':>6s}")
    groups: dict[str, list[Times]] = defaultdict(list)
    for r in resolved:
        groups["TOTAL"].append(r)
        groups[r.timeframe].append(r)
        groups[r.direction].append(r)
    for key in ("TOTAL", "15m", "1h", "4h", "bullish", "bearish"):
        g = groups.get(key)
        if not g:
            continue
        d = [r.analytic_known_idx - r.sweep_idx for r in g]
        retro = sum(1 for x in d if x > 0)
        out.append(f"    {key:>12s} {len(g):6d} {retro/len(g):11.1%} "
                   f"{statistics.median(d):5.0f} {_pct(d,.75):5.0f} "
                   f"{_pct(d,.90):5.0f} {max(d):6.0f}")
        summary.setdefault("retro", {})[key] = [len(g), retro / len(g),
                                                statistics.median(d), max(d)]

    # por simbolo: a retroatividade e universal, entao o que varia e a
    # MAGNITUDE do atraso, nao a existencia dele.
    by_sym: dict[str, list[int]] = defaultdict(list)
    for r in resolved:
        by_sym[r.symbol].append(r.analytic_known_idx - r.sweep_idx)
    eligible = {k: v for k, v in by_sym.items() if len(v) >= 20}
    if eligible:
        meds = {k: statistics.median(v) for k, v in eligible.items()}
        vals = sorted(meds.values())
        always = sum(
            1 for v in eligible.values() if all(x > 0 for x in v)
        )
        out.append(
            f"\n    por simbolo (n>=20): {len(eligible)} simbolos; "
            f"{always} com 100% dos sweeps retroativos"
        )
        out.append(
            f"      mediana do atraso: min={vals[0]:.0f} p50={statistics.median(vals):.0f} "
            f"max={vals[-1]:.0f}"
        )
        worst = sorted(meds.items(), key=lambda kv: -kv[1])[:5]
        out.append("      piores 5 (mediana de candles): "
                   + ", ".join(f"{k} {v:.0f}" for k, v in worst))
        summary["per_symbol_delay"] = {
            "n": len(eligible),
            "all_retro": always,
            "median_of_medians": statistics.median(vals),
            "max": vals[-1],
        }

    # tempo real, nao so candles
    out.append("\n    o mesmo atraso em tempo de relogio (mediana):")
    for tf in ("15m", "1h", "4h"):
        g = groups.get(tf)
        if not g:
            continue
        med = statistics.median([r.analytic_known_idx - r.sweep_idx for r in g])
        period = TIMEFRAME_PERIOD[TimeFrame(tf)]
        out.append(f"      {tf:>4s}: {med:.0f} candles = {med * period}")
    return summary


# --------------------------------------------------------------------------
# S1.2 / S1.3 / S1.10 — replay exato
# --------------------------------------------------------------------------


class _Prefix(OfflineKlinesProvider):
    """Serve um prefixo ja em memoria (janela expansiva: o inicio e fixo)."""

    max_fetch_limit = 100_000

    def __init__(self, candles: list[Candle]) -> None:
        self._candles = candles

    def get_ohlcv(self, symbol=None, timeframe=None, limit: int = 500):  # noqa: ANN001
        return self._candles[-limit:]


@dataclass
class Life:
    """Tudo o que o replay viu sobre uma identidade fisica de sweep."""

    first_cut: int
    stamps: set[datetime]
    refs: set[float]
    events: set[StructureEvent]
    last_cut: int
    gaps: int = 0  # quantas vezes sumiu depois de ja ter aparecido


def replay(
    provider, symbol: str, timeframe: TimeFrame, limit: int, step: int, buffer: int = 300
) -> tuple[Run, dict[Ident, Life], int]:
    full = run_pipeline(provider, symbol, timeframe, limit)
    all_candles = provider.get_ohlcv(symbol, timeframe, limit + buffer)
    lives: dict[Ident, Life] = {}
    for cut in range(limit // 2, limit + 1, step):
        run = run_pipeline(_Prefix(all_candles[: buffer + cut]), symbol, timeframe, cut)
        idx = cut - 1  # indice, na serie final, do ultimo candle deste prefixo
        seen_now: set[Ident] = set()
        for e in run.events:
            if e.event is not StructureEvent.LIQUIDITY_SWEEP or e.provisional:
                continue
            ident: Ident = (e.direction, e.price_level)
            seen_now.add(ident)
            life = lives.get(ident)
            if life is None:
                lives[ident] = Life(
                    first_cut=idx,
                    stamps={e.timestamp},
                    refs={e.reference_price_level or 0.0},
                    events={e.event},
                    last_cut=idx,
                )
            else:
                if life.last_cut < idx - step:
                    life.gaps += 1
                life.stamps.add(e.timestamp)
                life.refs.add(e.reference_price_level or 0.0)
                life.events.add(e.event)
                life.last_cut = idx
    return full, lives, limit // 2


def report_replay(
    results: list[tuple[Run, dict[Ident, Life], int]], step: int, out: list[str]
) -> dict:
    summary: dict = {}
    final_all: list[tuple[Run, Ident, Life]] = []
    seen_total = 0
    #: Indice do primeiro prefixo, por serie. Um evento datado ANTES dele ja
    #: existia quando a janela abriu: seu nascimento nao foi observado, so
    #: limitado por cima. Contar esses como "nasceu no primeiro prefixo" infla
    #: o atraso -- e a censura a esquerda que separa o p50 (confiavel) dos
    #: percentis altos (nao).
    first_cut_of: dict[int, int] = {}
    for run, lives, opened in results:
        first_cut_of[id(run)] = opened
        final = {
            (e.direction, e.price_level)
            for e in run.events
            if e.event is StructureEvent.LIQUIDITY_SWEEP and not e.provisional
        }
        seen_total += len(lives)
        for ident, life in lives.items():
            if ident in final:
                final_all.append((run, ident, life))

    out.append(f"\n{'='*78}\nS1.2/S1.3 — REPLAY EXATO (passo {step})\n{'='*78}")
    vanished = seen_total - len(final_all)
    out.append(f"\n    identidades vistas em algum prefixo : {seen_total}")
    out.append(f"    sobreviveram ate a serie final      : {len(final_all)}")
    out.append(f"    apareceram e sumiram (repaint)      : {vanished} "
               f"({vanished/max(1,seen_total):.2%})")
    summary["seen"] = seen_total
    summary["final"] = len(final_all)
    summary["vanished"] = vanished

    # --- S1.3: estabilidade depois de conhecido ---
    moved_ts = sum(1 for _r, _i, life in final_all if len(life.stamps) > 1)
    moved_ref = sum(1 for _r, _i, life in final_all if len(life.refs) > 1)
    flickered = sum(1 for _r, _i, life in final_all if life.gaps > 0)
    n = max(1, len(final_all))
    out.append("\n[S1.3] DEPOIS DE CONHECIDO, O EVENTO PERMANECE IDENTICO?")
    out.append(f"    A) delayed mas estavel        : {n - moved_ts - moved_ref - flickered:6d}")
    out.append(f"    B) piscou (sumiu e voltou)    : {flickered:6d}  ({flickered/n:6.2%})")
    out.append(f"    C) timestamp foi reescrito    : {moved_ts:6d}  ({moved_ts/n:6.2%})")
    out.append(f"    C) reference_price reescrito  : {moved_ref:6d}  ({moved_ref/n:6.2%})")
    out.append("    direcao/tipo reescritos       : 0  (fazem parte da identidade)")
    summary["stability"] = {"moved_ts": moved_ts, "moved_ref": moved_ref,
                            "flickered": flickered, "n": len(final_all)}

    # --- known_at real vs timestamp ---
    delays: list[int] = []
    residuals: list[int] = []
    floors: list[int] = []
    censored = 0
    per: dict[str, list[int]] = defaultdict(list)
    # Piso analitico por (serie, timestamp): separa o que o back-dating e o
    # lookback explicam do que sobra para a maquina de estados.
    analytic: dict[tuple[int, datetime], int] = {}
    for run, _ident, _life in final_all:
        key = id(run)
        if any(k[0] == key for k in analytic):
            continue
        for t in measure_times(run):
            if t.analytic_known_idx is not None:
                analytic[(key, run.candles[t.sweep_idx].timestamp)] = (
                    t.analytic_known_idx - t.sweep_idx
                )
    for run, ident, life in final_all:
        index_of = {c.timestamp: i for i, c in enumerate(run.candles)}
        ts = min(life.stamps)
        i = index_of.get(ts)
        if i is None:
            continue
        if i < first_cut_of.get(id(run), 0):
            censored += 1
            continue
        d = max(0, life.first_cut - i)
        delays.append(d)
        floor = analytic.get((id(run), ts))
        if floor is not None:
            floors.append(floor)
            residuals.append(d - floor)
        per[run.timeframe.value].append(d)
        per[ident[0].value].append(d)
    if delays:
        out.append(
            f"\n[S1.5-replay] known_at REAL menos o timestamp (candles)\n"
            f"    {censored} eventos datados antes da abertura da janela foram "
            f"descartados\n    (censura a esquerda: o nascimento deles nao foi "
            f"observado)"
        )
        out.append(f"    {'corte':>10s} {'n':>6s} {'retro':>7s} {'p50':>5s} "
                   f"{'p75':>5s} {'p90':>5s} {'max':>6s}")
        for key in ["TOTAL", *sorted(per)]:
            d = delays if key == "TOTAL" else per[key]
            retro = sum(1 for x in d if x > 0) / max(1, len(d))
            out.append(f"    {key:>10s} {len(d):6d} {retro:6.1%} "
                       f"{statistics.median(d):5.0f} {_pct(d,.75):5.0f} "
                       f"{_pct(d,.90):5.0f} {max(d):6.0f}")
            summary.setdefault("known_delay", {})[key] = [
                len(d), retro, statistics.median(d), max(d)
            ]

    if residuals:
        out.append(f"\n[S1.1] DE ONDE VEM O ATRASO REAL (candles, n={len(residuals)})")
        for label, vals in (
            ("piso analitico (back-dating + lookback)", floors),
            ("residuo da maquina de estados", residuals),
            ("known_at - timestamp (total)", delays),
        ):
            out.append(
                f"    {label:40s} p50={statistics.median(vals):5.0f} "
                f"p75={_pct(vals, .75):5.0f} p90={_pct(vals, .90):5.0f} "
                f"max={max(vals):6.0f}"
            )
        summary["delay_decomposition"] = {
            "floor_p50": statistics.median(floors),
            "residual_p50": statistics.median(residuals),
            "total_p50": statistics.median(delays),
            "residual_p90": _pct(residuals, 0.90),
        }

    # --- S1.10: baseline causal ---
    out.append("\n[S1.10] BASELINE CAUSAL — reacao medida a partir de known_at")
    arms: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    for run, ident, life in final_all:
        index_of = {c.timestamp: i for i, c in enumerate(run.candles)}
        i = index_of.get(min(life.stamps))
        if i is None or run.atr <= 0 or i < first_cut_of.get(id(run), 0):
            continue
        expect_bull = ident[0] is not BULL
        for arm, start in (("timestamp", i), ("known_at", max(i, life.first_cut))):
            if start >= len(run.candles) - 1:
                continue
            mfe, mae = _excursions(
                run.candles, start, run.candles[start].close, run.atr,
                expect_bullish=expect_bull,
            )
            for h in HORIZONS:
                if h in mfe:
                    arms[arm].append((h, mfe[h], mae[h]))
    out.append(f"    {'arm':>10s} {'h':>4s} {'n':>6s} {'MFE/MAE':>8s} {'MFE>MAE':>8s}")
    for arm in ("timestamp", "known_at"):
        for h in HORIZONS:
            g = [(f, a) for hh, f, a in arms[arm] if hh == h]
            if not g:
                continue
            fin = [f / a for f, a in g if a > 0]
            med = statistics.median(fin) if fin else float("nan")
            win = sum(1 for f, a in g if f > a) / len(g)
            out.append(f"    {arm:>10s} {h:4d} {len(g):6d} {med:8.2f} {win:8.1%}")
            summary.setdefault("causal", {})[f"{arm}/{h}"] = [len(g), med, win]
    return summary


# --------------------------------------------------------------------------
# S1.11 / S1.12 — consumidores
# --------------------------------------------------------------------------


def consumers(run: Run, times: list[Times], out_rows: list[dict]) -> None:
    """Confluencia: sweep usado antes de existir, e sweep do lado errado.

    A janela e o predicado sao os do proprio `StructureConfluenceEngine`
    (constantes e helpers importados, nao reescritos); o que este modulo
    acrescenta e o `known_at` de cada sweep, que o engine nao tem.
    """
    candles = run.candles
    index_of = {c.timestamp: i for i, c in enumerate(candles)}
    last_idx = len(candles) - 1
    known_by_idx: dict[int, int] = {}
    for t in times:
        if t.analytic_known_idx is not None:
            known_by_idx[t.sweep_idx] = t.analytic_known_idx

    sweeps = [
        (index_of[e.timestamp], e)
        for e in run.events
        if e.event is StructureEvent.LIQUIDITY_SWEEP
        and not e.provisional
        and e.timestamp in index_of
    ]
    flow = sorted(
        (
            (index_of[e.timestamp], e)
            for e in run.events
            if e.timestamp in index_of
            and e.event
            in (
                StructureEvent.BREAK_OF_STRUCTURE,
                StructureEvent.CHANGE_OF_CHARACTER,
                StructureEvent.CHOCH_FAILED,
            )
        ),
        key=lambda pair: pair[0],
    )

    # S1.11: o sweep cai do lado errado de um evento estrutural? Se um
    # BOS/CHoCH acontece entre o candle que data o sweep e o candle em que o
    # sweep passa a existir, entao o sweep *parece* ter precedido esse evento
    # quando na verdade so foi conhecido depois dele. E exatamente a condicao
    # que `LiquidityHuntEngine._swept_since` (timestamp >= flip) e o
    # fatiamento de episodios por `grab_ts` leem errado.
    flip_idxs = [
        i
        for i, e in flow
        if e.event in (StructureEvent.BREAK_OF_STRUCTURE,
                       StructureEvent.CHANGE_OF_CHARACTER)
    ]
    for s_idx, _e in sweeps:
        k = known_by_idx.get(s_idx)
        if k is None:
            continue
        out_rows.append(
            {
                "symbol": run.symbol,
                "timeframe": run.timeframe.value,
                "event": "_sweep_side_of_flip",
                "crossed_flip": any(s_idx < f <= k for f in flip_idxs),
            }
        )

    for ev_idx, ev in flow:
        if ev.event is StructureEvent.CHOCH_FAILED:
            continue
        is_choch = ev.event is StructureEvent.CHANGE_OF_CHARACTER
        if is_choch:
            origin_idx, _p = StructureConfluenceEngine._reversal_origin(
                ev, ev_idx, candles, index_of
            )
            fwd = (
                ev_idx
                if ev.provisional
                else StructureConfluenceEngine._choch_forward_bound(
                    ev, ev_idx, flow, last_idx
                )
            )
            lo, hi = origin_idx, fwd
        else:
            lo, hi = ev_idx - _SWEEP_LOOKBACK, ev_idx - 1
        credits = [(s, e) for s, e in sweeps if lo <= s <= hi]
        if not credits:
            continue
        # Qual e o lado *semanticamente correto*? O proprio comentario do
        # engine diz: "a bullish reversal sweeps the lows, a wick-down /
        # bearish-labeled sweep". Como `direction` de um sweep e o lado que o
        # pavio alcancou, o sweep que ALIMENTA uma quebra de alta e o
        # bearish (tomou minimas) -- ou seja, alinhado = direcao OPOSTA a da
        # quebra. Um sweep da MESMA direcao da quebra tomou liquidez do lado
        # para onde o preco depois foi: nao e combustivel, e o alvo.
        aligned = any(e.direction is not ev.direction for _s, e in credits)
        same_side_only = not aligned
        # anacronismo: o sweep so passou a existir depois do proprio evento
        anachronistic = all(
            known_by_idx.get(s, s) > ev_idx for s, _e in credits
        )
        out_rows.append(
            {
                "symbol": run.symbol,
                "timeframe": run.timeframe.value,
                "event": ev.event.value,
                "is_choch": is_choch,
                "credits": len(credits),
                # o fator so e creditado pelo lado "certo" se algum sweep da
                # janela tem a mesma direcao do rompimento
                "wrong_side_only": same_side_only,
                "has_aligned": aligned,
                "anachronistic": anachronistic,
                "forward_window": is_choch and hi > ev_idx,
            }
        )


def report_consumers(all_rows: list[dict], out: list[str]) -> dict:
    out.append(f"\n{'='*78}\nS1.11/S1.12 — CONSUMIDORES\n{'='*78}")
    side = [r for r in all_rows if r["event"] == "_sweep_side_of_flip"]
    rows = [r for r in all_rows if r["event"] != "_sweep_side_of_flip"]
    if side:
        crossed = sum(1 for r in side if r["crossed_flip"])
        out.append(
            f"\n    sweeps datados ANTES de um BOS/CHoCH que so existiram "
            f"DEPOIS dele:\n      {crossed}/{len(side)} ({crossed/len(side):.1%}) "
            f"-- o caso que `_swept_since` e o fatiamento de\n      episodios do "
            f"HUNT leem do lado errado da perna"
        )
    n = max(1, len(rows))
    out.append(f"\n    eventos BOS/CHoCH que recebem credito de sweep: {len(rows)}")
    for label, key in (
        ("credito SO de sweep do mesmo lado da quebra (S7)", "wrong_side_only"),
        ("sweep so existia DEPOIS do evento (S1)", "anachronistic"),
        ("janela olha para FRENTE (CHoCH)", "forward_window"),
    ):
        hit = sum(1 for r in rows if r[key])
        out.append(f"    {label:42s}: {hit:6d}  ({hit/n:6.1%})")
    out.append("\n    (alinhado = sweep de direcao OPOSTA a quebra, o que o "
               "comentario do\n     engine descreve como combustivel; o engine "
               "nao testa direcao nenhuma)")
    out.append("\n    por tipo de evento:")
    for kind in ("break_of_structure", "change_of_character"):
        g = [r for r in rows if r["event"] == kind]
        if not g:
            continue
        w = sum(1 for r in g if r["wrong_side_only"]) / len(g)
        a = sum(1 for r in g if r["anachronistic"]) / len(g)
        out.append(f"      {kind:22s} n={len(g):6d}  lado errado={w:6.1%}  "
                   f"anacronico={a:6.1%}")
    return {
        "sweep_crossed_flip": sum(1 for r in side if r["crossed_flip"]),
        "sweep_side_n": len(side),
        "n": len(rows),
        "wrong_side_only": sum(1 for r in rows if r["wrong_side_only"]),
        "anachronistic": sum(1 for r in rows if r["anachronistic"]),
        "forward_window": sum(1 for r in rows if r["forward_window"]),
    }


# --------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframes", nargs="+", default=["15m", "1h", "4h"])
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=112)
    ap.add_argument("--candles", type=int, default=2400)
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--consumers", action="store_true")
    ap.add_argument("--replay-candles", type=int, default=800)
    ap.add_argument("--step", type=int, default=1)
    args = ap.parse_args()

    tfs = [TimeFrame(t) for t in args.timeframes]
    provider = OfflineKlinesProvider()
    symbols = args.symbols or cached_symbols(tfs)[: args.limit]
    out: list[str] = []
    summary: dict = {}

    if args.replay:
        results: list[tuple[Run, dict[Ident, Life], int]] = []
        for s in symbols:
            for tf in tfs:
                try:
                    results.append(
                        replay(provider, s, tf, args.replay_candles, args.step)
                    )
                except (DataProviderError, ValueError, IndexError) as exc:
                    out.append(f"    ! {s} {tf.value}: {exc}")
        out.insert(0, f"replay: {len(symbols)} simbolos x {len(tfs)} TFs "
                      f"x {args.replay_candles} candles, passo {args.step}")
        summary["replay"] = report_replay(results, args.step, out)
    else:
        rows: list[Times] = []
        crows: list[dict] = []
        for s in symbols:
            for tf in tfs:
                try:
                    run = run_pipeline(provider, s, tf, args.candles)
                except (DataProviderError, ValueError, IndexError) as exc:
                    out.append(f"    ! {s} {tf.value}: {exc}")
                    continue
                t = measure_times(run)
                rows.extend(t)
                if args.consumers:
                    consumers(run, t, crows)
        out.insert(0, f"amostra: {len(symbols)} simbolos x {len(tfs)} TFs "
                      f"x {args.candles} candles")
        summary["times"] = report_times(rows, out)
        if args.consumers:
            summary["consumers"] = report_consumers(crows, out)

    print("\n".join(out))
    BASELINE.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nbaseline -> {BASELINE}")


if __name__ == "__main__":
    main()
