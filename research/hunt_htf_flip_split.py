"""H7: fatiar a perna no flip da HTF -- o que muda de stream, e como mede.

A correcao (producao, `LiquidityHuntEngine._split_at_htf_flips`)
-----------------------------------------------------------------
Uma perna era julgada contra a HTF *no seu flip* e ficava com esse veredito
ate o proximo flip da LTF. Uma perna que nunca re-flipa -- o H4 do ETH em
alta de 02/07 a 31/07 sob um D1 que virou bullish em 18/07 -- ficava um mes
inteiro como "contra-tendencia", e cada grab depois da virada da HTF era
rotulado como caca de entrantes presos contra uma tendencia que ja nao
existia. Agora a perna e fatiada em cada flip da HTF (no *fechamento* do
candle que o produziu, a mesma cerca causal de `_htf_trend_at`); a fatia
anterior continua sendo o que era, a posterior e re-julgada.

Isto e uma correcao de **rotulo**, nao de score: nenhum peso, limiar, pool ou
gate muda. O que este arquivo mede e o que uma correcao de rotulo pode
prometer, fixado antes de rodar:

    1. quantas pernas sao fatiadas e quantos episodios mudam de stream;
    2. os episodios que **migram** (hunt -> continuation, ou o inverso), medidos
       no stream novo, nao ficam abaixo do controle casado -- se a fatia
       re-julgada fosse lixo, o rotulo novo tambem estaria errado;
    3. nenhum dos dois streams piora no conjunto (h=20, acerto).

`Legacy` e a producao com o fatiamento desligado (`_split_at_htf_flips`
devolve as pernas como estao) e reproduz o comportamento anterior.

Run:
    poetry run python -m research.hunt_htf_flip_split \\
        --out research/hunt_htf_flip_split_baseline.json
    poetry run python -m research.hunt_htf_flip_split \\
        --report-only research/hunt_htf_flip_split_baseline.json
    poetry run python -m research.hunt_htf_flip_split --case ETHUSDT:H4
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
from liquidity_hunter.app.liquidity_hunt import LiquidityHuntEngine
from liquidity_hunter.core.domain import RetailPositioning
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
    _agg,
    _fmt,
    _section,
    annotate,
    engine_episodes,
    outcome_of,
    series_span,
)


class Legacy(LiquidityHuntEngine):
    """Producao sem o fatiamento: a perna inteira leva a HTF do seu flip."""

    @classmethod
    def _split_at_htf_flips(cls, segments: Any, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        return list(segments)


VARIANTS: dict[str, type[LiquidityHuntEngine]] = {
    "LEGACY": Legacy,
    "SPLIT": LiquidityHuntEngine,
}


def _n_sliced(data: DashboardData) -> int:
    engine = LiquidityHuntEngine()
    segments = engine._trend_segments(data.internal_structure_events)
    now = data.candles[-1].timestamp if data.candles else None
    sliced = engine._split_at_htf_flips(
        segments, data.higher_timeframe_events, engine._htf_period(data), now
    )
    return sum(1 for _d, _t, e in sliced if e is None)


def run_panel(symbols: tuple[str, ...], tf_names: tuple[str, ...], seed: int = 7) -> dict[str, Any]:
    rng = random.Random(seed)
    episodes: dict[str, dict[str, dict[str, Any]]] = {v: {} for v in VARIANTS}
    outcomes: dict[str, dict[str, Any]] = {}
    spans: dict[str, list[str]] = {}
    errors: list[str] = []
    n_legs = 0
    n_sliced = 0

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
                if w == 0:
                    n_legs += len(
                        LiquidityHuntEngine._trend_segments(data.internal_structure_events)
                    )
                    n_sliced += _n_sliced(data)
                for name, cls in VARIANTS.items():
                    for key, ep in engine_episodes(cls(), data, symbol, tf_name).items():
                        # A chave NAO leva o stream: um grab que migra de stream
                        # tem de ser reconhecido como o mesmo grab.
                        ident = f"{key.symbol}|{key.tf}|{key.anchor}"
                        episodes[name].setdefault(
                            ident, {**ep, "stream": key.stream, "window": f"w{w}"}
                        )
                        if ident not in outcomes:
                            up = ep["hunted_side"] == RetailPositioning.SHORT.value
                            res = outcome_of(
                                data.candles, datetime.fromisoformat(key.anchor), up, rng
                            )
                            if res is not None:
                                outcomes[ident] = {
                                    "symbol": symbol,
                                    "tf": tf_name,
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
            "legs_w0": n_legs,
            "htf_slices_w0": n_sliced,
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


def _ids(payload: dict[str, Any], variant: str, stream: str) -> set[str]:
    return {i for i, ep in payload["episodes"][variant].items() if ep["stream"] == stream}


def report(payload: dict[str, Any]) -> None:  # noqa: PLR0915 - um relatorio
    annotate(payload)
    meta = payload["meta"]
    _section("H7 - PERNA FATIADA NO FLIP DA HTF")
    print(
        f"painel: {len(meta['symbols'])} simbolos x {meta['timeframes']} x "
        f"{meta['windows']} janelas | futuros: {meta['futures']} | erros: {len(payload['errors'])}"
    )
    print(
        f"pernas (w0): {meta['legs_w0']} | fatias por flip da HTF: {meta['htf_slices_w0']} "
        f"({meta['htf_slices_w0'] / max(1, meta['legs_w0']):.1%} das pernas)"
    )
    leg, new = payload["episodes"]["LEGACY"], payload["episodes"]["SPLIT"]
    both = set(leg) & set(new)
    migrou = {i for i in both if leg[i]["stream"] != new[i]["stream"]}
    h2c = {i for i in migrou if new[i]["stream"] == "continuation"}
    c2h = migrou - h2c
    lado_muda = {i for i in both if leg[i]["hunted_side"] != new[i]["hunted_side"]}
    print(
        f"episodios: legado {len(leg)} -> novo {len(new)} | comuns {len(both)} | "
        f"surgem {len(set(new) - set(leg))} | somem {len(set(leg) - set(new))}"
    )
    print(
        f"migram de stream: {len(migrou)} (hunt->cont {len(h2c)}, cont->hunt {len(c2h)}) | "
        f"lado cacado muda: {len(lado_muda)}"
    )

    for stream in ("hunt", "continuation"):
        print(f"\n--- {stream} ---")
        a, b = _ids(payload, "LEGACY", stream), _ids(payload, "SPLIT", stream)
        for h in (10, 20, 40):
            print(f"  h={h:2d} legado {_fmt(_agg(_rows(payload, a), h))}")
            print(f"       novo   {_fmt(_agg(_rows(payload, b), h))}")
        print("  por timeframe (h=20):")
        for tf in meta["timeframes"]:
            aa = _agg([r for r in _rows(payload, a) if r["tf"] == tf])
            bb = _agg([r for r in _rows(payload, b) if r["tf"] == tf])
            if aa and bb:
                print(
                    f"    {tf:4s} legado {aa['win']:6.1%} ({int(aa['n'])}) -> "
                    f"novo {bb['win']:6.1%} ({int(bb['n'])})  ctl {bb['ctl_win']:.1%}"
                )

    print("\n--- episodios que MIGRAM (medidos no stream novo) ---")
    for label, ids in (("hunt -> continuation", h2c), ("continuation -> hunt", c2h)):
        rows = _rows(payload, ids)
        print(f"  {label}: {_fmt(_agg(rows))}")
        print(f"     [discovery] {_fmt(_agg(_split(rows, 'discovery')))}")
        print(f"     [holdout  ] {_fmt(_agg(_split(rows, 'holdout')))}")
    print(f"  SURGEM  {_fmt(_agg(_rows(payload, set(new) - set(leg))))}")
    print(f"  SOMEM   {_fmt(_agg(_rows(payload, set(leg) - set(new))))}")

    print("\n  VEREDITO (h=20):")
    ok = True
    rm = _rows(payload, migrou)
    am = _agg(rm)
    if am is not None:
        c = am["win"] >= am["ctl_win"]
        ok &= c
        verdict = "PASSA" if c else "falha"
        print(f"  migrados >= controle: {verdict} ({am['win']:.1%} vs {am['ctl_win']:.1%})")
    for stream in ("hunt", "continuation"):
        aa = _agg(_rows(payload, _ids(payload, "LEGACY", stream)))
        bb = _agg(_rows(payload, _ids(payload, "SPLIT", stream)))
        if aa and bb:
            c = bb["win"] >= aa["win"] - 0.005
            ok &= c
            verdict = "PASSA" if c else "falha"
            print(f"  {stream} nao piora: {verdict} ({aa['win']:.1%} -> {bb['win']:.1%})")
    print(f"  => correcao de rotulo {'SUSTENTADA' if ok else 'CONTESTADA'} pela medicao")


def case_report(symbol: str, tf_name: str) -> None:
    tf = TFS[tf_name]
    data = load_dashboard_data(
        symbol=symbol, timeframe=tf, limit=VISIBLE_LIMIT, futures_provider=NoFuturesProvider()
    )
    engine = LiquidityHuntEngine()
    print(f"\n=== {symbol} {tf_name}: flips da HTF (known_at) ===")
    for at, trend in engine._htf_flips(data.higher_timeframe_events, engine._htf_period(data)):
        print(f"  {at}  {trend.value}")
    for name, cls in VARIANTS.items():
        for stream, method in (
            ("hunt", "build_history"),
            ("continuation", "build_continuation_history"),
        ):
            print(f"\n--- {name} {stream} ---")
            for e in getattr(cls(), method)(data):
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
