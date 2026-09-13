"""H8: o gate de extremo do VSA e o vacuo do stream de continuation.

O que o codigo faz hoje, lido antes de medir
--------------------------------------------
`build_continuation_history` exige assinatura de piso (`require_vsa=True`) e
desliga o `raid` (`allow_raid=False`); a continuation nao tem `realignment`.
Sobram como piso `vsa` e `supertrend`. E o `VolumeSpreadAnalyzer` so emite um
padrao bearish (up-thrust / buying climax) se o candle faz a **maxima dos 20
anteriores** (`_at_local_extreme`). Numa perna de baixa, o topo de um pullback
e por definicao um `LOWER_HIGH` -- mais baixo que os 20 anteriores -- entao o
gate descarta exatamente o candle que a continuation precisa. O stream so
enxerga um pullback quando ele *varre* uma referencia (raro numa escada
limpa) ou quando o Supertrend flipa. Resultado: quanto mais limpa a
tendencia, mais vazio o stream.

O caso (ETH H4, perna bearish 13/05 -> 15/06 sob D1 bearish, usuario
2026-09-13): dez `LOWER_HIGH`, um unico sweep bullish (13/06), zero grabs. Sem
o gate, o mesmo analisador classifica os candles dos pivos de 13/05, 14/05,
23/05 e 26/05 como up-thrust / buying climax (confianca 59-82).

Hipotese, fixada antes de rodar
-------------------------------
Numa perna de continuation, o candle de cada pivo de pullback (`LOWER_HIGH`
numa perna bearish, `HIGHER_LOW` numa bullish) e classificado pelo **mesmo**
analisador, **mesmos** limiares, **sem** o gate. Um padrao do lado do grab
entra como fonte `vsa` (3, ou 4 se confianca >= 70) -- colapsa por `max` com
o VSA que a producao ja emitiu, entao nada conta duas vezes. Piso, ancora,
limiar (4) e o guard anti-raid sao os de producao. O hunt nao e tocado.

Um braco (P1); P0 e a classe com o sinal desligado e reproduz a producao.

Criterio de aprovacao: os episodios que **surgem** acertam mais que o
controle casado em discovery E holdout (h=20); o conjunto da continuation
nao piora.

Run:
    poetry run python -m research.hunt_pivot_thrust \\
        --out research/hunt_pivot_thrust_baseline.json
    poetry run python -m research.hunt_pivot_thrust \\
        --report-only research/hunt_pivot_thrust_baseline.json
    poetry run python -m research.hunt_pivot_thrust --case ETHUSDT:H4
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
    EpisodeKey,
    _agg,
    _fmt,
    _section,
    annotate,
    outcome_of,
    series_span,
)


def pivot_thrust_signals(
    data: DashboardData,
    hunted_short: bool,
    capture_direction: MarketDirection,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, float, str]]:
    """VSA do lado do grab nos candles de pivo de pullback, sem o gate."""
    candles = data.candles
    if len(candles) < 3:
        return []
    index = {c.timestamp: i for i, c in enumerate(candles)}
    analyzer = VolumeSpreadAnalyzer(gate_extreme_lookback=0)
    lookback = analyzer._resolve_lookback(candles[0].timeframe)
    deltas = volume_delta_series(candles)
    patterns = _VSA_SHORT_CAPTURE if hunted_short else _VSA_LONG_CAPTURE
    # O pullback de uma perna bullish faz HIGHER_LOW (grab para baixo, longs
    # cacados); o de uma perna bearish faz LOWER_HIGH (grab para cima).
    pivot = StructureEvent.LOWER_HIGH if hunted_short else StructureEvent.HIGHER_LOW
    out: list[tuple[datetime, float, str]] = []
    for e in data.internal_structure_events:
        if e.event is not pivot or not start <= e.timestamp <= end:
            continue
        i = index.get(e.timestamp)
        if i is None or i < lookback:
            continue
        sig = analyzer._classify(candles, deltas, i, lookback)
        if sig is None or sig.pattern not in patterns:
            continue
        # Peso por confianca, como a H8 mediu. A H9 (P3) promoveu peso 4 sempre
        # (`research/hunt_pivot_thrust_ext.py`); a producao le assim hoje.
        weight = _WEIGHT_VSA_STRONG if sig.confidence >= _VSA_STRONG_CONFIDENCE else _WEIGHT_VSA
        out.append((e.timestamp, weight, "vsa"))
    return out


class PivotThrustEngine(LiquidityHuntEngine):
    """Producao (P1) ou producao com o sinal de pivo desligado (P0).

    O sinal foi promovido a producao em 2026-09-13
    (`LiquidityHuntEngine._pivot_vsa_signals`); `pivot_thrust_signals` acima e
    a versao de pesquisa que o painel mediu, mantida para o registro. P0
    desliga o gancho de producao e reproduz o motor anterior.
    """

    ENABLED = True

    @staticmethod
    def _pivot_vsa_signals(  # type: ignore[override]
        data: DashboardData, hunted_short: bool, start: datetime, end: datetime
    ) -> list[tuple[datetime, float, str]]:
        return []


class P0(PivotThrustEngine):
    ENABLED = False


class P1(LiquidityHuntEngine):
    ENABLED = True


VARIANTS: dict[str, type[LiquidityHuntEngine]] = {"P0": P0, "P1": P1}


def continuation_episodes(
    engine: LiquidityHuntEngine, data: DashboardData, symbol: str, tf: str
) -> dict[EpisodeKey, dict[str, Any]]:
    out: dict[EpisodeKey, dict[str, Any]] = {}
    for e in engine.build_continuation_history(data):
        key = EpisodeKey(symbol, tf, "continuation", e.end_timestamp.isoformat())
        out[key] = {
            "start": e.start_timestamp.isoformat(),
            "score": e.capture_score,
            "sources": sorted(e.capture_sources),
            "hunted_side": e.hunted_side.value,
        }
    return out


def run_panel(symbols: tuple[str, ...], tf_names: tuple[str, ...], seed: int = 7) -> dict[str, Any]:
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
                    for key, ep in continuation_episodes(cls(), data, symbol, tf_name).items():
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
                                    "stream": "continuation",
                                    "anchor": key.anchor,
                                    "up": up,
                                    "sample": sample_of(symbol),
                                    **res,
                                }
            print(
                f"  {symbol} {tf_name}: " + " ".join(f"{v}={len(episodes[v])}" for v in VARIANTS),
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
        },
        "episodes": episodes,
        "outcomes": outcomes,
        "spans": spans,
        "errors": errors,
    }


def _rows(payload: dict[str, Any], idents: set[str]) -> list[dict[str, Any]]:
    return [payload["outcomes"][i] for i in idents if i in payload["outcomes"]]


def _split(rows: list[dict[str, Any]], which: str) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("split") == which]


def report(payload: dict[str, Any]) -> None:  # noqa: PLR0915 - um relatorio
    annotate(payload)
    meta = payload["meta"]
    _section("H8 - VSA SEM GATE NOS PIVOS DE PULLBACK (CONTINUATION)")
    print(
        f"painel: {len(meta['symbols'])} simbolos x {meta['timeframes']} x "
        f"{meta['windows']} janelas | futuros: {meta['futures']} | erros: {len(payload['errors'])}"
    )
    base, var = payload["episodes"]["P0"], payload["episodes"]["P1"]
    base_ids, var_ids = set(base), set(var)
    novos, gone = var_ids - base_ids, base_ids - var_ids
    print(f"episodios: producao {len(base)} -> P1 {len(var)}")
    print(f"preservados {len(base_ids & var_ids)} | surgem {len(novos)} | somem {len(gone)}")
    for h in (10, 20, 40):
        print(f"\n  -- horizonte {h} candles --")
        print(f"  producao    {_fmt(_agg(_rows(payload, base_ids), h))}")
        print(f"  P1          {_fmt(_agg(_rows(payload, var_ids), h))}")
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
    for up, lbl in ((True, "alta (perna bearish)"), (False, "baixa (perna bullish)")):
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
    print(f"  => P1 {'APROVADA' if ok_d and ok_h and ok_all else 'REPROVADA'}")


def case_report(symbol: str, tf_name: str) -> None:
    tf = TFS[tf_name]
    data = load_dashboard_data(
        symbol=symbol, timeframe=tf, limit=VISIBLE_LIMIT, futures_provider=NoFuturesProvider()
    )
    for name, cls in VARIANTS.items():
        print(f"\n--- {name} continuation ---")
        for e in cls().build_continuation_history(data):
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
