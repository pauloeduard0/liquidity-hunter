"""H10: o contexto do Tide como FILTRO dos grabs do HUNT e da CONTINUATION.

O que motiva
------------
O registro do hunt e consistente: toda fonte nova que empurra o score ate o
limiar gera marca aleatoria (H5 displacement 34% vs 48%, H6 sweep-thrust 43%
vs 46%), e o unico ganho veio de ler um sinal existente num lugar melhor (H8).
O caminho aberto e menos marca, mais especifica. O Tide (`frontend/src/utils/
tideRibbon.ts`) carrega tres canais que descrevem ONDE e COM QUEM o grab
aconteceu, sem tocar no score. Este painel mede dois deles, os que dao para
reconstruir no historico so com candles:

    fase       distancia do fechamento a VWAP periodica, em unidades da banda
               (50 = borda +-1 sigma, como a linha de fase do Tide), lida no
               candle FECHADO do grab e ORIENTADA a direcao da captura:
               positiva = o preco ainda esticado a favor da captura, negativa =
               retraido contra ela (uma captura para cima com fase -80 fechou
               abaixo da VWAP -1 sigma).
    agressao   CVD na janela do `MarketControlAnalyzer` (`_TIMEFRAME_WINDOW`),
               em % do volume, tambem orientada a direcao da captura. E o
               fallback de saturacao do Tide; o controller creditado (bordas)
               exige OI e a Binance retem ~30 dias, entao fica de fora.

Os cenarios do usuario (2026-09-13), traduzidos
-----------------------------------------------
    (1) "hunt dos comprados com o preco muito alto": comprados cacados =
        captura para baixo; "muito alto" = o grab fecha bem ACIMA da VWAP,
        isto e, fase orientada <= -100 (esticado CONTRA a captura). Nos dois
        streams a pergunta e a mesma: o grab chegou ao lugar que todo mundo
        olha (a VWAP e a borda), ou o preco esta longe dela?
    (2) "CONT de alta enquanto o preco ja perdeu a alta e continua caindo":
        grab de continuation com a agressao contra a perna E a fase
        esticada contra a captura (a fita "em conflito", lavada).

Hipoteses, fixadas ANTES de rodar
---------------------------------
    H10a  Fase. Grabs cujo fechamento esta dentro do envelope ou retraido
          contra a captura (fase orientada < +50) acertam mais no h=20 que o
          controle casado; grabs com o preco ainda esticado a favor
          (fase >= +100) acertam MENOS que o controle. Baldes:
          <= -100 | (-100,-50] | (-50,+50) | [+50,+100) | >= +100.
          Previsao: acerto cai monotonicamente ao longo dos baldes.
    H10b  Conflito (continuation). Grab com agressao contra a captura e fase
          <= -100 e a perna ja quebrada: acerta menos que o controle. Com
          agressao a favor e fase dentro do envelope, acerta mais.
    H10c  Espaco (hunt, espelho da H10a para vendidos cacados): mesma grade,
          nada de novo a registrar, so a leitura separada por lado cacado.

Criterio de aprovacao (a regra do "balde removido nao e evidencia")
------------------------------------------------------------------
O corte candidato e UM so, fixado aqui: reter fase < +50. Aprova se, em
DISCOVERY e HOLDOUT (h=20):
    - o conjunto retido bate o controle casado;
    - o conjunto removido (fase >= +50) fica ABAIXO do controle casado;
    - o net total em ATR do conjunto retido nao e menor que o do conjunto
      inteiro (tirar o pior balde sempre sobe a media; o total e a prova).
Nada em `liquidity_hunter/` e tocado. Os episodios sao os de producao
(`LiquidityHuntEngine` sem override); o painel so os anota.

Run:
    poetry run python -m research.hunt_tide_context \\
        --out research/hunt_tide_context_baseline.json
    poetry run python -m research.hunt_tide_context \\
        --report-only research/hunt_tide_context_baseline.json
    poetry run python -m research.hunt_tide_context --case ETHUSDT:H4
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from liquidity_hunter.app.dashboard_data import (
    _HIGHER_TIMEFRAME_MAP,
    DashboardData,
    load_dashboard_data,
)
from liquidity_hunter.app.liquidity_hunt import LiquidityHuntEngine
from liquidity_hunter.core.domain import Candle, RetailPositioning
from liquidity_hunter.psychology.analyzers.market_control import _TIMEFRAME_WINDOW
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
    outcome_of,
    series_span,
)

#: A linha de fase do Tide: 50 = borda +-1 sigma da VWAP periodica.
PHASE_PER_SIGMA = 50.0
#: Guard de dispersao do Tide (`MIN_SPAN_FRAC`): acumulacao recem-iniciada
#: tem sigma ~ 0 e a fase explode; abaixo disto nao ha fita.
MIN_SPAN_FRAC = 1e-4
#: Baldes da H10a, sobre a fase ORIENTADA a captura.
PHASE_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("<=-100", float("-inf"), -100.0),
    ("(-100,-50]", -100.0, -50.0),
    ("(-50,+50)", -50.0, 50.0),
    ("[+50,+100)", 50.0, 100.0),
    (">=+100", 100.0, float("inf")),
)
#: O corte candidato, unico, fixado antes de rodar.
RETAIN_BELOW = 50.0
HORIZON = 20


# ---------------------------------------------------------------------------
# 1. Os dois canais do Tide, lidos no candle do grab
# ---------------------------------------------------------------------------


def phase_by_timestamp(data: DashboardData) -> dict[datetime, float]:
    """Fase do Tide por candle: 50 * (close - vwap) / (upper_1 - vwap)."""
    out: dict[datetime, float] = {}
    if data.vwap is None:
        return out
    closes = {c.timestamp: c.close for c in data.candles}
    for p in data.vwap.points:
        if p.upper_1 is None:
            continue
        span = p.upper_1 - p.value
        if span <= abs(p.value) * MIN_SPAN_FRAC:
            continue
        close = closes.get(p.timestamp)
        if close is None:
            continue
        out[p.timestamp] = PHASE_PER_SIGMA * (close - p.value) / span
    return out


def aggression_by_timestamp(candles: Sequence[Candle], window: int) -> dict[datetime, float]:
    """`aggressionByCandle` do Tide: CVD da janela em % do volume da janela."""
    out: dict[datetime, float] = {}
    if window < 1 or len(candles) < window:
        return out
    deltas = [2.0 * c.taker_buy_volume - c.volume for c in candles]
    delta = volume = 0.0
    for i, c in enumerate(candles):
        delta += deltas[i]
        volume += c.volume
        if i >= window:
            delta -= deltas[i - window]
            volume -= candles[i - window].volume
        if i >= window - 1 and volume > 0:
            out[c.timestamp] = 100.0 * delta / volume
    return out


def episode_rows(data: DashboardData, symbol: str, tf_name: str) -> dict[str, dict[str, Any]]:
    """Os episodios de producao dos dois streams, anotados com os canais."""
    engine = LiquidityHuntEngine()
    phase = phase_by_timestamp(data)
    aggression = aggression_by_timestamp(
        data.candles, _TIMEFRAME_WINDOW.get(data.timeframe, 10)
    )
    out: dict[str, dict[str, Any]] = {}
    streams = (
        ("hunt", engine.build_history(data)),
        ("continuation", engine.build_continuation_history(data)),
    )
    for stream, episodes in streams:
        for e in episodes:
            up = e.hunted_side is RetailPositioning.SHORT
            sign = 1.0 if up else -1.0
            ph = phase.get(e.end_timestamp)
            ag = aggression.get(e.end_timestamp)
            ident = f"{symbol}|{tf_name}|{stream}|{e.end_timestamp.isoformat()}"
            out[ident] = {
                "symbol": symbol,
                "tf": tf_name,
                "stream": stream,
                "anchor": e.end_timestamp.isoformat(),
                "start": e.start_timestamp.isoformat(),
                "up": up,
                "score": e.capture_score,
                "sources": sorted(e.capture_sources),
                "phase": None if ph is None else sign * ph,
                "aggression": None if ag is None else sign * ag,
                "sample": sample_of(symbol),
            }
    return out


# ---------------------------------------------------------------------------
# 2. O painel
# ---------------------------------------------------------------------------


def run_panel(symbols: tuple[str, ...], tf_names: tuple[str, ...], seed: int = 7) -> dict[str, Any]:
    rng = random.Random(seed)
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
            before = len(outcomes)
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
                # Dedup entre janelas: a primeira janela que ve o grab fica
                # com ele (mesma regra dos paineis anteriores).
                for ident, row in episode_rows(data, symbol, tf_name).items():
                    if ident in outcomes:
                        continue
                    anchor = datetime.fromisoformat(row["anchor"])
                    res = outcome_of(data.candles, anchor, row["up"], rng)
                    if res is not None:
                        outcomes[ident] = {**row, "window": f"w{w}", **res}
            print(f"  {symbol} {tf_name}: {len(outcomes) - before} episodios", flush=True)

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
            "retain_below": RETAIN_BELOW,
            "horizon": HORIZON,
        },
        "outcomes": outcomes,
        "spans": spans,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# 3. Relatorio
# ---------------------------------------------------------------------------


def _bucket(phase: float) -> str:
    if phase <= -100:
        return PHASE_BUCKETS[0][0]
    if phase <= -50:
        return PHASE_BUCKETS[1][0]
    if phase < 50:
        return PHASE_BUCKETS[2][0]
    if phase < 100:
        return PHASE_BUCKETS[3][0]
    return PHASE_BUCKETS[4][0]


def _split(rows: list[dict[str, Any]], which: str) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("split") == which]


def _line(label: str, rows: list[dict[str, Any]], h: int = HORIZON) -> None:
    print(f"  {label:26s}{_fmt(_agg(rows, h))}")


def _net_total(rows: list[dict[str, Any]], h: int = HORIZON) -> float:
    return sum(float(r[f"net_{h}"]) for r in rows if f"net_{h}" in r)


def _phase_table(rows: list[dict[str, Any]], title: str) -> None:
    print(f"\n  {title}")
    for name, _lo, _hi in PHASE_BUCKETS:
        rs = [r for r in rows if r["phase"] is not None and _bucket(r["phase"]) == name]
        _line(f"fase {name}", rs)
    _line("sem fita", [r for r in rows if r["phase"] is None])


def _verdict(rows: list[dict[str, Any]], title: str) -> bool:
    print(f"\n  VEREDITO {title} (h={HORIZON}, corte: reter fase < {RETAIN_BELOW:+.0f})")
    with_phase = [r for r in rows if r["phase"] is not None]
    keep = [r for r in with_phase if r["phase"] < RETAIN_BELOW]
    drop = [r for r in with_phase if r["phase"] >= RETAIN_BELOW]
    ok = True
    for split in ("discovery", "holdout"):
        k, d = _agg(_split(keep, split)), _agg(_split(drop, split))
        a = _split(with_phase, split)
        ok_keep = k is not None and k["win"] > k["ctl_win"]
        ok_drop = d is not None and d["win"] < d["ctl_win"]
        ok_total = _net_total(_split(keep, split)) >= _net_total(a)
        ok = ok and ok_keep and ok_drop and ok_total
        print(f"   [{split:9s}] retido   {_fmt(k)}  {'PASSA' if ok_keep else 'falha'}")
        print(f"   [{split:9s}] removido {_fmt(d)}  {'PASSA' if ok_drop else 'falha'}")
        print(
            f"   [{split:9s}] net total retido {_net_total(_split(keep, split)):+7.1f} "
            f"vs todos {_net_total(a):+7.1f}  {'PASSA' if ok_total else 'falha'}"
        )
    print(f"  => {title}: {'APROVADO' if ok else 'REPROVADO'}")
    return ok


def report(payload: dict[str, Any]) -> None:  # noqa: PLR0915 - um relatorio
    annotate(payload)
    meta = payload["meta"]
    rows = list(payload["outcomes"].values())
    _section("H10 - CONTEXTO DO TIDE NO GRAB (FASE E AGRESSAO ORIENTADAS A CAPTURA)")
    print(
        f"painel: {len(meta['symbols'])} simbolos x {meta['timeframes']} x "
        f"{meta['windows']} janelas | futuros: {meta['futures']} | erros: {len(payload['errors'])}"
    )
    hunt = [r for r in rows if r["stream"] == "hunt"]
    cont = [r for r in rows if r["stream"] == "continuation"]
    print(f"episodios: hunt {len(hunt)} | continuation {len(cont)}")
    for h in (10, 20, 40):
        print(f"\n  -- horizonte {h} candles --")
        _line("hunt", hunt, h)
        _line("continuation", cont, h)

    _section("H10a - FASE ORIENTADA A CAPTURA, POR BALDE")
    _phase_table(hunt, "HUNT (todos)")
    _phase_table(cont, "CONTINUATION (todos)")
    for tf in meta["timeframes"]:
        _phase_table([r for r in hunt if r["tf"] == tf], f"HUNT {tf}")
        _phase_table([r for r in cont if r["tf"] == tf], f"CONTINUATION {tf}")
    _phase_table([r for r in hunt if r["up"]], "H10c HUNT vendidos cacados (captura p/ cima)")
    _phase_table([r for r in hunt if not r["up"]], "H10c HUNT comprados cacados (captura p/ baixo)")

    _section("H10b - CONFLITO NA CONTINUATION (agressao x fase)")
    with_both = [r for r in cont if r["phase"] is not None and r["aggression"] is not None]
    favor = [r for r in with_both if r["aggression"] >= 0]
    contra = [r for r in with_both if r["aggression"] < 0]
    _line("agressao a favor", favor)
    _line("agressao contra", contra)
    _line("favor & |fase|<50", [r for r in favor if abs(r["phase"]) < 50])
    _line("contra & fase<=-100", [r for r in contra if r["phase"] <= -100])
    _line("contra & fase>=+100", [r for r in contra if r["phase"] >= 100])
    print("  (o mesmo, so pra referencia, no hunt)")
    hb = [r for r in hunt if r["phase"] is not None and r["aggression"] is not None]
    _line("hunt agressao a favor", [r for r in hb if r["aggression"] >= 0])
    _line("hunt agressao contra", [r for r in hb if r["aggression"] < 0])

    _section("VEREDITOS")
    _verdict(hunt, "HUNT")
    _verdict(cont, "CONTINUATION")
    print("\n  por bloco temporal (h=20), conjunto retido vs removido:")
    for stream, rs in (("hunt", hunt), ("continuation", cont)):
        for b in range(BLOCKS):
            blk = [r for r in rs if r.get("block") == b and r["phase"] is not None]
            k = _agg([r for r in blk if r["phase"] < RETAIN_BELOW])
            d = _agg([r for r in blk if r["phase"] >= RETAIN_BELOW])
            if k or d:
                kw = f"{k['win']:6.1%} ({int(k['n'])})" if k else "-"
                dw = f"{d['win']:6.1%} ({int(d['n'])})" if d else "-"
                print(f"    {stream:12s} bloco {b}: retido {kw} | removido {dw}")


def case_report(symbol: str, tf_name: str) -> None:
    tf = TFS[tf_name]
    data = load_dashboard_data(
        symbol=symbol, timeframe=tf, limit=VISIBLE_LIMIT, futures_provider=NoFuturesProvider()
    )
    for row in episode_rows(data, symbol, tf_name).values():
        ph = "  n/a" if row["phase"] is None else f"{row['phase']:+5.0f}"
        ag = "  n/a" if row["aggression"] is None else f"{row['aggression']:+5.1f}"
        print(
            f"  {row['stream']:12s} {row['start'][:16]} -> {row['anchor'][:16]} "
            f"{'up  ' if row['up'] else 'down'} fase {ph} agr {ag}% score={row['score']:.0f} "
            f"{row['sources']}"
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
