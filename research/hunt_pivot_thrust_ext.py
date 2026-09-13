"""H9: o que ainda falta no pullback depois da H8 -- duas extensoes, medidas.

Depois da H8 (VSA sem gate no candle do pivo de pullback, em producao), a
perna bearish do ETH H4 13/05 -> 15/06 ganhou UM grab (14/05) e continuou
vazia ate junho. Lido candle a candle, o que sobrou:

    23/05 20:00  pivo `LOWER_HIGH`, buying climax confianca 60 -> peso 3,
                 sem delta -> 3 < 4. Falta um ponto.
    26/05 12:00  up-thrust confianca 82 -- mas no candle que RETESTOU o pivo
                 de 25/05 (2140 contra 2142), nao no candle do evento. A H8
                 so le o candle carimbado.
    29/05, 31/05, 08/06, 11/06  sem anatomia de thrust: nada a ler.

Duas extensoes, pre-registradas, uma variavel cada:

    P2  janela ao redor do pivo: alem do candle do evento, o candle que faz o
        EXTREMO (maxima numa perna bearish) nos PIVOT_WINDOW candles de cada
        lado do pivo -- o reteste do pivo e o mesmo topo de pullback.
    P3  o VSA de pivo pesa 4 mesmo fraco (confianca < 70): um thrust no topo
        do pullback fecha o grab sozinho, sem precisar de delta.
    P23 as duas.

P1 e a H8 como medida (peso por confianca; a producao passou a ser P3 depois
desta etapa). Criterio: os episodios que surgem sobre a P1 acertam
mais que o controle em discovery E holdout (h=20); o stream nao piora.
Se P2 e P3 passarem sozinhas, P23 e lida; senao, nao.

Run:
    poetry run python -m research.hunt_pivot_thrust_ext \\
        --out research/hunt_pivot_thrust_ext_baseline.json
    poetry run python -m research.hunt_pivot_thrust_ext \\
        --report-only research/hunt_pivot_thrust_ext_baseline.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from liquidity_hunter.app.dashboard_data import DashboardData
from liquidity_hunter.app.liquidity_hunt import (
    _VSA_LONG_CAPTURE,
    _VSA_SHORT_CAPTURE,
    _VSA_STRONG_CONFIDENCE,
    _WEIGHT_VSA,
    _WEIGHT_VSA_STRONG,
    LiquidityHuntEngine,
)
from liquidity_hunter.core.domain import StructureEvent
from liquidity_hunter.indicators.volume_delta import volume_delta_series
from liquidity_hunter.psychology.analyzers.volume_spread import VolumeSpreadAnalyzer
from research import hunt_pivot_thrust as hp
from research._symbols import UNIVERSE
from research.hunt_score_variants import BLOCKS, _agg, _fmt, _section, annotate

PIVOT_WINDOW = 6


def pivot_signals(
    data: DashboardData,
    hunted_short: bool,
    start: datetime,
    end: datetime,
    window: int,
    weak_is_strong: bool,
) -> list[tuple[datetime, float, str]]:
    candles = data.candles
    if len(candles) < 3:
        return []
    pivot = StructureEvent.LOWER_HIGH if hunted_short else StructureEvent.HIGHER_LOW
    stamps = [
        e.timestamp
        for e in data.internal_structure_events
        if e.event is pivot and start <= e.timestamp <= end
    ]
    if not stamps:
        return []
    index = {c.timestamp: i for i, c in enumerate(candles)}
    analyzer = VolumeSpreadAnalyzer(gate_extreme_lookback=0)
    deltas = volume_delta_series(candles)
    patterns = _VSA_SHORT_CAPTURE if hunted_short else _VSA_LONG_CAPTURE
    out: list[tuple[datetime, float, str]] = []
    seen: set[int] = set()
    for ts in stamps:
        i = index.get(ts)
        if i is None:
            continue
        cands = {i}
        if window > 0:
            lo, hi = max(0, i - window), min(len(candles), i + window + 1)
            rng = range(lo, hi)
            cands.add(
                max(rng, key=lambda k: candles[k].high)
                if hunted_short
                else min(rng, key=lambda k: candles[k].low)
            )
        for k in sorted(cands):
            if k in seen or not start <= candles[k].timestamp <= end:
                continue
            sig = analyzer.classify_candle(candles, deltas, k)
            if sig is None or sig.pattern not in patterns:
                continue
            seen.add(k)
            strong = weak_is_strong or sig.confidence >= _VSA_STRONG_CONFIDENCE
            out.append((candles[k].timestamp, _WEIGHT_VSA_STRONG if strong else _WEIGHT_VSA, "vsa"))
    return out


class _Ext(LiquidityHuntEngine):
    WINDOW = 0
    WEAK_IS_STRONG = False

    @classmethod
    def _pivot_vsa_signals(  # type: ignore[override]
        cls, data: DashboardData, hunted_short: bool, start: datetime, end: datetime
    ) -> list[tuple[datetime, float, str]]:
        return pivot_signals(data, hunted_short, start, end, cls.WINDOW, cls.WEAK_IS_STRONG)


class P1(_Ext):
    """A H8 como foi medida (peso por confianca). A producao, desde a H9, e P3."""


class P2(_Ext):
    WINDOW = PIVOT_WINDOW


class P3(_Ext):
    WEAK_IS_STRONG = True


class P23(_Ext):
    WINDOW = PIVOT_WINDOW
    WEAK_IS_STRONG = True


VARIANTS: dict[str, type[LiquidityHuntEngine]] = {"P1": P1, "P2": P2, "P3": P3, "P23": P23}


def _rows(payload: dict[str, Any], idents: set[str]) -> list[dict[str, Any]]:
    return [payload["outcomes"][i] for i in idents if i in payload["outcomes"]]


def _split(rows: list[dict[str, Any]], which: str) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("split") == which]


def report_variant(payload: dict[str, Any], variant: str) -> bool:
    base, var = payload["episodes"]["P1"], payload["episodes"][variant]
    base_ids, var_ids = set(base), set(var)
    novos, gone = var_ids - base_ids, base_ids - var_ids
    _section(f"{variant}")
    print(
        f"  episodios: P1 {len(base)} -> {variant} {len(var)} | "
        f"surgem {len(novos)} | somem {len(gone)}"
    )
    for h in (10, 20, 40):
        rn = _rows(payload, novos)
        print(f"  h={h:2d} P1      {_fmt(_agg(_rows(payload, base_ids), h))}")
        print(f"       {variant:7s} {_fmt(_agg(_rows(payload, var_ids), h))}")
        print(f"       SURGEM  {_fmt(_agg(rn, h))}")
        print(f"        [disc] {_fmt(_agg(_split(rn, 'discovery'), h))}")
        print(f"        [hold] {_fmt(_agg(_split(rn, 'holdout'), h))}")
        print(f"       SOMEM   {_fmt(_agg(_rows(payload, gone), h))}")
    print("  surgem por timeframe (h=20):")
    for tf in payload["meta"]["timeframes"]:
        an = _agg([r for r in _rows(payload, novos) if r["tf"] == tf])
        if an:
            print(f"    {tf:4s} {an['win']:6.1%} ({int(an['n'])}) ctl {an['ctl_win']:.1%}")
    print("  surgem por bloco (h=20):")
    for b in range(BLOCKS):
        an = _agg([r for r in _rows(payload, novos) if r.get("block") == b])
        if an:
            print(f"    bloco {b}: {an['win']:6.1%} ({int(an['n'])}) ctl {an['ctl_win']:.1%}")
    rn = _rows(payload, novos)
    d, hld = _agg(_split(rn, "discovery")), _agg(_split(rn, "holdout"))
    ok_d = d is not None and d["win"] > d["ctl_win"]
    ok_h = hld is not None and hld["win"] > hld["ctl_win"]
    ab, av = _agg(_rows(payload, base_ids)), _agg(_rows(payload, var_ids))
    ok_all = ab is not None and av is not None and av["win"] >= ab["win"]
    print(
        f"  VEREDITO: surgem>ctl discovery {'PASSA' if ok_d else 'falha'} | "
        f"holdout {'PASSA' if ok_h else 'falha'} | conjunto nao piora "
        f"{'PASSA' if ok_all else 'falha'} => "
        f"{'APROVADA' if ok_d and ok_h and ok_all else 'REPROVADA'}"
    )
    return bool(ok_d and ok_h and ok_all)


def report(payload: dict[str, Any]) -> None:
    annotate(payload)
    meta = payload["meta"]
    _section("H9 - EXTENSOES DO VSA DE PIVO (CONTINUATION)")
    print(
        f"painel: {len(meta['symbols'])} simbolos x {meta['timeframes']} x "
        f"{meta['windows']} janelas"
    )
    ok2 = report_variant(payload, "P2")
    ok3 = report_variant(payload, "P3")
    if ok2 and ok3:
        report_variant(payload, "P23")
    else:
        _section("P23")
        print("  NAO interpretada: so se le a combinacao quando as partes passam sozinhas.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report-only", type=Path)
    parser.add_argument("--symbols", type=int, default=len(UNIVERSE))
    parser.add_argument("--tfs", default="M15,H1,H4")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    if args.report_only:
        report(json.loads(args.report_only.read_text()))
        return
    hp.VARIANTS = VARIANTS  # type: ignore[assignment]
    payload = hp.run_panel(UNIVERSE[: args.symbols], tuple(args.tfs.split(",")), seed=args.seed)
    payload["meta"]["pivot_window"] = PIVOT_WINDOW
    if args.out:
        args.out.write_text(json.dumps(payload, indent=1, default=str))
        print(f"\nescrito: {args.out}")
    report(payload)


if __name__ == "__main__":
    main()
