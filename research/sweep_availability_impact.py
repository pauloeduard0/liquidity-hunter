"""SWEEP S1.1 — o que muda quando o sweep so pode ser usado depois de existir.

O S1 fechou com dois fatos. O `timestamp` de um `LIQUIDITY_SWEEP` e retroativo
em 100% dos casos (mediana 6 candles, ate 68 no H4), e o stream e *estavel*:
97,6% dos eventos nascem tarde e nunca mais mudam. O problema, portanto, nao e
repaint -- e **latencia com data falsa**, e quem paga sao os consumidores que
leem o evento como se ele existisse no candle que ele carimba.

Este estudo mede o tamanho dessa conta, consumidor por consumidor, **sem tocar
em producao**: nada de `known_at` no dominio, nada de mexer no `timestamp`,
nada de mexer no detector. O `known_at` e reconstruido aqui, do lado de fora,
e os consumidores reais sao reexecutados com e sem o gate.

O gate
------
Um sweep so participa de uma decisao avaliada em `T` se::

    known_at(sweep) <= T

`known_at` e o piso analitico do S1 -- o pivo que disparou a emissao, mais o
`swing_lookback` que o confirma. O replay exato do S1 mediu o residual da
maquina de estados sobre esse piso em **0 candles no p50 e no p90**, ou seja,
o piso *e* o valor real na esmagadora maioria dos casos; onde nao for, este
estudo subestima o impacto, nunca o exagera.

Consumidores medidos
--------------------
1. ``structure_confluence``  -- fator `LIQUIDITY_SWEEP`, peso 9,0 (prioridade)
2. S7 -- direcao do sweep creditado, ja no stream causal
3. ``LiquidityHuntEngine``   -- `_collect_capture_signals` e `_swept_since`
4. ``OIRegimeAnalyzer``      -- `FLUSH` e exclusivo de sweep
5. VWAP ancorada no ultimo sweep (`_build_anchored_vwaps`)

O consumidor 4 original, ``ManipulationCycleDetector``, saiu do codigo em
2026-09-11 (camada aposentada por falta de medicao); o braco que o media foi
removido daqui e a numeracao dos painies seguintes desceu de um.

Nao se mede edge aqui. A pergunta e correcao causal e impacto funcional.

Uso
---
    poetry run python research/sweep_availability_impact.py --limit 112
    poetry run python research/sweep_availability_impact.py --limit 8 --json /tmp/x
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from liquidity_hunter.app.dashboard_data import (
    _HUNT_PROXIMITY_ATR,
    _VWAP_MIN_ANCHOR_CANDLES,
    DashboardData,
    load_dashboard_data,
)
from liquidity_hunter.app.liquidity_hunt import LiquidityHuntEngine
from liquidity_hunter.app.structure_confluence import (
    _FACTOR_WEIGHTS,
    _SWEEP_LOOKBACK,
    StructureConfluenceEngine,
)
from liquidity_hunter.core.domain import (
    Candle,
    ConfluenceFactor,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.indicators import anchored_vwap
from research._offline import OfflineKlinesProvider, cached_symbols
from research._paginated import NoFuturesProvider
from research.sweep_causality import _find_extreme_index, lookback_of

BULL = MarketDirection.BULLISH
SWEEP = StructureEvent.LIQUIDITY_SWEEP
BOS = StructureEvent.BREAK_OF_STRUCTURE
CHOCH = StructureEvent.CHANGE_OF_CHARACTER
SWEEP_WEIGHT = _FACTOR_WEIGHTS[ConfluenceFactor.LIQUIDITY_SWEEP]
BASELINE = Path(__file__).parent / "sweep_s1_1_baseline.json"

#: Cortes de score usados para a pergunta "quantos cruzam um limiar". O projeto
#: **nao tem** um limiar de confluencia -- o unico consumo visivel e a contagem
#: de fatores (`✦N` no `MainChart`). Estes cortes sao portanto uma convencao
#: deste relatorio, declarada aqui, e nao uma regra de producao.
THRESHOLDS = (30.0, 40.0, 50.0, 60.0)


# ---------------------------------------------------------------------------
# known_at
# ---------------------------------------------------------------------------


def known_at_map(
    candles: list[Candle], events: list[MarketStructure], timeframe: TimeFrame
) -> dict[datetime, int]:
    """``known_at`` (indice de candle) de cada `LIQUIDITY_SWEEP` confirmado.

    Piso analitico do S1: o pivo cujo extremo e o `price_level` do evento, mais
    o `swing_lookback` que o confirma. Sweeps provisionais ficam de fora --
    eles ja se anunciam como nao-confirmados, e o S1 mediu apenas confirmados.
    """
    index_of = {c.timestamp: i for i, c in enumerate(candles)}
    lb = lookback_of(timeframe)
    out: dict[datetime, int] = {}
    for event in events:
        if event.event is not SWEEP or event.provisional:
            continue
        i = index_of.get(event.timestamp)
        if i is None:
            continue
        pivot = _find_extreme_index(
            candles, event.price_level, i, high=event.direction is BULL, forward=True
        )
        # Sem casamento exato de extremo o pivo nao e localizavel; o S1 mediu
        # isso em 0,0% dos sweeps. Cai no piso conservador (lookback puro).
        out[event.timestamp] = (i if pivot is None else pivot) + lb
    return out


def gated_events(
    events: list[MarketStructure], known: dict[datetime, int], upto: int
) -> list[MarketStructure]:
    """`events` sem os sweeps que ainda nao seriam conheciveis no candle `upto`.

    **Invariante (S1.1-10):** nenhum sweep com ``known_at > upto`` sobrevive.
    Nada alem de sweeps e removido: o gate e sobre disponibilidade do sweep,
    nao sobre o resto do stream.
    """
    return [
        e
        for e in events
        if not (e.event is SWEEP and known.get(e.timestamp, -1) > upto)
    ]


# ---------------------------------------------------------------------------
# 1 + 2 — structure_confluence e S7
# ---------------------------------------------------------------------------


@dataclass
class ConfRow:
    symbol: str
    timeframe: str
    kind: str            # "bos" | "choch"
    direction: str
    provisional: bool
    score: float         # LEGACY, do engine de producao
    n_factors: int
    had_sweep: bool      # o fator entrou no LEGACY
    #: credito sobrevive exigindo known_at <= candle da quebra
    causal_at_break: bool
    #: credito sobrevive exigindo known_at <= fim da janela (CHoCH forward)
    causal_at_window: bool
    forward_window: bool  # a janela do CHoCH olha para frente do evento
    #: S7, no stream causal: existe sweep de direcao OPOSTA a quebra?
    aligned_legacy: bool | None
    #: S7 exigindo known_at <= candle da quebra
    aligned_causal: bool | None
    #: S7 exigindo known_at <= fim da janela (respeita o forward do CHoCH)
    aligned_causal_window: bool | None

    @property
    def lost_at_break(self) -> bool:
        return self.had_sweep and not self.causal_at_break

    @property
    def lost_at_window(self) -> bool:
        return self.had_sweep and not self.causal_at_window

    def causal_score(self, *, at_window: bool) -> float:
        """Score sem o fator de sweep quando ele nao sobrevive ao gate.

        O teto de 100 nunca morde: a soma de TODOS os pesos e exatamente 100,
        entao remover 9,0 nunca esbarra no `min`.
        """
        lost = self.lost_at_window if at_window else self.lost_at_break
        return self.score - SWEEP_WEIGHT if lost else self.score


def confluence_rows(data: DashboardData, known: dict[datetime, int]) -> list[ConfRow]:
    candles, events = data.candles, data.internal_structure_events
    idx_by_ts = {c.timestamp: i for i, c in enumerate(candles)}
    last_idx = len(candles) - 1

    # Mesmo predicado do engine (`structure_confluence.py:101`): TODO sweep
    # indexavel entra, provisional inclusive.
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
    by_key = {
        (c.event_timestamp, c.event_type): c for c in data.structure_confluence
    }

    rows: list[ConfRow] = []
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

        credits = [(s, e) for s, e in sweeps if lo <= s <= hi]
        # Um sweep provisional nao tem known_at medido; o S1 so mediu
        # confirmados. Sem medida, nao se afirma indisponibilidade: ele passa.
        def _known(ts: datetime, s_idx: int) -> int:
            return known.get(ts, s_idx)

        at_break = [
            (s, e) for s, e in credits if _known(e.timestamp, s) <= ev_idx
        ]
        at_window = [
            (s, e) for s, e in credits if _known(e.timestamp, s) <= hi
        ]

        def _aligned(
            group: list[tuple[int, MarketStructure]],
            break_dir: MarketDirection = ev.direction,
        ) -> bool | None:
            # S7 com a definicao corrigida do S1: alinhado = sweep de direcao
            # OPOSTA a da quebra (tomou a liquidez que ALIMENTA a ruptura).
            if not group:
                return None
            return any(e.direction is not break_dir for _s, e in group)

        rows.append(
            ConfRow(
                symbol=data.symbol,
                timeframe=data.timeframe.value,
                kind="choch" if is_choch else "bos",
                direction=ev.direction.value,
                provisional=bool(ev.provisional),
                score=conf.score,
                n_factors=len(conf.factors),
                had_sweep=ConfluenceFactor.LIQUIDITY_SWEEP in conf.factors,
                causal_at_break=bool(at_break),
                causal_at_window=bool(at_window),
                forward_window=is_choch and hi > ev_idx,
                aligned_legacy=_aligned(credits),
                aligned_causal=_aligned(at_break),
                aligned_causal_window=_aligned(at_window),
            )
        )
    return rows


# ---------------------------------------------------------------------------
# 3 — HUNT
# ---------------------------------------------------------------------------


class CausalHuntEngine(LiquidityHuntEngine):
    """`LiquidityHuntEngine` com o gate de disponibilidade do sweep.

    Duas leituras, e so elas: `_collect_capture_signals` (um sweep so conta na
    janela de captura se ja era conhecido no fim dela) e `_swept_since` (idem,
    contra o candle vivo). Nenhuma outra regra do engine muda.
    """

    def __init__(self, *a: Any, known: dict[datetime, int], candles: list[Candle],
                 **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._known = known
        self._idx = {c.timestamp: i for i, c in enumerate(candles)}
        self._last = len(candles) - 1

    def _available(self, ts: datetime, upto: int) -> bool:
        return self._known.get(ts, self._idx.get(ts, upto)) <= upto

    def _collect_capture_signals(  # type: ignore[override]
        self, data: DashboardData, hunted_short: bool,
        capture_direction: MarketDirection, start: datetime, end: datetime,
        allow_raid: bool = True,
    ) -> list[tuple[datetime, float, str]]:
        signals = super()._collect_capture_signals(
            data, hunted_short, capture_direction, start, end, allow_raid
        )
        end_idx = self._idx.get(end, self._last)
        return [
            s for s in signals
            if s[2] != "sweep" or self._available(s[0], end_idx)
        ]

    def _swept_since(  # type: ignore[override]
        self, events: list[MarketStructure],
        capture_direction: MarketDirection, flip_timestamp: datetime,
    ) -> bool:
        return any(
            e.event is SWEEP
            and e.direction is capture_direction
            and e.timestamp >= flip_timestamp
            and self._available(e.timestamp, self._last)
            for e in events
        )


def _dump(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, list):
        return [o.model_dump(mode="json") for o in obj]
    return obj.model_dump(mode="json")


def hunt_impact(data: DashboardData, known: dict[datetime, int]) -> dict:
    legacy = LiquidityHuntEngine(proximity_atr=_HUNT_PROXIMITY_ATR)
    causal = CausalHuntEngine(
        proximity_atr=_HUNT_PROXIMITY_ATR, known=known, candles=data.candles
    )
    out: dict[str, Any] = {}
    for name, fn in (
        ("state", lambda e: _dump(e.build(data))),
        ("history", lambda e: _dump(e.build_history(data))),
        ("continuation", lambda e: _dump(e.build_continuation_history(data))),
    ):
        a, b = fn(legacy), fn(causal)
        out[name] = _diff_payload(a, b)
    return out


#: Campos que identificam uma linha de saida atraves dos dois bracos. O diff
#: precisa de uma identidade ESTAVEL: sem ela, duas listas de tamanhos
#: diferentes viram "tudo mudou" (ou, pior, colapsam numa chave so).
_IDENTITY = ("start_timestamp", "hunted_side", "sweep_timestamp", "phase")


def _key(row: dict) -> tuple:
    return tuple(row.get(k) for k in _IDENTITY)


def _diff_payload(a: Any, b: Any) -> dict:
    """Diferenca entre duas saidas serializadas do mesmo consumidor."""
    if isinstance(a, list):
        ka = {_key(x): x for x in a}
        kb = {_key(x): x for x in b}
        common = ka.keys() & kb.keys()
        fields: Counter = Counter()
        for k in common:
            for f in ka[k]:
                if ka[k].get(f) != kb[k].get(f):
                    fields[f] += 1
        return {
            "n_legacy": len(a), "n_causal": len(b),
            "removed": len(ka.keys() - kb.keys()),
            "added": len(kb.keys() - ka.keys()),
            "changed": sum(1 for k in common if ka[k] != kb[k]),
            "fields": dict(fields),
        }
    if a is None and b is None:
        return {"n_legacy": 0, "n_causal": 0, "removed": 0, "added": 0,
                "changed": 0, "fields": {}}
    if a is None or b is None:
        return {"n_legacy": int(a is not None), "n_causal": int(b is not None),
                "removed": int(b is None), "added": int(a is None),
                "changed": 0, "fields": {"__existence__": 1}}
    fields = {k: 1 for k in a if a.get(k) != b.get(k)}
    return {"n_legacy": 1, "n_causal": 1, "removed": 0, "added": 0,
            "changed": int(bool(fields)), "fields": fields}


# ---------------------------------------------------------------------------
# 4 — OI regime (FLUSH)
# ---------------------------------------------------------------------------


def oi_impact(data: DashboardData, known: dict[datetime, int]) -> dict:
    """`FLUSH` e exclusivo de `LIQUIDITY_SWEEP` (`oi_regime.py:227`).

    Sem futuros no cache offline nao ha `oi_analysis` para diffar, mas o que
    decide aqui nao precisa dele: o rotulo FLUSH e uma *requalificacao do
    proprio evento de sweep*, no candle do sweep. Logo a disponibilidade de um
    FLUSH e, por construcao, identica a do sweep que o origina -- e o S1 mediu
    essa disponibilidade em 100% retroativa. O que se mede aqui e o tamanho
    dessa heranca, e o alcance dela (quantos sweeps sao elegiveis a FLUSH).
    """
    idx = {c.timestamp: i for i, c in enumerate(data.candles)}
    lags = [
        known[e.timestamp] - idx[e.timestamp]
        for e in data.internal_structure_events
        if e.event is SWEEP and e.timestamp in known and e.timestamp in idx
    ]
    return {
        "flush_eligible": len(lags),
        "lag_candles": lags,
        "has_oi": data.oi_analysis is not None,
    }


# ---------------------------------------------------------------------------
# 5 — VWAP ancorada no ultimo sweep
# ---------------------------------------------------------------------------


def vwap_impact(data: DashboardData, known: dict[datetime, int]) -> dict | None:
    """A ancora de sweep escolhida muda quando so os conhecidos sao elegiveis?

    Reproduz `_build_anchored_vwaps` para o ramo de sweep (o ramo de CHoCH nao
    consome sweep), nos dois bracos, e compara a serie que sai do
    `anchored_vwap` de producao.
    """
    candles, events = data.candles, data.internal_structure_events
    if len(candles) <= _VWAP_MIN_ANCHOR_CANDLES:
        return None
    idx = {c.timestamp: i for i, c in enumerate(candles)}
    last = len(candles) - 1
    cutoff = candles[-_VWAP_MIN_ANCHOR_CANDLES].timestamp

    def latest(evs: list[MarketStructure]) -> MarketStructure | None:
        for e in reversed(evs):
            if e.provisional or e.event is not SWEEP or e.timestamp > cutoff:
                continue
            return e
        return None

    a = latest(events)
    b = latest(gated_events(events, known, last))
    if a is None:
        return None
    early = known.get(a.timestamp, idx[a.timestamp]) - idx[a.timestamp]
    row: dict[str, Any] = {
        "anchor_changed": (b is None or b.timestamp != a.timestamp),
        "causal_anchor_missing": b is None,
        "early_candles": early,
        "candles_shown_early": max(0, min(early, last - idx[a.timestamp])),
    }
    if b is not None and b.timestamp != a.timestamp:
        sa = anchored_vwap(candles, a.timestamp, symbol=data.symbol,
                           timeframe=data.timeframe, label="a")
        sb = anchored_vwap(candles, b.timestamp, symbol=data.symbol,
                           timeframe=data.timeframe, label="b")
        if sa and sb and sa.points and sb.points:
            pa, pb = sa.points[-1], sb.points[-1]
            px = candles[-1].close
            row["vwap_diff_pct"] = abs(pa.vwap - pb.vwap) / px * 100 if px else None
            wa = pa.upper_band - pa.vwap
            wb = pb.upper_band - pb.vwap
            row["sigma_ratio"] = (wb / wa) if wa else None
            row["anchor_shift_candles"] = idx[a.timestamp] - idx[b.timestamp]
    return row


# ---------------------------------------------------------------------------
# painel
# ---------------------------------------------------------------------------


@dataclass
class Panel:
    conf: list[ConfRow] = field(default_factory=list)
    hunt: list[dict] = field(default_factory=list)
    oi: list[dict] = field(default_factory=list)
    vwap: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def measure(provider: Any, symbol: str, tf: TimeFrame, limit: int,
            panel: Panel) -> None:
    data = load_dashboard_data(
        provider=provider, symbol=symbol, timeframe=tf, limit=limit,
        futures_provider=NoFuturesProvider(),
    )
    if not data.candles:
        return
    known = known_at_map(data.candles, data.internal_structure_events, tf)
    tag = {"symbol": symbol, "timeframe": tf.value}
    panel.conf.extend(confluence_rows(data, known))
    panel.hunt.append({**tag, **hunt_impact(data, known)})
    panel.oi.append({**tag, **oi_impact(data, known)})
    v = vwap_impact(data, known)
    if v is not None:
        panel.vwap.append({**tag, **v})


def _p(vals: list[float], q: float) -> float:
    if not vals:
        return float("nan")
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * len(s)))]


def report(panel: Panel, out: list[str]) -> dict:
    summary: dict[str, Any] = {}
    conf = panel.conf

    # --- 1. confluencia ---------------------------------------------------
    out.append(f"\n{'='*78}\nS1.1-1 — STRUCTURE_CONFLUENCE (fator peso "
               f"{SWEEP_WEIGHT:.0f})\n{'='*78}")
    for kind, label in (("bos", "BOS"), ("choch", "CHoCH")):
        g = [r for r in conf if r.kind == kind]
        if not g:
            continue
        had = [r for r in g if r.had_sweep]
        lost_b = [r for r in g if r.lost_at_break]
        lost_w = [r for r in g if r.lost_at_window]
        out.append(f"\n  {label}: {len(g)} eventos qualificados")
        out.append(f"    recebem o fator no LEGACY        : {len(had):6d} "
                   f"({len(had)/len(g):6.1%})")
        out.append(f"    PERDEM exigindo known_at<=quebra : {len(lost_b):6d} "
                   f"({len(lost_b)/len(g):6.1%} do total, "
                   f"{len(lost_b)/max(1,len(had)):.1%} dos que recebem)")
        if kind == "choch":
            fwd = [r for r in g if r.forward_window]
            out.append(f"    janela olha para FRENTE (desenho): {len(fwd):6d} "
                       f"({len(fwd)/len(g):6.1%})")
            out.append(f"    PERDEM exigindo known_at<=janela : {len(lost_w):6d} "
                       f"({len(lost_w)/len(g):6.1%}) <- anacronismo PURO, "
                       f"fora do uso forward intencional")
        scores_a = [r.score for r in g]
        at_window = kind == "choch"

        def key(r: ConfRow, w: bool = at_window) -> float:
            return r.causal_score(at_window=w)

        scores_b = [key(r) for r in g]
        out.append(f"    score medio  legacy={statistics.fmean(scores_a):6.2f}  "
                   f"causal={statistics.fmean(scores_b):6.2f}  "
                   f"delta={statistics.fmean(scores_b)-statistics.fmean(scores_a):+6.2f}")
        lost = lost_w if kind == "choch" else lost_b
        out.append(f"    eventos com score alterado       : {len(lost):6d} "
                   f"({len(lost)/len(g):6.1%}), sempre -9 e -1 fator (✦N→✦N-1)")
        out.append("    cruzam limiar (convencao deste relatorio):")
        for t in THRESHOLDS:
            fa = sum(1 for r in g if r.score >= t)
            fb = sum(1 for r in g if key(r) >= t)
            out.append(f"      >={t:4.0f}: legacy {fa:5d}  causal {fb:5d}  "
                       f"deixam de cruzar {fa-fb:4d} ({(fa-fb)/max(1,fa):5.1%} "
                       f"dos que cruzavam)")
        summary[f"conf_{kind}"] = {
            "n": len(g), "had_sweep": len(had), "lost_at_break": len(lost_b),
            "lost_at_window": len(lost_w),
            "score_legacy": statistics.fmean(scores_a),
            "score_causal": statistics.fmean(scores_b),
            "thresholds": {str(t): [sum(1 for r in g if r.score >= t),
                                    sum(1 for r in g if key(r) >= t)]
                           for t in THRESHOLDS},
        }

    # por TF e por direcao
    out.append("\n  impacto por timeframe e direcao (perda do fator / eventos)")
    out.append(f"    {'corte':>10s} {'n':>7s} {'legacy c/ sweep':>16s} "
               f"{'perde':>7s} {'%':>7s}")
    groups: dict[str, list[ConfRow]] = defaultdict(list)
    for r in conf:
        groups["TOTAL"].append(r)
        groups[r.timeframe].append(r)
        groups[r.direction].append(r)
        groups[f"{r.kind}/{r.timeframe}"].append(r)
    for k in ("TOTAL", "15m", "1h", "4h", "bullish", "bearish",
              "bos/15m", "bos/1h", "bos/4h", "choch/15m", "choch/1h", "choch/4h"):
        g = groups.get(k)
        if not g:
            continue
        had = sum(1 for r in g if r.had_sweep)
        lost = sum(1 for r in g
                   if (r.lost_at_window if r.kind == "choch" else r.lost_at_break))
        out.append(f"    {k:>10s} {len(g):7d} {had:16d} {lost:7d} "
                   f"{lost/max(1,len(g)):7.1%}")
        summary.setdefault("by_cut", {})[k] = [len(g), had, lost]

    # --- 2. S7 ------------------------------------------------------------
    out.append(f"\n{'='*78}\nS1.1-2 — S7 DIRECAO (alinhado = sweep de direcao "
               f"OPOSTA a quebra)\n{'='*78}")
    out.append("\n    (para o CHoCH o braco que decide e causal@janela: a janela\n"
               "     forward e DESENHO, nao bug -- S1.12)")
    out.append(f"\n    {'':>8s} {'braco':>13s} {'creditados':>11s} "
               f"{'alinhado':>9s} {'lado errado':>12s}")
    for kind in ("bos", "choch"):
        for arm, attr in (("legacy", "aligned_legacy"),
                          ("causal@quebra", "aligned_causal"),
                          ("causal@janela", "aligned_causal_window")):
            g = [r for r in conf
                 if r.kind == kind and getattr(r, attr) is not None]
            if not g:
                continue
            al = sum(1 for r in g if getattr(r, attr))
            out.append(f"    {kind:>8s} {arm:>13s} {len(g):11d} "
                       f"{al/len(g):9.1%} {1-al/len(g):12.1%}")
            summary.setdefault("s7", {})[f"{kind}/{arm}"] = [len(g), al]

    # --- 3. HUNT ----------------------------------------------------------
    out.append(f"\n{'='*78}\nS1.1-3 — LIQUIDITY HUNT\n{'='*78}")
    for stream in ("state", "history", "continuation"):
        tot = Counter()
        fields: Counter = Counter()
        for row in panel.hunt:
            d = row[stream]
            for k in ("n_legacy", "n_causal", "removed", "added", "changed"):
                tot[k] += d.get(k, 0)
            for f in d.get("fields", {}):
                fields[f] += 1
        n = max(1, tot["n_legacy"])
        out.append(f"\n  {stream}: legacy {tot['n_legacy']}  causal "
                   f"{tot['n_causal']}  removidos {tot['removed']}  "
                   f"adicionados {tot['added']}  alterados {tot['changed']} "
                   f"({(tot['removed']+tot['changed'])/n:.1%})")
        if fields:
            out.append("    campos que mudam: " + ", ".join(
                f"{k}({v})" for k, v in fields.most_common(8)))
        summary.setdefault("hunt", {})[stream] = dict(tot)

    # --- 4. OI ------------------------------------------------------------
    out.append(f"\n{'='*78}\nS1.1-4 — OI REGIME (FLUSH)\n{'='*78}")
    lags = [x for row in panel.oi for x in row["lag_candles"]]
    out.append(f"\n  sweeps elegiveis a FLUSH: {len(lags)}  "
               f"(FLUSH so existe sobre LIQUIDITY_SWEEP)")
    if lags:
        out.append(f"  atraso herdado do sweep: p50={statistics.median(lags):.0f} "
                   f"p90={_p(lags,.9):.0f} max={max(lags)} candles — "
                   f"100% dos FLUSH sao datados antes de poderem existir")
    out.append("  conteudo (o oi_delta lido no candle do sweep): INALTERADO "
               "sob modelo A")
    summary["oi"] = {"eligible": len(lags),
                     "lag_p50": statistics.median(lags) if lags else None,
                     "lag_max": max(lags) if lags else None}

    # --- 5. VWAP ----------------------------------------------------------
    out.append(f"\n{'='*78}\nS1.1-5 — VWAP ANCORADA NO ULTIMO SWEEP\n{'='*78}")
    v = panel.vwap
    if v:
        ch = [r for r in v if r["anchor_changed"]]
        miss = [r for r in v if r["causal_anchor_missing"]]
        early = [r["candles_shown_early"] for r in v]
        out.append(f"\n  paineis com ancora de sweep: {len(v)}")
        out.append(f"    ancora MUDA sob o gate     : {len(ch):5d} "
                   f"({len(ch)/len(v):.1%})")
        out.append(f"      dos quais some por completo: {len(miss)}")
        out.append(f"    candles desenhados cedo demais: "
                   f"p50={statistics.median(early):.0f} p90={_p(early,.9):.0f} "
                   f"max={max(early)}")
        dv = [r["vwap_diff_pct"] for r in ch if r.get("vwap_diff_pct")]
        sg = [r["sigma_ratio"] for r in ch if r.get("sigma_ratio")]
        sh = [r["anchor_shift_candles"] for r in ch if r.get("anchor_shift_candles")]
        if dv:
            out.append(f"    quando muda: |ΔVWAP| p50={statistics.median(dv):.2f}% "
                       f"p90={_p(dv,.9):.2f}%   sigma_causal/sigma_legacy "
                       f"p50={statistics.median(sg):.2f}   "
                       f"deslocamento da ancora p50={statistics.median(sh):.0f} candles")
        summary["vwap"] = {
            "n": len(v), "changed": len(ch), "missing": len(miss),
            "early_p50": statistics.median(early),
            "vwap_diff_p50": statistics.median(dv) if dv else None,
            "sigma_ratio_p50": statistics.median(sg) if sg else None,
        }

    # --- 9. tabela de magnitude ------------------------------------------
    out.append(f"\n{'='*78}\nS1.1-9 — MAGNITUDE\n{'='*78}")
    out.append(f"\n  {'consumidor':>22s} {'eventos':>8s} {'legacy':>7s} "
               f"{'anacron':>8s} {'causal':>7s} {'mudam':>7s} {'%':>7s} "
               f"{'sever.':>7s}")
    table: list[tuple] = []
    for kind, label in (("bos", "confluence/BOS"), ("choch", "confluence/CHoCH")):
        g = [r for r in conf if r.kind == kind]
        if not g:
            continue
        had = sum(1 for r in g if r.had_sweep)
        lost = sum(1 for r in g
                   if (r.lost_at_window if kind == "choch" else r.lost_at_break))
        table.append((label, len(g), had, lost, had - lost, lost))
    h = summary.get("hunt", {})
    for stream in ("state", "history", "continuation"):
        d = h.get(stream, {})
        ch = d.get("removed", 0) + d.get("changed", 0)
        table.append((f"hunt/{stream}", d.get("n_legacy", 0), None, None, None, ch))
    o = summary.get("oi", {})
    table.append(("oi_regime/FLUSH", o.get("eligible", 0), o.get("eligible", 0),
                  o.get("eligible", 0), 0, 0))
    vs = summary.get("vwap", {})
    table.append(("vwap_anchor", vs.get("n", 0), None, None, None,
                  vs.get("changed", 0)))
    sev_rows = {}
    for label, n, legacy, anach, causal, changed in table:
        pct = changed / n if n else 0.0
        sev = "HIGH" if pct >= 0.20 else "MEDIUM" if pct >= 0.05 else "LOW"
        sev_rows[label] = [n, legacy, anach, causal, changed, pct, sev]
        out.append(f"  {label:>22s} {n:8d} "
                   f"{'—' if legacy is None else legacy:>7} "
                   f"{'—' if anach is None else anach:>8} "
                   f"{'—' if causal is None else causal:>7} "
                   f"{changed:7d} {pct:7.1%} {sev:>7s}")
    out.append("\n  criterio: HIGH >=20% dos eventos do consumidor mudam de "
               "valor sob o gate;\n  MEDIUM >=5%; LOW abaixo disso. O OI entra "
               "a parte: 0% de conteudo muda,\n  mas 100% da DATACAO e "
               "retroativa (a severidade dele e de datacao, nao de valor).")
    summary["severity"] = sev_rows

    # --- 14. casos claros -------------------------------------------------
    out.append(f"\n{'='*78}\nS1.1-14 — BTC / ETH / SOL\n{'='*78}")
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        g = [r for r in conf if r.symbol == sym]
        if not g:
            continue
        lost = sum(1 for r in g
                   if (r.lost_at_window if r.kind == "choch" else r.lost_at_break))
        had = sum(1 for r in g if r.had_sweep)
        out.append(f"    {sym:>9s}: {len(g):4d} eventos, {had:3d} com fator, "
                   f"{lost:3d} perdem ({lost/max(1,len(g)):5.1%})")
        summary.setdefault("majors", {})[sym] = [len(g), had, lost]
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframes", nargs="+", default=["15m", "1h", "4h"])
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=112)
    ap.add_argument("--candles", type=int, default=1200)
    ap.add_argument("--json", default=str(BASELINE))
    args = ap.parse_args()

    tfs = [TimeFrame(t) for t in args.timeframes]
    provider = OfflineKlinesProvider()
    symbols = args.symbols or cached_symbols(tfs)[: args.limit]
    panel = Panel()
    for s in symbols:
        for tf in tfs:
            try:
                measure(provider, s, tf, args.candles, panel)
            except Exception as exc:  # noqa: BLE001 - um simbolo morto nao para o painel
                panel.errors.append(f"{s} {tf.value}: {type(exc).__name__}: {exc}")

    out = [f"painel: {len(symbols)} simbolos x {len(tfs)} TFs x {args.candles} "
           f"candles; {len(panel.errors)} falhas"]
    summary = report(panel, out)
    print("\n".join(out))
    if panel.errors:
        print(f"\nfalhas ({len(panel.errors)}):")
        for e in panel.errors[:10]:
            print(f"  ! {e}")
    Path(args.json).write_text(json.dumps(summary, indent=1, default=str))


if __name__ == "__main__":
    main()
