"""H5: o HUNT enxerga a captura que *rompe*, ou so a que *rejeita*?

O que o codigo faz hoje, lido antes de medir
--------------------------------------------
Todas as assinaturas de piso do `LiquidityHuntEngine` tem a forma "nivel
tocado e devolvido": `raid` (pavio atravessa o pool, close volta), `vsa`
(climax/thrust = candle de exaustao), `supertrend` (STOP_RUN = banda tomada e
devolvida) e `realignment` (a quebra que flipa a estrutura, e que so existe
quando o detector confirma um CHoCH/BOS). Um `LIQUIDITY_SWEEP` e por definicao
uma quebra que **nao** segurou, e seu timestamp e o do primeiro pavio a tocar
a referencia (`find_wick_break_index`), nao o do candle que expandiu.

Logo, pela leitura do codigo, uma captura que *desloca* -- o candle de
expansao que atravessa o pool do lado cacado, fecha alem dele e nao devolve --
nao tem assinatura propria. Ela so entra no score como `zone` (2), que sozinho
nunca abre o gate de piso. O motor marca a caca no ponto em que o preco
**cutucou** a liquidez, nao onde a **levou**.

Os dois casos que originaram a pergunta (ETH H1, usuario, 2026-09-13)
---------------------------------------------------------------------
    30/08 16:00 UTC   candle 3,7 TR, corpo 86%, 5,6x a mediana de volume,
                      fecha na maxima. Producao: grab as 12:00 (sweep + vsa,
                      score 7) -- quatro candles ANTES, no primeiro pavio.
    11/09 12:00 UTC   candles 5,4 e 10,5 TR, fecham atraves de quatro pools
                      EQH. Producao: grab as 09:00 -- o cluster (zone 08:00,
                      raid 09:00, realignment 12:00, zones 12-13:00) ancora
                      no primeiro carimbo de piso, o raid, tres candles ANTES.

Nos dois o motor viu a caca; errou o **candle**. E o candle e o que o usuario
le no grafico.

Hipotese e desenho, fixados antes de rodar
------------------------------------------
Um novo sinal, `displacement`: candle na direcao da captura com

    range  >= DISPLACEMENT_TR      x mean TR da janela  (2.5)
    corpo  >= DISPLACEMENT_BODY    do range             (0.5)
    close  alem do extremo dos DISPLACEMENT_LOOKBACK candles anteriores (12)
    volume >= DISPLACEMENT_VOLUME  x mediana dos 50 anteriores (2.0)

Ele e assinatura de piso (abre o gate) e, quando presente no cluster, **ancora
o grab** -- o box termina no candle que levou a liquidez, nao no que a tocou.
Os quatro limiares sao os mesmos que separam os dois casos acima de seus
vizinhos, e nao foram procurados: a primeira grade e a que fica.

Dois bracos, com semanticas diferentes e ambos declarados aqui:

    D4   `displacement` pesa 4, como `raid`/`realignment`: e evidencia de
         confluencia, precisa de parceiro (sweep, zone, delta...) para chegar
         ao limiar 7 do hunt.
    D7   `displacement` pesa 7: o deslocamento E a captura, sozinho.

`D0` e a mesma classe com o sinal desligado, e tem de reproduzir a producao
episodio a episodio (`test_d0_reproduz_a_producao`); se nao reproduzir, a
maquinaria esta errada e nenhum numero desta etapa vale.

Criterio de aprovacao (item 14 da disciplina): os episodios que **surgem**
com o sinal acertam mais que o controle casado em simbolo, timeframe e
direcao, em discovery E em holdout; e os episodios **re-ancorados** (mesma
caca, outro candle) nao medem pior que antes de re-ancorar. Um braco que
passe so em discovery e ruido de busca, nao achado.

Continuation nao e tocada: o stream de continuacao le o lado oposto (o
pullback) e um deslocamento la e a propria perna de tendencia.

Run:
    poetry run python -m research.hunt_displacement_capture \\
        --out research/hunt_displacement_capture_baseline.json
    poetry run python -m research.hunt_displacement_capture \\
        --report-only research/hunt_displacement_capture_baseline.json
    poetry run python -m research.hunt_displacement_capture --case ETHUSDT:H1
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import fmean, median
from typing import Any

from liquidity_hunter.app.dashboard_data import (
    _HIGHER_TIMEFRAME_MAP,
    DashboardData,
    load_dashboard_data,
)
from liquidity_hunter.app.liquidity_hunt import (
    _CAPTURE_THRESHOLD,
    _FLOOR_SIGNATURE_SOURCES,
    _RAID_SHAPED_SOURCES,
    _WEIGHT_DELTA_MODIFIER,
    _WEIGHT_REALIGNMENT,
    LiquidityHuntEngine,
    _opposite,
)
from liquidity_hunter.core.domain import Candle, MarketDirection, RetailPositioning
from liquidity_hunter.indicators.supertrend import true_range_series
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

DISPLACEMENT_TR = 2.5
DISPLACEMENT_BODY = 0.5
DISPLACEMENT_LOOKBACK = 12
DISPLACEMENT_VOLUME = 2.0
DISPLACEMENT_VOLUME_WINDOW = 50

WEIGHT_D4 = 4.0
WEIGHT_D7 = 7.0


# ---------------------------------------------------------------------------
# 1. O sinal
# ---------------------------------------------------------------------------


def displacement_candles(
    candles: list[Candle], capture_direction: MarketDirection
) -> list[datetime]:
    """Timestamps dos candles de deslocamento na direcao da captura.

    Tudo e lido do prefixo ate o candle (extremo anterior, mediana de volume
    anterior); so o `mean TR` e da janela inteira, como em
    `_effective_proximity` -- identico em todos os bracos, entao nao e uma
    variavel do experimento (ver `hunt_atr_proximity_causality`).
    """
    if len(candles) < DISPLACEMENT_LOOKBACK + 2:
        return []
    trs = true_range_series(candles)
    mean_tr = fmean(trs) if trs else 0.0
    if mean_tr <= 0:
        return []
    up = capture_direction is MarketDirection.BULLISH
    out: list[datetime] = []
    for i in range(DISPLACEMENT_LOOKBACK, len(candles)):
        c = candles[i]
        rng = c.high - c.low
        if rng <= 0 or rng < DISPLACEMENT_TR * mean_tr:
            continue
        body = (c.close - c.open) if up else (c.open - c.close)
        if body / rng < DISPLACEMENT_BODY:
            continue
        prev = candles[i - DISPLACEMENT_LOOKBACK : i]
        beyond = c.close > max(p.high for p in prev) if up else c.close < min(p.low for p in prev)
        if not beyond:
            continue
        vol_prev = [p.volume for p in candles[max(0, i - DISPLACEMENT_VOLUME_WINDOW) : i]]
        if not vol_prev or c.volume < DISPLACEMENT_VOLUME * median(vol_prev):
            continue
        out.append(c.timestamp)
    return out


# ---------------------------------------------------------------------------
# 2. A variante
# ---------------------------------------------------------------------------


class DisplacementEngine(LiquidityHuntEngine):
    """Producao mais um sinal e uma regra de ancora; `WEIGHT = None` desliga.

    `_capture_grabs` e uma copia da producao com duas linhas a mais: o
    conjunto de piso inclui `displacement`, e a ancora vai para ele quando
    esta no cluster. A copia e verificada, nao prometida: `D0` (sinal
    desligado) tem de devolver a producao byte a byte.
    """

    WEIGHT: float | None = None

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
        if self.WEIGHT is not None and allow_raid:
            # `allow_raid=False` marca a chamada da continuation, que a
            # hipotese nao alcanca.
            signals.extend(
                (ts, self.WEIGHT, "displacement")
                for ts in displacement_candles(data.candles, capture_direction)
                if start <= ts <= end
            )
        return signals

    def _capture_grabs(  # type: ignore[override]
        self,
        data: DashboardData,
        hunted_short: bool,
        capture_direction: MarketDirection,
        start: datetime,
        end: datetime,
        merge_gap: timedelta | None,
        threshold: float = _CAPTURE_THRESHOLD,
        require_vsa: bool = False,
        realignment_ts: datetime | None = None,
        allow_raid: bool = True,
        pivot_vsa: bool = False,
    ) -> list[tuple[datetime, float, list[str]]]:
        floor_sources = _FLOOR_SIGNATURE_SOURCES | {"displacement"}
        signals = self._collect_capture_signals(
            data,
            hunted_short,
            capture_direction,
            start,
            end,
            allow_raid=allow_raid,
            pivot_vsa=pivot_vsa,
        )
        if realignment_ts is not None:
            signals.append((realignment_ts, _WEIGHT_REALIGNMENT, "realignment"))
        signals.sort(key=lambda s: s[0])

        clusters: list[list[tuple[datetime, float, str]]] = []
        for signal in signals:
            if clusters and merge_gap is not None and signal[0] - clusters[-1][-1][0] <= merge_gap:
                clusters[-1].append(signal)
            else:
                clusters.append([signal])

        grabs: list[tuple[datetime, float, list[str]]] = []
        for cluster in clusters:
            by_source: dict[str, float] = {}
            for _ts, weight, source in cluster:
                by_source[source] = max(by_source.get(source, 0.0), weight)
            if require_vsa and not (floor_sources & by_source.keys()):
                continue
            first_ts, last_ts = cluster[0][0], cluster[-1][0]
            if self._delta_confirms(data, capture_direction, first_ts, last_ts):
                by_source["delta"] = _WEIGHT_DELTA_MODIFIER
            elif "raid" in by_source and self._delta_confirms(
                data, _opposite(capture_direction), first_ts, last_ts
            ):
                by_source["delta"] = _WEIGHT_DELTA_MODIFIER
            if set(by_source) <= _RAID_SHAPED_SOURCES:
                continue
            score = sum(by_source.values())
            if score < threshold:
                continue
            anchor = first_ts
            disp = [ts for ts, _w, s in cluster if s == "displacement"]
            if disp:
                # O box termina no candle que LEVOU a liquidez.
                anchor = min(disp)
            elif require_vsa:
                floor_stamps = [
                    ts for ts, _w, source in cluster if source in ("vsa", "raid", "supertrend")
                ]
                if floor_stamps:
                    anchor = min(floor_stamps)
            grabs.append((anchor, score, sorted(by_source)))
        return grabs


class D0(DisplacementEngine):
    WEIGHT = None


class D4(DisplacementEngine):
    WEIGHT = WEIGHT_D4


class D7(DisplacementEngine):
    WEIGHT = WEIGHT_D7


VARIANTS: dict[str, type[DisplacementEngine]] = {"D0": D0, "D4": D4, "D7": D7}


# ---------------------------------------------------------------------------
# 3. O painel
# ---------------------------------------------------------------------------


def hunt_episodes(
    engine: LiquidityHuntEngine, data: DashboardData, symbol: str, tf: str
) -> dict[EpisodeKey, dict[str, Any]]:
    out: dict[EpisodeKey, dict[str, Any]] = {}
    for e in engine.build_history(data):
        key = EpisodeKey(symbol, tf, "hunt", e.end_timestamp.isoformat())
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
    n_disp = 0

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
                    n_disp += len(displacement_candles(data.candles, MarketDirection.BULLISH))
                    n_disp += len(displacement_candles(data.candles, MarketDirection.BEARISH))
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
            "displacement": {
                "tr": DISPLACEMENT_TR,
                "body": DISPLACEMENT_BODY,
                "lookback": DISPLACEMENT_LOOKBACK,
                "volume": DISPLACEMENT_VOLUME,
                "volume_window": DISPLACEMENT_VOLUME_WINDOW,
            },
            "weights": {"D4": WEIGHT_D4, "D7": WEIGHT_D7},
            "displacement_candles_w0": n_disp,
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


def _same_hunt(
    base: dict[str, dict[str, Any]], var: dict[str, dict[str, Any]]
) -> list[tuple[str, str]]:
    """Pares (id base, id variante) da MESMA caca re-ancorada: mesmo simbolo,
    tf, lado e inicio, fim diferente."""
    by_start: dict[tuple[str, str], list[str]] = {}
    for i, ep in var.items():
        sym, tf, _s, _a = i.split("|")
        by_start.setdefault((f"{sym}|{tf}|{ep['hunted_side']}", ep["start"]), []).append(i)
    pairs = []
    for i, ep in base.items():
        if i in var:
            continue
        sym, tf, _s, _a = i.split("|")
        cands = by_start.get((f"{sym}|{tf}|{ep['hunted_side']}", ep["start"]), [])
        cands = [c for c in cands if c not in base]
        if len(cands) == 1:
            pairs.append((i, cands[0]))
    return pairs


def _verdict(label: str, disc: dict[str, float] | None, hold: dict[str, float] | None) -> bool:
    ok_d = disc is not None and disc["win"] > disc["ctl_win"]
    ok_h = hold is not None and hold["win"] > hold["ctl_win"]
    print(
        f"  {label}: discovery {'PASSA' if ok_d else 'falha'} | "
        f"holdout {'PASSA' if ok_h else 'falha'}"
    )
    return ok_d and ok_h


def report_variant(payload: dict[str, Any], variant: str) -> None:  # noqa: PLR0915
    _section(f"{variant}  (displacement pesa {payload['meta']['weights'][variant]})")
    base, var = payload["episodes"]["D0"], payload["episodes"][variant]
    base_ids, var_ids = set(base), set(var)
    novos = var_ids - base_ids
    gone = base_ids - var_ids
    pairs = _same_hunt(base, var)
    paired_base = {a for a, _ in pairs}
    paired_var = {b for _, b in pairs}
    novos_puros = novos - paired_var
    gone_puros = gone - paired_base
    com_disp = {i for i in var_ids if "displacement" in var[i]["sources"]}
    print(f"  episodios: baseline {len(base)} -> variante {len(var)}")
    print(
        f"  preservados {len(base_ids & var_ids)} | re-ancorados {len(pairs)} | "
        f"surgem {len(novos_puros)} | somem {len(gone_puros)} | "
        f"com displacement no cluster {len(com_disp)}"
    )

    for h in (10, 20, 40):
        print(f"\n  -- horizonte {h} candles --")
        rb, rv = _rows(payload, base_ids), _rows(payload, var_ids)
        print(f"  baseline    {_fmt(_agg(rb, h))}")
        print(f"  variante    {_fmt(_agg(rv, h))}")
        print(f"  SURGEM      {_fmt(_agg(_rows(payload, novos_puros), h))}")
        print(f"   [discovery]{_fmt(_agg(_split(_rows(payload, novos_puros), 'discovery'), h))}")
        print(f"   [holdout  ]{_fmt(_agg(_split(_rows(payload, novos_puros), 'holdout'), h))}")
        print(f"  RE-ANCORADOS antes {_fmt(_agg(_rows(payload, paired_base), h))}")
        print(f"  RE-ANCORADOS depois{_fmt(_agg(_rows(payload, paired_var), h))}")
        print(
            "   [discovery] antes "
            f"{_fmt(_agg(_split(_rows(payload, paired_base), 'discovery'), h))}"
        )
        print(
            f"   [discovery] depois{_fmt(_agg(_split(_rows(payload, paired_var), 'discovery'), h))}"
        )
        print(
            f"   [holdout  ] antes {_fmt(_agg(_split(_rows(payload, paired_base), 'holdout'), h))}"
        )
        print(
            f"   [holdout  ] depois{_fmt(_agg(_split(_rows(payload, paired_var), 'holdout'), h))}"
        )
        print(f"  SOMEM       {_fmt(_agg(_rows(payload, gone_puros), h))}")

    print("\n  por timeframe (h=20, acerto variante vs baseline):")
    for tf in payload["meta"]["timeframes"]:
        rb = [r for r in _rows(payload, base_ids) if r["tf"] == tf]
        rv = [r for r in _rows(payload, var_ids) if r["tf"] == tf]
        ab, av = _agg(rb), _agg(rv)
        if ab and av:
            print(
                f"    {tf:4s} base {ab['win']:6.1%} ({int(ab['n'])}) -> "
                f"var {av['win']:6.1%} ({int(av['n'])})  ctl {av['ctl_win']:.1%}"
            )
    print("  por direcao (h=20):")
    for up, lbl in ((True, "alta (shorts cacados)"), (False, "baixa (longs cacados)")):
        rn = [r for r in _rows(payload, novos_puros) if r["up"] is up]
        an = _agg(rn)
        if an:
            print(f"    {lbl:24s} surgem {an['win']:6.1%} ({int(an['n'])}) ctl {an['ctl_win']:.1%}")
    print("  surgem por bloco temporal (h=20):")
    for b in range(BLOCKS):
        rn = [r for r in _rows(payload, novos_puros) if r.get("block") == b]
        an = _agg(rn)
        if an:
            print(f"    bloco {b}: {an['win']:6.1%} ({int(an['n'])}) ctl {an['ctl_win']:.1%}")

    print("\n  VEREDITO (h=20):")
    rn = _rows(payload, novos_puros)
    ok_novos = _verdict(
        "surgem > controle", _agg(_split(rn, "discovery")), _agg(_split(rn, "holdout"))
    )
    ra, rd = _rows(payload, paired_base), _rows(payload, paired_var)
    aa, ad = _agg(ra), _agg(rd)
    ok_re = aa is None or ad is None or ad["win"] >= aa["win"]
    print(
        f"  re-ancorados nao pioram: {'PASSA' if ok_re else 'falha'}"
        + (f" ({aa['win']:.1%} -> {ad['win']:.1%})" if aa and ad else "")
    )
    print(f"  => {variant} {'APROVADA' if ok_novos and ok_re else 'REPROVADA'}")


def report(payload: dict[str, Any]) -> None:
    annotate(payload)
    meta = payload["meta"]
    _section("H5 - CAPTURA POR DESLOCAMENTO")
    print(
        f"painel: {len(meta['symbols'])} simbolos x {meta['timeframes']} x "
        f"{meta['windows']} janelas | futuros: {meta['futures']}"
    )
    print(f"erros: {len(payload['errors'])}")
    print(
        f"displacement: {meta['displacement']} | "
        f"candles marcados (w0): {meta['displacement_candles_w0']}"
    )
    print(
        f"episodios baseline D0: {len(payload['episodes']['D0'])} | "
        f"com outcome: {len(payload['outcomes'])}"
    )
    for variant in ("D4", "D7"):
        report_variant(payload, variant)


def case_report(symbol: str, tf_name: str) -> None:
    tf = TFS[tf_name]
    data = load_dashboard_data(
        symbol=symbol, timeframe=tf, limit=VISIBLE_LIMIT, futures_provider=NoFuturesProvider()
    )
    print(f"\n=== {symbol} {tf_name}: displacement candles ===")
    for d in (MarketDirection.BULLISH, MarketDirection.BEARISH):
        for ts in displacement_candles(data.candles, d):
            print(f"  {ts}  {d.value}")
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
    parser.add_argument("--case", help="SIMBOLO:TF, ex ETHUSDT:H1")
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
