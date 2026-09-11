"""SWEEP S7 — o fator `LIQUIDITY_SWEEP` da confluencia testa o lado certo?

O S1 achou um bug causal no `timestamp` do sweep; o S1.1 mediu o custo dele e
achou pouco (<4% dos eventos em todo consumidor). O unico achado que sobreviveu
ao gate de disponibilidade foi este: **`StructureConfluenceEngine` credita 9,0
pontos a qualquer sweep na janela, sem olhar de que lado ele foi**
(`structure_confluence.py:221`). Depois de corrigir a definicao de alinhamento
(S1.12), 4,9% dos creditos de BOS e 42,0% dos de CHoCH sao do lado errado.

Este estudo pergunta se esses 42% sao um bug semantico ou uma regra intencional
disfarcada, e o que custaria exigir alinhamento. **Nao altera producao.**

S7.0 — a semantica, lida do codigo e nao assumida
-------------------------------------------------
`direction` de um `LIQUIDITY_SWEEP` e **o lado que o pavio alcancou**, nao a
tendencia: `internal_structure.py:3074` emite `BULLISH` para o sweep que
atravessa um topo (`find_wick_break_index(..., bullish=True)`), e o comentario
do detector diz isso explicitamente (`internal_structure.py:1096`: "whose
`direction` is the pivot/wick side, not the trend").

O proprio engine de confluencia declara a regra que quer
(`structure_confluence.py:219`)::

    # A stop-hunt sweep within the window (any side -- a bullish reversal
    # sweeps the lows, a wick-down/bearish-labeled sweep).

"uma reversao de ALTA varre os MINIMOS, um sweep rotulado BEARISH". Logo:

===================  ====================  ==========================
quebra               sweep ALINHADO        leitura
===================  ====================  ==========================
BOS bullish          BEARISH (varreu low)  liquidez vendedora vira combustivel
BOS bearish          BULLISH (varreu high) liquidez compradora vira combustivel
CHoCH bullish        BEARISH (varreu low)  o stop-hunt que lancou a reversao
CHoCH bearish        BULLISH (varreu high) idem, espelhado
===================  ====================  ==========================

**Alinhado = direcao OPOSTA a da quebra.** Um sweep da MESMA direcao da quebra
tomou a liquidez do lado para onde o preco depois foi: e o *alvo* do movimento,
nao o combustivel dele. O engine escreve a regra no comentario e nao a testa
em lugar nenhum -- `any(sweep_lo <= s <= sweep_hi for s in sweep_idxs)` ignora
`direction` por completo.

Uso
---
    poetry run python research/sweep_directional_confluence.py --limit 112
    poetry run python research/sweep_directional_confluence.py --limit 6 --cases
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
    _FACTOR_WEIGHTS,
    _SWEEP_LOOKBACK,
    StructureConfluenceEngine,
)
from liquidity_hunter.core.domain import (
    ConfluenceFactor,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.indicators import true_range_series
from research._offline import OfflineKlinesProvider, cached_symbols
from research._paginated import NoFuturesProvider
from research.sweep_audit import HORIZONS, _excursions

BULL = MarketDirection.BULLISH
BEAR = MarketDirection.BEARISH
SWEEP = StructureEvent.LIQUIDITY_SWEEP
BOS = StructureEvent.BREAK_OF_STRUCTURE
CHOCH = StructureEvent.CHANGE_OF_CHARACTER
SWEEP_WEIGHT = _FACTOR_WEIGHTS[ConfluenceFactor.LIQUIDITY_SWEEP]
BASELINE = Path(__file__).parent / "sweep_s7_baseline.json"

#: S7.0, formalizado como funcao para ser testavel. `None` quando a direcao do
#: sweep ou da quebra nao e direcional (o detector sempre emite uma das duas,
#: mas nao se assume isso: o caso "missing direction" e contado, nao suposto).
def is_aligned(break_dir: MarketDirection, sweep_dir: MarketDirection) -> bool | None:
    if break_dir is MarketDirection.NEUTRAL or sweep_dir is MarketDirection.NEUTRAL:
        return None
    return sweep_dir is not break_dir


#: Quartis temporais para o S7.13.
BLOCKS = 4


# ---------------------------------------------------------------------------
# extracao
# ---------------------------------------------------------------------------


@dataclass
class Credit:
    """Um sweep individual creditado a um evento de estrutura."""
    symbol: str
    timeframe: str
    kind: str                 # "bos" | "choch"
    break_dir: str
    sweep_dir: str
    aligned: bool | None
    #: posicao do sweep em relacao ao candle do evento
    when: str                 # "before" | "same" | "after"
    offset: int               # sweep_idx - ev_idx
    in_forward: bool          # o sweep caiu na parte forward da janela
    #: direcao do ultimo BOS/CHoCH ATE o candle do sweep -- a tendencia vigente
    #: quando o sweep foi emitido. `None` se nao houver evento anterior.
    trend_at: str | None
    #: o sweep e contra a tendencia vigente? (o detector so emite sweep para
    #: pivos CONTRA-tendencia, entao isto deve ser ~100% -- e o teste do
    #: mecanismo, nao uma suposicao)
    counter_trend: bool | None
    block: int


@dataclass
class Event:
    """Um BOS/CHoCH qualificado, com o veredito de lado da sua janela."""
    symbol: str
    timeframe: str
    kind: str
    direction: str
    provisional: bool
    ev_idx: int
    timestamp: str
    score: float
    factors: list[str]
    n_credits: int
    n_aligned: int
    n_wrong: int
    n_missing: int
    #: classe do EVENTO: como o fator seria julgado se testasse direcao
    klass: str                # aligned | wrong_side | ambiguous | missing | none
    #: a MESMA classe, calculada so com os sweeps em `s_idx <= ev_idx` -- a
    #: unica parte da janela que uma decisao em tempo real poderia usar. Para o
    #: BOS e identica (a janela ja e para tras); para o CHoCH e o teste que
    #: separa "lado errado" de "ainda nao aconteceu".
    klass_bwd: str
    block: int
    #: coocorrencia (distancia em candles ate o sinal mais proximo, ou None)
    co_bos: int | None
    co_grab: int | None
    co_raid: int | None
    co_supertrend: int | None
    co_vsa: int | None
    mfe: dict = field(default_factory=dict)
    mae: dict = field(default_factory=dict)

    @property
    def had_sweep(self) -> bool:
        return self.n_credits > 0

    @property
    def keeps_under_directional(self) -> bool:
        """O fator sobrevive exigindo alinhamento?"""
        return self.n_aligned > 0

    def directional_score(self) -> float:
        """Score do braco DIRECTIONAL.

        O teto de `min(100, ...)` nunca morde (a soma de todos os pesos e
        exatamente 100), entao perder o fator custa sempre exatamente -9.
        """
        if self.had_sweep and not self.keeps_under_directional:
            return self.score - SWEEP_WEIGHT
        return self.score

    def n_factors_directional(self) -> int:
        lost = self.had_sweep and not self.keeps_under_directional
        return len(self.factors) - (1 if lost else 0)


def _nearest(stamps: list[int], ev_idx: int, span: int = 20) -> int | None:
    """Distancia (em candles) ate o sinal mais proximo dentro de +-`span`."""
    best: int | None = None
    for s in stamps:
        d = s - ev_idx
        if abs(d) <= span and (best is None or abs(d) < abs(best)):
            best = d
    return best


def extract(data: DashboardData) -> tuple[list[Event], list[Credit]]:
    candles = data.candles
    events = data.internal_structure_events
    idx_by_ts = {c.timestamp: i for i, c in enumerate(candles)}
    last_idx = len(candles) - 1
    n = len(candles)
    tr = true_range_series(candles)
    atr = statistics.fmean(tr) if tr else 0.0

    sweeps = [
        (idx_by_ts[e.timestamp], e)
        for e in events
        if e.event is SWEEP and e.timestamp in idx_by_ts
    ]
    flow = sorted(
        (
            (idx_by_ts[e.timestamp], e)
            for e in events
            if e.timestamp in idx_by_ts
            and e.event in (BOS, CHOCH, StructureEvent.CHOCH_FAILED)
        ),
        key=lambda pair: pair[0],
    )

    # --- fontes de coocorrencia (S7.12) ---
    bos_idxs = [i for i, e in flow if e.event is BOS]
    grab_idxs = [idx_by_ts[g.timestamp] for g in data.liquidity_grabs
                 if g.timestamp in idx_by_ts]
    st_idxs = [idx_by_ts[b.timestamp] for b in data.supertrend_breaks
               if b.timestamp in idx_by_ts]
    vsa_idxs = [idx_by_ts[s.timestamp] for s in data.volume_spread_signals
                if s.timestamp in idx_by_ts]
    raid_idxs = [
        idx_by_ts[ep.start_timestamp]
        for ep in data.liquidity_hunt_history
        if ep.start_timestamp in idx_by_ts
    ]

    by_key = {(c.event_timestamp, c.event_type): c for c in data.structure_confluence}

    out_ev: list[Event] = []
    out_cr: list[Credit] = []
    for ev in events:
        if ev.event not in (BOS, CHOCH):
            continue
        ev_idx = idx_by_ts.get(ev.timestamp)
        conf = by_key.get((ev.timestamp, ev.event))
        if ev_idx is None or conf is None:
            continue
        is_choch = ev.event is CHOCH
        if is_choch:
            origin_idx, _p = StructureConfluenceEngine._reversal_origin(
                ev, ev_idx, candles, idx_by_ts
            )
            hi = (
                ev_idx
                if ev.provisional
                else StructureConfluenceEngine._choch_forward_bound(
                    ev, ev_idx, flow, last_idx
                )
            )
            lo = origin_idx
        else:
            lo, hi = ev_idx - _SWEEP_LOOKBACK, ev_idx - 1

        block = min(BLOCKS - 1, ev_idx * BLOCKS // max(1, n))

        def _trend_at(i: int) -> MarketDirection | None:
            """Tendencia vigente em `i`: direcao do ultimo BOS/CHoCH ate ali."""
            prev = [e2 for j, e2 in flow
                    if j <= i and e2.event in (BOS, CHOCH)]
            return prev[-1].direction if prev else None

        credits = [(s, e) for s, e in sweeps if lo <= s <= hi]
        n_al = n_wr = n_ms = 0
        for s_idx, s_ev in credits:
            al = is_aligned(ev.direction, s_ev.direction)
            if al is None:
                n_ms += 1
            elif al:
                n_al += 1
            else:
                n_wr += 1
            tr_at = _trend_at(s_idx)
            out_cr.append(
                Credit(
                    symbol=data.symbol,
                    timeframe=data.timeframe.value,
                    kind="choch" if is_choch else "bos",
                    break_dir=ev.direction.value,
                    sweep_dir=s_ev.direction.value,
                    aligned=al,
                    when=("before" if s_idx < ev_idx
                          else "same" if s_idx == ev_idx else "after"),
                    offset=s_idx - ev_idx,
                    in_forward=s_idx > ev_idx,
                    trend_at=None if tr_at is None else tr_at.value,
                    counter_trend=(None if tr_at is None
                                   else s_ev.direction is not tr_at),
                    block=block,
                )
            )
        def _classify(
            group: list[tuple[int, MarketStructure]],
            break_dir: MarketDirection = ev.direction,
        ) -> str:
            a = w = m = 0
            for _s, se in group:
                r = is_aligned(break_dir, se.direction)
                if r is None:
                    m += 1
                elif r:
                    a += 1
                else:
                    w += 1
            if not group:
                return "none"
            if m and not (a or w):
                return "missing"
            if a and w:
                return "ambiguous"
            return "aligned" if a else "wrong_side"

        klass = _classify(credits)
        klass_bwd = _classify([(si, se) for si, se in credits if si <= ev_idx])

        mfe, mae = _excursions(
            candles, ev_idx, candles[ev_idx].close, atr,
            expect_bullish=ev.direction is BULL,
        )
        out_ev.append(
            Event(
                symbol=data.symbol,
                timeframe=data.timeframe.value,
                kind="choch" if is_choch else "bos",
                direction=ev.direction.value,
                provisional=bool(ev.provisional),
                ev_idx=ev_idx,
                timestamp=ev.timestamp.isoformat(),
                score=conf.score,
                factors=[f.value for f in conf.factors],
                n_credits=len(credits),
                n_aligned=n_al,
                n_wrong=n_wr,
                n_missing=n_ms,
                klass=klass,
                klass_bwd=klass_bwd,
                block=block,
                co_bos=_nearest([i for i in bos_idxs if i != ev_idx], ev_idx),
                co_grab=_nearest(grab_idxs, ev_idx),
                co_raid=_nearest(raid_idxs, ev_idx),
                co_supertrend=_nearest(st_idxs, ev_idx),
                co_vsa=_nearest(vsa_idxs, ev_idx),
                mfe=mfe,
                mae=mae,
            )
        )
    return out_ev, out_cr


# ---------------------------------------------------------------------------
# relatorio
# ---------------------------------------------------------------------------


def _pct(vals: list[float], q: float) -> float:
    if not vals:
        return float("nan")
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * len(s)))]


def _outcome(group: list[Event], h: int) -> tuple[int, float, float] | None:
    """(n, mediana de MFE/MAE, fracao MFE>MAE) no horizonte `h`."""
    g = [(e.mfe[h], e.mae[h]) for e in group if h in e.mfe and h in e.mae]
    if not g:
        return None
    fin = [f / a for f, a in g if a > 0]
    return (len(g), statistics.median(fin) if fin else float("nan"),
            sum(1 for f, a in g if f > a) / len(g))


def _outcome_block(title: str, groups: dict[str, list[Event]],
                   out: list[str], summary: dict, key: str) -> None:
    out.append(f"\n  {title}")
    out.append(f"    {'grupo':>12s} {'h':>4s} {'n':>6s} {'MFE/MAE':>8s} "
               f"{'MFE>MAE':>8s}")
    for name, g in groups.items():
        for h in HORIZONS:
            r = _outcome(g, h)
            if r is None:
                continue
            out.append(f"    {name:>12s} {h:4d} {r[0]:6d} {r[1]:8.2f} {r[2]:8.1%}")
            summary.setdefault(key, {})[f"{name}/{h}"] = list(r)


def report(evs: list[Event], crs: list[Credit], out: list[str]) -> dict:
    summary: dict[str, Any] = {}

    # --- S7.1: matriz de confusao sobre CREDITOS -------------------------
    out.append(f"\n{'='*78}\nS7.1 — MATRIZ DE CONFUSAO (creditos de sweep)\n{'='*78}")
    out.append("\n  alinhado = sweep de direcao OPOSTA a quebra (S7.0)")
    out.append(f"\n    {'corte':>14s} {'creditos':>9s} {'aligned':>9s} "
               f"{'wrong':>9s} {'missing':>8s}")
    groups: dict[str, list[Credit]] = defaultdict(list)
    for c in crs:
        groups["TOTAL"].append(c)
        groups[c.kind].append(c)
        groups[f"{c.kind}/{c.timeframe}"].append(c)
        groups[f"{c.kind}/{c.break_dir}"].append(c)
    for k in ("TOTAL", "bos", "choch",
              "bos/15m", "bos/1h", "bos/4h", "choch/15m", "choch/1h", "choch/4h",
              "bos/bullish", "bos/bearish", "choch/bullish", "choch/bearish"):
        g = groups.get(k)
        if not g:
            continue
        al = sum(1 for c in g if c.aligned is True)
        wr = sum(1 for c in g if c.aligned is False)
        ms = sum(1 for c in g if c.aligned is None)
        out.append(f"    {k:>14s} {len(g):9d} {al/len(g):9.1%} "
                   f"{wr/len(g):9.1%} {ms:8d}")
        summary.setdefault("confusion", {})[k] = [len(g), al, wr, ms]

    out.append("\n  classe do EVENTO (a janela inteira, que e o que o fator ve)")
    out.append(f"    {'corte':>14s} {'eventos':>8s} {'aligned':>8s} "
               f"{'wrong':>8s} {'ambig':>8s} {'none':>8s}")
    egroups: dict[str, list[Event]] = defaultdict(list)
    for e in evs:
        egroups["TOTAL"].append(e)
        egroups[e.kind].append(e)
    for k in ("TOTAL", "bos", "choch"):
        g = egroups.get(k)
        if not g:
            continue
        c = Counter(e.klass for e in g)
        out.append(f"    {k:>14s} {len(g):8d} {c['aligned']/len(g):8.1%} "
                   f"{c['wrong_side']/len(g):8.1%} {c['ambiguous']/len(g):8.1%} "
                   f"{c['none']/len(g):8.1%}")
        summary.setdefault("event_class", {})[k] = dict(c)

    # --- S7.2 / S7.3 / S7.4: reexecucao ----------------------------------
    out.append(f"\n{'='*78}\nS7.2/3/4 — LEGACY vs DIRECTIONAL\n{'='*78}")
    for kind, label in (("bos", "S7.3 — BOS"), ("choch", "S7.4 — CHoCH")):
        g = [e for e in evs if e.kind == kind]
        if not g:
            continue
        had = [e for e in g if e.had_sweep]
        lost = [e for e in had if not e.keeps_under_directional]
        sa = statistics.fmean([e.score for e in g])
        sb = statistics.fmean([e.directional_score() for e in g])
        out.append(f"\n  {label}: {len(g)} eventos")
        out.append(f"    com sweep na janela        : {len(had):6d} "
                   f"({len(had)/len(g):6.1%})")
        out.append(f"    PERDEM o fator (so wrong)  : {len(lost):6d} "
                   f"({len(lost)/len(g):6.1%} do total, "
                   f"{len(lost)/max(1,len(had)):.1%} dos que recebem)")
        out.append(f"    score medio {sa:6.2f} -> {sb:6.2f}  (delta {sb-sa:+.2f})")
        out.append(f"    ✦N alterado                : {len(lost):6d} "
                   f"eventos, sempre -1 fator e -9 pontos")
        dist_a = [e.score for e in g]
        out.append(f"    distribuicao do score  p25={_pct(dist_a,.25):5.1f} "
                   f"p50={statistics.median(dist_a):5.1f} "
                   f"p75={_pct(dist_a,.75):5.1f} max={max(dist_a):5.1f}")
        by: dict[str, list[Event]] = defaultdict(list)
        for e in g:
            by[e.timeframe].append(e)
            by[e.direction].append(e)
        out.append(f"    {'corte':>10s} {'n':>6s} {'c/ sweep':>9s} {'perde':>7s} {'%':>7s}")
        for k in ("15m", "1h", "4h", "bullish", "bearish"):
            gg = by.get(k)
            if not gg:
                continue
            h2 = sum(1 for e in gg if e.had_sweep)
            l2 = sum(1 for e in gg if e.had_sweep and not e.keeps_under_directional)
            out.append(f"    {k:>10s} {len(gg):6d} {h2:9d} {l2:7d} "
                       f"{l2/len(gg):7.1%}")
        summary[f"arm_{kind}"] = {
            "n": len(g), "had": len(had), "lost": len(lost),
            "score_legacy": sa, "score_directional": sb,
        }

    # --- S7.5 / S7.6 / S7.7: janela forward e timing ---------------------
    out.append(f"\n{'='*78}\nS7.5/6/7 — JANELA FORWARD E TIMING (CHoCH)\n{'='*78}")
    ch = [c for c in crs if c.kind == "choch"]
    out.append(f"\n  {len(ch)} creditos de CHoCH, por posicao relativa ao evento")
    out.append(f"    {'quando':>8s} {'creditos':>9s} {'%':>7s} "
               f"{'aligned':>9s} {'wrong':>9s}")
    for when in ("before", "same", "after"):
        g = [c for c in ch if c.when == when]
        if not g:
            continue
        al = sum(1 for c in g if c.aligned is True)
        out.append(f"    {when:>8s} {len(g):9d} {len(g)/len(ch):7.1%} "
                   f"{al/len(g):9.1%} {1-al/len(g):9.1%}")
        summary.setdefault("timing", {})[when] = [len(g), al]

    wrong = [c for c in ch if c.aligned is False]
    algn = [c for c in ch if c.aligned is True]
    if wrong and algn:
        out.append("\n  onde cada lado se concentra:")
        for name, g in (("wrong-side", wrong), ("aligned", algn)):
            cnt = Counter(c.when for c in g)
            offs = [c.offset for c in g]
            out.append(f"    {name:>11s}: before {cnt['before']/len(g):5.1%}  "
                       f"same {cnt['same']/len(g):5.1%}  "
                       f"after {cnt['after']/len(g):5.1%}   "
                       f"offset p50={statistics.median(offs):+.0f} "
                       f"p25={_pct(offs,.25):+.0f} p75={_pct(offs,.75):+.0f}")
            summary.setdefault("concentration", {})[name] = [
                len(g), cnt["before"], cnt["same"], cnt["after"],
                statistics.median(offs)]

    fwd = [c for c in ch if c.in_forward]
    bwd = [c for c in ch if not c.in_forward]
    out.append("\n  S7.5 — DENTRO da propria janela forward (o desenho), "
               "o lado ainda erra?")
    for name, g in (("forward (>ev)", fwd), ("backward (<=ev)", bwd)):
        if not g:
            continue
        al = sum(1 for c in g if c.aligned is True)
        out.append(f"    {name:>16s}: {len(g):6d} creditos, "
                   f"aligned {al/len(g):5.1%}, wrong {1-al/len(g):5.1%}")
        summary.setdefault("forward_split", {})[name] = [len(g), al]

    # --- S7.7: o mecanismo do rotulo -------------------------------------
    out.append(f"\n{'='*78}\nS7.7 — O QUE `direction` DE UM SWEEP REALMENTE "
               f"CODIFICA\n{'='*78}")
    known_tr = [c for c in crs if c.counter_trend is not None]
    if known_tr:
        ct = sum(1 for c in known_tr if c.counter_trend)
        out.append(f"\n  sweeps emitidos CONTRA a tendencia vigente: "
                   f"{ct}/{len(known_tr)} ({ct/len(known_tr):.1%})")
        out.append("  (o detector so rotula como sweep um pivo contra-tendencia:\n"
                   "   `direction` e o lado do pavio, que e o OPOSTO da tendencia\n"
                   "   no momento. Logo `direction` nao e um fato independente --\n"
                   "   e a tendencia vigente, invertida.)")
        summary["counter_trend"] = [len(known_tr), ct]

    out.append("\n  consequencia: para o CHoCH, 'alinhado' vira um proxy de "
               "'depois do flip'")
    out.append(f"    {'':>18s} {'aligned':>10s} {'wrong':>10s}")
    for when in ("before", "same", "after"):
        g = [c for c in ch if c.when == when]
        if not g:
            continue
        al = sum(1 for c in g if c.aligned is True)
        out.append(f"    {when+' do CHoCH':>18s} {al:10d} {len(g)-al:10d}")
    tbl = [[0, 0], [0, 0]]
    for c in ch:
        if c.aligned is None:
            continue
        tbl[0 if c.when == "after" else 1][0 if c.aligned else 1] += 1
    tot = sum(sum(r) for r in tbl)
    if tot:
        agree = (tbl[0][0] + tbl[1][1]) / tot
        out.append(f"\n  concordancia entre 'aligned' e 'depois do flip': "
                   f"{agree:.1%} de {tot} creditos")
        out.append("  -> os dois testes sao quase o MESMO teste. Exigir "
                   "alinhamento num CHoCH\n     e, na pratica, exigir que o "
                   "sweep seja posterior a reversao.")
        summary["alignment_is_timing"] = [tot, agree]

    # --- S7.8 / S7.9: outcome --------------------------------------------
    out.append(f"\n{'='*78}\nS7.8/9 — OUTCOME DESCRITIVO (nao e estrategia)\n{'='*78}")
    out.append("\n  MFE/MAE medidos do close do candle da quebra, na direcao "
               "da quebra.\n  Os tres grupos sao CHoCH do mesmo painel, entao "
               "symbol/TF/direcao/periodo\n  entram como estratificacao abaixo, "
               "nao como controle aleatorio.")
    chev = [e for e in evs if e.kind == "choch"]
    groups_o = {
        "aligned": [e for e in chev if e.klass == "aligned"],
        "wrong_side": [e for e in chev if e.klass == "wrong_side"],
        "ambiguous": [e for e in chev if e.klass == "ambiguous"],
        "sem sweep": [e for e in chev if e.klass == "none"],
    }
    _outcome_block("CHoCH, por classe de lado:", groups_o, out, summary, "outcome_choch")

    bosev = [e for e in evs if e.kind == "bos"]
    _outcome_block("BOS, por classe de lado:", {
        "aligned": [e for e in bosev if e.klass == "aligned"],
        "wrong_side": [e for e in bosev if e.klass == "wrong_side"],
        "sem sweep": [e for e in bosev if e.klass == "none"],
    }, out, summary, "outcome_bos")

    # O teste que decide: reclassificar so com a parte PARA TRAS da janela.
    out.append("\n  S7.8b — a MESMA comparacao usando so a parte PARA TRAS da "
               "janela\n  (a unica que uma decisao em tempo real teria; para o "
               "BOS nada muda)")
    cb = Counter(e.klass_bwd for e in chev)
    out.append("    classes do CHoCH so para tras: "
               + ", ".join(f"{k}={v} ({v/len(chev):.1%})"
                           for k, v in cb.most_common()))
    summary["choch_backward_class"] = dict(cb)
    _outcome_block("CHoCH, classe calculada so para tras:", {
        "aligned": [e for e in chev if e.klass_bwd == "aligned"],
        "wrong_side": [e for e in chev if e.klass_bwd == "wrong_side"],
        "ambiguous": [e for e in chev if e.klass_bwd == "ambiguous"],
        "sem sweep": [e for e in chev if e.klass_bwd == "none"],
    }, out, summary, "outcome_choch_bwd")

    # estratificado: dentro de cada (TF, direcao), o mesmo contraste
    out.append("\n  o mesmo contraste DENTRO de cada (TF, direcao) — h=20, "
               "MFE>MAE:")
    out.append(f"    {'estrato':>16s} {'aligned':>16s} {'wrong':>16s} "
               f"{'sem sweep':>16s}")
    for tf in ("15m", "1h", "4h"):
        for d in ("bullish", "bearish"):
            cells = []
            for klass in ("aligned", "wrong_side", "none"):
                g = [e for e in chev
                     if e.timeframe == tf and e.direction == d and e.klass == klass]
                r = _outcome(g, 20)
                cells.append(f"{r[2]:6.1%} (n={r[0]:4d})" if r else "—")
            out.append(f"    {tf+'/'+d:>16s} {cells[0]:>16s} {cells[1]:>16s} "
                       f"{cells[2]:>16s}")
            summary.setdefault("strata", {})[f"{tf}/{d}"] = cells

    # S7.9: score base (sem o fator de sweep) por grupo
    out.append("\n  S7.9 — o score dos OUTROS fatores ja difere entre os grupos?")
    out.append(f"    {'grupo':>12s} {'n':>6s} {'base (sem sweep)':>18s}")
    for name, g in groups_o.items():
        if not g:
            continue
        base = [e.score - (SWEEP_WEIGHT if e.had_sweep else 0.0) for e in g]
        out.append(f"    {name:>12s} {len(g):6d} {statistics.fmean(base):18.2f}")
        summary.setdefault("base_score", {})[name] = [
            len(g), statistics.fmean(base)]

    # --- S7.12: redundancia ----------------------------------------------
    out.append(f"\n{'='*78}\nS7.12 — REDUNDANCIA (CHoCH, ±20 candles)\n{'='*78}")
    out.append(f"\n    {'sinal':>12s} {'aligned':>10s} {'wrong_side':>12s} "
               f"{'sem sweep':>11s}")
    for name, attr in (("BOS", "co_bos"), ("grab", "co_grab"),
                       ("hunt raid", "co_raid"), ("supertrend", "co_supertrend"),
                       ("VSA", "co_vsa")):
        cells = []
        for klass in ("aligned", "wrong_side", "none"):
            g = groups_o["sem sweep"] if klass == "none" else groups_o[klass]
            if not g:
                cells.append("—")
                continue
            hit = sum(1 for e in g if getattr(e, attr) is not None)
            cells.append(f"{hit/len(g):.1%}")
        out.append(f"    {name:>12s} {cells[0]:>10s} {cells[1]:>12s} "
                   f"{cells[2]:>11s}")
        summary.setdefault("redundancy", {})[name] = cells

    # quantos fatores OUTROS o evento ja tem
    out.append("\n    fatores medios ALEM do sweep:")
    for name, g in groups_o.items():
        if not g:
            continue
        k = statistics.fmean([len(e.factors) - (1 if e.had_sweep else 0) for e in g])
        out.append(f"      {name:>12s}: {k:.2f}")

    # --- S7.13: robustez temporal ----------------------------------------
    out.append(f"\n{'='*78}\nS7.13 — ROBUSTEZ TEMPORAL (4 blocos)\n{'='*78}")
    out.append(f"\n    {'bloco':>6s} {'creditos CHoCH':>15s} {'wrong':>8s}   "
               f"{'eventos':>8s} {'perdem fator':>13s}")
    for b in range(BLOCKS):
        g = [c for c in ch if c.block == b]
        ge = [e for e in chev if e.block == b]
        if not g or not ge:
            continue
        wr = sum(1 for c in g if c.aligned is False)
        lost = sum(1 for e in ge if e.had_sweep and not e.keeps_under_directional)
        out.append(f"    {b:6d} {len(g):15d} {wr/len(g):8.1%}   "
                   f"{len(ge):8d} {lost/len(ge):13.1%}")
        summary.setdefault("blocks", {})[str(b)] = [len(g), wr, len(ge), lost]

    # --- S7.14: robustez por simbolo -------------------------------------
    out.append(f"\n{'='*78}\nS7.14 — ROBUSTEZ POR SIMBOLO\n{'='*78}")
    per: dict[str, list[Credit]] = defaultdict(list)
    for c in ch:
        per[c.symbol].append(c)
    elig = {k: v for k, v in per.items() if len(v) >= 10}
    if elig:
        rates = {k: sum(1 for c in v if c.aligned is False) / len(v)
                 for k, v in elig.items()}
        vals = sorted(rates.values())
        out.append(f"\n    {len(elig)} simbolos com >=10 creditos de CHoCH")
        out.append(f"    wrong-side por simbolo: p25={_pct(vals,.25):.1%} "
                   f"p50={statistics.median(vals):.1%} p75={_pct(vals,.75):.1%}  "
                   f"min={vals[0]:.1%} max={vals[-1]:.1%}")
        out.append(f"    simbolos com wrong-side >50%: "
                   f"{sum(1 for v in vals if v > .5)}/{len(vals)}; "
                   f">25%: {sum(1 for v in vals if v > .25)}/{len(vals)}")
        worst = sorted(rates.items(), key=lambda kv: -kv[1])[:5]
        out.append("    piores 5: " + ", ".join(f"{k} {v:.0%}" for k, v in worst))
        summary["per_symbol"] = {
            "n": len(elig), "p25": _pct(vals, .25),
            "p50": statistics.median(vals), "p75": _pct(vals, .75),
            "over_50pct": sum(1 for v in vals if v > .5),
        }
    return summary


def cases(evs: list[Event], crs: list[Credit], out: list[str]) -> None:
    """S7.15 — casos concretos em BTC/ETH/SOL, com a sequencia."""
    out.append(f"\n{'='*78}\nS7.15 — CASOS (BTC/ETH/SOL)\n{'='*78}")
    by_ev: dict[tuple, list[Credit]] = defaultdict(list)
    for c in crs:
        by_ev[(c.symbol, c.timeframe, c.kind)].append(c)
    majors = {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    wanted = [
        ("A", "CHoCH + sweep ALINHADO", lambda e: e.kind == "choch"
         and e.klass == "aligned"),
        ("B", "CHoCH + sweep WRONG-SIDE ja visivel ANTES da quebra",
         lambda e: e.kind == "choch" and e.klass_bwd == "wrong_side"),
        ("C", "wrong-side que so ocorre DEPOIS do CHoCH",
         lambda e: e.kind == "choch" and e.klass == "wrong_side"
         and e.klass_bwd == "none"),
        ("D", "wrong-side infla ✦N sem outro fator", lambda e: e.kind == "choch"
         and e.klass == "wrong_side" and len(e.factors) <= 2),
        ("E", "remover seria INCORRETO (ambiguo: tem os dois)",
         lambda e: e.kind == "choch" and e.klass == "ambiguous"),
    ]
    for tag, title, pred in wanted:
        pool = [e for e in evs if e.symbol in majors and pred(e)]
        out.append(f"\n  {tag}) {title} — {len(pool)} casos nos majors")
        if not pool:
            out.append("      (nenhum)")
            continue
        e = sorted(pool, key=lambda x: (x.symbol, x.timestamp))[0]
        out.append(f"      {e.symbol} {e.timeframe} {e.timestamp}  "
                   f"CHoCH {e.direction}  score {e.score:.0f}  "
                   f"✦{len(e.factors)} [{', '.join(e.factors) or '—'}]")
        rel = [c for c in by_ev[(e.symbol, e.timeframe, e.kind)]]
        # os creditos deste evento nao sao rastreaveis 1:1 pela chave acima;
        # o que se mostra e a composicao do proprio evento, que basta.
        out.append(f"      creditos: {e.n_credits}  "
                   f"(aligned {e.n_aligned}, wrong {e.n_wrong})")
        for h in (5, 20):
            if h in e.mfe:
                out.append(f"      h={h:<3d} MFE {e.mfe[h]:5.2f} ATR   "
                           f"MAE {e.mae[h]:5.2f} ATR")
        del rel


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
    evs: list[Event] = []
    crs: list[Credit] = []
    errors: list[str] = []
    for s in symbols:
        for tf in tfs:
            try:
                data = load_dashboard_data(
                    provider=provider, symbol=s, timeframe=tf, limit=args.candles,
                    futures_provider=NoFuturesProvider(),
                )
                e, c = extract(data)
                evs.extend(e)
                crs.extend(c)
            except Exception as exc:  # noqa: BLE001 - um simbolo morto nao para o painel
                errors.append(f"{s} {tf.value}: {type(exc).__name__}: {exc}")

    out = [f"painel: {len(symbols)} simbolos x {len(tfs)} TFs x {args.candles} "
           f"candles; {len(evs)} eventos, {len(crs)} creditos, "
           f"{len(errors)} falhas"]
    summary = report(evs, crs, out)
    if args.cases:
        cases(evs, crs, out)
    print("\n".join(out))
    if errors:
        print(f"\nfalhas ({len(errors)}):")
        for e in errors[:10]:
            print(f"  ! {e}")
    Path(args.json).write_text(json.dumps(summary, indent=1, default=str))


if __name__ == "__main__":
    main()
