"""H6: o gate de extremo do VSA cega o HUNT nos pullbacks de uma perna em alta?

O que o codigo faz hoje, lido antes de medir
--------------------------------------------
`VolumeSpreadAnalyzer._classify` reconhece a anatomia (thrust/climax) e depois
aplica um gate de contexto: um padrao bullish so e emitido se o candle faz a
**minima dos 20 candles anteriores** (`_at_local_extreme`,
`gate_extreme_lookback=20`). Numa perna de alta, um pullback faz por definicao
uma minima MAIS ALTA que a origem da perna -- entao o gate descarta o thrust
exatamente nos candles em que o HUNT precisa dele como assinatura de piso.

O caso (ETH H4, perna 02/07 -> 31/07, usuario 2026-09-13)
---------------------------------------------------------
    06/07 12:00 UTC  O1759 H1788 L1728 C1788, volume 4,0x, fecha na maxima.
                     Anatomia: DOWN_THRUST. Gate: minima dos 20 = 1702
                     (03/07, origem da perna) -> descartado. O cluster fica
                     so com `sweep` (3) e reprova por assinatura de piso.
    17/07 12:00 UTC  O1838 L1802 C1830, volume 2,0x. Anatomia: DOWN_THRUST.
                     Gate: minima dos 20 = 1778 (14/07) -> descartado.
                     Nenhum outro sinal: o pullback e invisivel.

Nos dois, a estrutura ja certificou o extremo (o `LIQUIDITY_SWEEP` diz que
aquela minima tomou uma referencia); o gate do VSA exige um extremo que uma
perna em alta nunca produz.

Hipotese, fixada antes de rodar
-------------------------------
Dentro da perna, o candle do `LIQUIDITY_SWEEP` na direcao da captura e o
candle que faz o extremo varrido (entre o sweep e o proximo evento, no
maximo 8 candles) sao classificados pelo **mesmo** analisador VSA, com os
**mesmos** limiares, **sem** o gate de 20 candles (`gate_extreme_lookback=0`).
Um thrust/climax do lado da captura encontrado ali entra como fonte `vsa`
(peso 3, ou 4 se confianca >= 70) -- a producao ja colapsa fontes iguais por
`max`, entao um VSA que a producao ja emitiu nao conta duas vezes, e piso,
ancora e limiar continuam sendo os de producao, sem excecao.

Um unico braco (S1). S0 e a mesma classe com o sinal desligado e tem de
reproduzir a producao (`test_s0_reproduz_a_producao`).

Criterio de aprovacao: os episodios que **surgem** acertam mais que o
controle casado em simbolo, tf e direcao, em discovery E holdout (h=20); e
o conjunto todo nao mede pior que a producao.

Run:
    poetry run python -m research.hunt_sweep_thrust \\
        --out research/hunt_sweep_thrust_baseline.json
    poetry run python -m research.hunt_sweep_thrust \\
        --report-only research/hunt_sweep_thrust_baseline.json
    poetry run python -m research.hunt_sweep_thrust --case ETHUSDT:H4
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from liquidity_hunter.app.dashboard_data import (
    _HIGHER_TIMEFRAME_MAP,
    DashboardData,
    load_dashboard_data,
)
from liquidity_hunter.app.liquidity_hunt import (
    _VSA_LONG_CAPTURE,
    _VSA_SHORT_CAPTURE,
    _VSA_STRONG_CONFIDENCE,
    _WEIGHT_VSA,
    _WEIGHT_VSA_STRONG,
    LiquidityHuntEngine,
)
from liquidity_hunter.core.domain import (
    MarketDirection,
    RetailPositioning,
    StructureEvent,
)
from liquidity_hunter.indicators.volume_delta import volume_delta_series
from liquidity_hunter.psychology.analyzers.volume_spread import VolumeSpreadAnalyzer
from research._symbols import UNIVERSE, sample_of
from research.hunt_displacement_capture import hunt_episodes
from research.hunt_htf_causality import (
    TFS,
    NoFuturesProvider,
    WindowProvider,
    fetch_series,
)
from research.hunt_score_redundancy import VISIBLE_LIMIT, WINDOW_STEP, WINDOWS
from research.hunt_score_variants import (
    BLOCKS,
    DISCOVERY_SHARE,
    _agg,
    _fmt,
    _section,
    annotate,
    outcome_of,
    series_span,
)

#: Ate onde procurar o candle que faz o extremo varrido depois do sweep.
EXTREME_SPAN = 8


# ---------------------------------------------------------------------------
# 1. O sinal
# ---------------------------------------------------------------------------


def sweep_thrust_signals(
    data: DashboardData,
    hunted_short: bool,
    capture_direction: MarketDirection,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, float, str]]:
    """VSA do lado da captura nos candles de sweep / extremo varrido, sem gate."""
    candles = data.candles
    if len(candles) < 3:
        return []
    index = {c.timestamp: i for i, c in enumerate(candles)}
    analyzer = VolumeSpreadAnalyzer(gate_extreme_lookback=0)
    lookback = analyzer._resolve_lookback(candles[0].timeframe)
    deltas = volume_delta_series(candles)
    patterns = _VSA_SHORT_CAPTURE if hunted_short else _VSA_LONG_CAPTURE
    stamps = sorted(
        e.timestamp
        for e in data.internal_structure_events
        if e.event is StructureEvent.LIQUIDITY_SWEEP
        and e.direction is capture_direction
        and start <= e.timestamp <= end
    )
    events = sorted(e.timestamp for e in data.internal_structure_events)
    out: list[tuple[datetime, float, str]] = []
    seen: set[datetime] = set()
    for ts in stamps:
        i = index.get(ts)
        if i is None or i < lookback:
            continue
        nxt = next((t for t in events if t > ts), None)
        j_end = min(len(candles), i + EXTREME_SPAN + 1)
        if nxt is not None and nxt in index:
            j_end = min(j_end, index[nxt] + 1)
        seg = range(i, j_end)
        if hunted_short:
            j_ext = max(seg, key=lambda k: candles[k].high)
        else:
            j_ext = min(seg, key=lambda k: candles[k].low)
        for k in {i, j_ext}:
            if k < lookback or candles[k].timestamp in seen:
                continue
            sig = analyzer._classify(candles, deltas, k, lookback)
            if sig is None or sig.pattern not in patterns:
                continue
            seen.add(candles[k].timestamp)
            weight = (
                _WEIGHT_VSA_STRONG
                if sig.confidence >= _VSA_STRONG_CONFIDENCE
                else _WEIGHT_VSA
            )
            out.append((candles[k].timestamp, weight, "vsa"))
    return out


# ---------------------------------------------------------------------------
# 2. A variante
# ---------------------------------------------------------------------------


class SweepThrustEngine(LiquidityHuntEngine):
    """Producao mais o VSA sem gate nos candles de sweep; `ENABLED = False` desliga.

    So `_collect_capture_signals` muda; `_capture_grabs` e a producao literal,
    entao piso, ancora, limiar e o guard anti-raid sao os de producao.
    """

    ENABLED = False

    def _collect_capture_signals(  # type: ignore[override]
        self,
        data: DashboardData,
        hunted_short: bool,
        capture_direction: MarketDirection,
        start: datetime,
        end: datetime,
        allow_raid: bool = True,
        pivot_vsa: bool = False,
    ) -> list[tuple[datetime, float, str]]:
        signals = super()._collect_capture_signals(
            data,
            hunted_short,
            capture_direction,
            start,
            end,
            allow_raid=allow_raid,
            pivot_vsa=pivot_vsa,
        )
        # `allow_raid=False` marca a chamada da continuation, fora da hipotese.
        if self.ENABLED and allow_raid:
            signals.extend(
                sweep_thrust_signals(data, hunted_short, capture_direction, start, end)
            )
        return signals


class S0(SweepThrustEngine):
    ENABLED = False


class S1(SweepThrustEngine):
    ENABLED = True


VARIANTS: dict[str, type[SweepThrustEngine]] = {"S0": S0, "S1": S1}


# ---------------------------------------------------------------------------
# 3. O painel
# ---------------------------------------------------------------------------


def run_panel(
    symbols: tuple[str, ...], tf_names: tuple[str, ...], seed: int = 7
) -> dict[str, Any]:
    rng = random.Random(seed)
    episodes: dict[str, dict[str, dict[str, Any]]] = {v: {} for v in VARIANTS}
    outcomes: dict[str, dict[str, Any]] = {}
    spans: dict[str, list[str]] = {}
    errors: list[str] = []

    for symbol in symbols:
        for tf_name in tf_names:
            tf = TFS[tf_name]
            htf = _HIGHER_TIMEFRAME_MAP[tf]
            try:
                series = fetch_series(symbol, tf, htf)
            except Exception as exc:  # noqa: BLE001 - um simbolo morto nao para o painel
                errors.append(f"{symbol} {tf_name}: {type(exc).__name__}: {exc}")
                continue
            if not series[htf] or len(series[tf]) < VISIBLE_LIMIT:
                errors.append(f"{symbol} {tf_name}: serie insuficiente")
                continue
            lo, hi = series_span(series[tf])
            spans[f"{symbol}:{tf_name}"] = [lo.isoformat(), hi.isoformat()]
            for w in range(WINDOWS):
                back = w * WINDOW_STEP
                if back >= len(series[tf]):
                    continue
                cut = series[tf][-1 - back].timestamp
                try:
                    data = load_dashboard_data(
                        provider=WindowProvider(series, cut=cut),
                        symbol=symbol,
                        timeframe=tf,
                        limit=VISIBLE_LIMIT,
                        futures_provider=NoFuturesProvider(),
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{symbol} {tf_name} w{w}: {type(exc).__name__}: {exc}")
                    continue
                for name, cls in VARIANTS.items():
                    for key, ep in hunt_episodes(cls(), data, symbol, tf_name).items():
                        ident = f"{key.symbol}|{key.tf}|{key.stream}|{key.anchor}"
                        episodes[name].setdefault(ident, {**ep, "window": f"w{w}"})
                        if ident not in outcomes:
                            up = ep["hunted_side"] == RetailPositioning.SHORT.value
                            res = outcome_of(
                                data.candles, datetime.fromisoformat(key.anchor), up, rng
                            )
                            if res is not None:
                                outcomes[ident] = {
                                    "symbol": symbol,
                                    "tf": tf_name,
                                    "stream": "hunt",
                                    "anchor": key.anchor,
                                    "up": up,
                                    "sample": sample_of(symbol),
                                    **res,
                                }
            print(
                f"  {symbol} {tf_name}: "
                + " ".join(f"{v}={len(episodes[v])}" for v in VARIANTS),
                flush=True,
            )

    return {
        "meta": {
            "generated": datetime.now(tz=UTC).isoformat(),
            "symbols": list(symbols),
            "timeframes": list(tf_names),
            "windows": WINDOWS,
            "seed": seed,
            "futures": "disabled (NoFuturesProvider)",
            "discovery_share": DISCOVERY_SHARE,
            "blocks": BLOCKS,
            "extreme_span": EXTREME_SPAN,
        },
        "episodes": episodes,
        "outcomes": outcomes,
        "spans": spans,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# 4. Relatorio
# ---------------------------------------------------------------------------


def _rows(payload: dict[str, Any], idents: set[str]) -> list[dict[str, Any]]:
    return [payload["outcomes"][i] for i in idents if i in payload["outcomes"]]


def _split(rows: list[dict[str, Any]], which: str) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("split") == which]


def report(payload: dict[str, Any]) -> None:  # noqa: PLR0915 - um relatorio
    annotate(payload)
    meta = payload["meta"]
    _section("H6 - VSA SEM GATE NOS CANDLES DE SWEEP")
    print(
        f"painel: {len(meta['symbols'])} simbolos x {meta['timeframes']} x "
        f"{meta['windows']} janelas | futuros: {meta['futures']} | erros: {len(payload['errors'])}"
    )
    base, var = payload["episodes"]["S0"], payload["episodes"]["S1"]
    base_ids, var_ids = set(base), set(var)
    novos, gone = var_ids - base_ids, base_ids - var_ids
    score_muda = [i for i in base_ids & var_ids if base[i]["score"] != var[i]["score"]]
    print(f"episodios: producao {len(base)} -> S1 {len(var)}")
    print(
        f"preservados {len(base_ids & var_ids)} | surgem {len(novos)} | "
        f"somem {len(gone)} | score alterado {len(score_muda)}"
    )
    for h in (10, 20, 40):
        print(f"\n  -- horizonte {h} candles --")
        print(f"  producao    {_fmt(_agg(_rows(payload, base_ids), h))}")
        print(f"  S1          {_fmt(_agg(_rows(payload, var_ids), h))}")
        rn = _rows(payload, novos)
        print(f"  SURGEM      {_fmt(_agg(rn, h))}")
        print(f"   [discovery]{_fmt(_agg(_split(rn, 'discovery'), h))}")
        print(f"   [holdout  ]{_fmt(_agg(_split(rn, 'holdout'), h))}")
        print(f"  SOMEM       {_fmt(_agg(_rows(payload, gone), h))}")
    print("\n  surgem por timeframe (h=20):")
    for tf in meta["timeframes"]:
        an = _agg([r for r in _rows(payload, novos) if r["tf"] == tf])
        if an:
            print(f"    {tf:4s} {an['win']:6.1%} ({int(an['n'])}) ctl {an['ctl_win']:.1%}")
    print("  surgem por direcao (h=20):")
    for up, lbl in ((True, "alta (shorts cacados)"), (False, "baixa (longs cacados)")):
        an = _agg([r for r in _rows(payload, novos) if r["up"] is up])
        if an:
            print(f"    {lbl:24s} {an['win']:6.1%} ({int(an['n'])}) ctl {an['ctl_win']:.1%}")
    print("  surgem por bloco temporal (h=20):")
    for b in range(BLOCKS):
        an = _agg([r for r in _rows(payload, novos) if r.get("block") == b])
        if an:
            print(f"    bloco {b}: {an['win']:6.1%} ({int(an['n'])}) ctl {an['ctl_win']:.1%}")

    print("\n  VEREDITO (h=20):")
    rn = _rows(payload, novos)
    d, hld = _agg(_split(rn, "discovery")), _agg(_split(rn, "holdout"))
    ok_d = d is not None and d["win"] > d["ctl_win"]
    ok_h = hld is not None and hld["win"] > hld["ctl_win"]
    ab, av = _agg(_rows(payload, base_ids)), _agg(_rows(payload, var_ids))
    ok_all = ab is not None and av is not None and av["win"] >= ab["win"]
    print(
        f"  surgem > controle: discovery {'PASSA' if ok_d else 'falha'} | "
        f"holdout {'PASSA' if ok_h else 'falha'}"
    )
    print(f"  conjunto nao piora: {'PASSA' if ok_all else 'falha'}")
    print(f"  => S1 {'APROVADA' if ok_d and ok_h and ok_all else 'REPROVADA'}")


def case_report(symbol: str, tf_name: str) -> None:
    tf = TFS[tf_name]
    data = load_dashboard_data(
        symbol=symbol, timeframe=tf, limit=VISIBLE_LIMIT, futures_provider=NoFuturesProvider()
    )
    for name, cls in VARIANTS.items():
        print(f"\n--- {name} ---")
        for e in cls().build_history(data):
            print(
                f"  {e.start_timestamp} -> {e.end_timestamp}  {e.hunted_side.value:5s} "
                f"score={e.capture_score:.0f} {sorted(e.capture_sources)}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report-only", type=Path)
    parser.add_argument("--symbols", type=int, default=len(UNIVERSE))
    parser.add_argument("--tfs", default="M15,H1,H4")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--case", help="SIMBOLO:TF, ex ETHUSDT:H4")
    args = parser.parse_args()

    if args.report_only:
        report(json.loads(args.report_only.read_text()))
        return
    if args.case:
        symbol, tf_name = args.case.split(":")
        case_report(symbol, tf_name)
        return
    payload = run_panel(UNIVERSE[: args.symbols], tuple(args.tfs.split(",")), seed=args.seed)
    if args.out:
        args.out.write_text(json.dumps(payload, indent=1, default=str))
        print(f"\nescrito: {args.out}")
    report(payload)


if __name__ == "__main__":
    main()
