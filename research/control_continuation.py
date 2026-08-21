"""Dinheiro novo sustenta o movimento? Continuacao apos BOS/CHoCH por quadrante de controle.

Tese do projeto (nunca medida): agressao com OI subindo (dinheiro novo) sustenta
a direcao; agressao com OI caindo (cobertura/liquidacao) nao sustenta. E dela que
vivem o card "Who's in Control", o `⚠ DON'T FADE` e a saturacao do Tide.

Metodo: para cada BOS/CHoCH nao-provisional, classifica o quadrante do
`MarketControlPoint` no candle do evento *em relacao a direcao do evento*, e mede
a excursao futura em N candles. O controle e sorteado no MESMO simbolo/timeframe
com a MESMA direcao — sem casar direcao, um periodo de alta faz qualquer coisa
parecer funcionar.

A entrada e medida de duas formas, lado a lado:

- `timestamp`: a partir do candle que o evento carrega. **Enviesada** — o evento
  so e emitido depois que o pivo confirmador se formou, entao esse candle usa
  informacao que ainda nao existia. Mantida so para dimensionar o vies.
- `conhecivel`: a partir do primeiro candle em que a pipeline de producao
  realmente emite aquele evento, datado por replay incremental
  (`research/_replay.py`). E a unica entrada que alguem poderia ter tomado.

    poetry run python -m research.control_continuation
"""

import random
import statistics as stats
from collections import Counter, defaultdict

from liquidity_hunter.app import load_dashboard_data, load_timeframe_structure
from liquidity_hunter.app.dashboard_data import (
    _HIGHER_TIMEFRAME_MAP,
    _run_internal_structure,
    default_ohlcv_provider,
)
from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    OIRegime,
    StructureEvent,
    TimeFrame,
)
from research._replay import confirmed_keys, key_of, scan_knowability

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
    "XRPUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT",
]
TIMEFRAMES = [TimeFrame.M15, TimeFrame.H1, TimeFrame.H4]
LIMIT = 1500
HORIZONS = [5, 10, 20, 40, 100]   # candles a frente
CONTROL_DRAWS = 20    # sorteios de controle por evento
# As duas entradas comparadas: o candle do evento (enviesado) e o primeiro em
# que ele existe de fato (datado por replay). Ver o docstring.
ENTRIES = ["timestamp", "conhecivel"]
WARMUP = 400  # primeiro prefixo avaliado no replay (bootstrap da pipeline)
SEED = 7

TREND_EVENTS = {StructureEvent.BREAK_OF_STRUCTURE, StructureEvent.CHANGE_OF_CHARACTER}
BULLISH_AGGRESSION = {OIRegime.LONG_BUILDUP, OIRegime.SHORT_COVERING}
BEARISH_AGGRESSION = {OIRegime.SHORT_BUILDUP, OIRegime.LONG_LIQUIDATION}
NEW_MONEY = {OIRegime.LONG_BUILDUP, OIRegime.SHORT_BUILDUP}


def classify(regime: OIRegime, direction: MarketDirection) -> str:
    """O quadrante do controle, lido em relacao a direcao do evento."""
    if regime is OIRegime.FLAT:
        return "flat"
    bullish_event = direction is MarketDirection.BULLISH
    agrees = regime in (BULLISH_AGGRESSION if bullish_event else BEARISH_AGGRESSION)
    if not agrees:
        return "contra"          # agressao no sentido oposto ao evento
    return "dinheiro_novo" if regime in NEW_MONEY else "saida"


def excursion(
    candles: list[Candle], i: int, bullish: bool, horizon: int
) -> tuple[float, float, float]:
    """(retorno assinado em N, MFE, MAE) em % do preco de entrada."""
    entry = candles[i].close
    fwd = candles[i + 1 : i + 1 + horizon]
    sign = 1.0 if bullish else -1.0
    ret = sign * (fwd[-1].close - entry) / entry * 100
    highs = max(c.high for c in fwd)
    lows = min(c.low for c in fwd)
    mfe = (highs - entry) / entry * 100 if bullish else (entry - lows) / entry * 100
    mae = (entry - lows) / entry * 100 if bullish else (highs - entry) / entry * 100
    return ret, mfe, mae


def htf_trend_timeline(events: list[MarketStructure]) -> list[tuple[object, MarketDirection]]:
    """Replay da tendencia da HTF ao longo do tempo (a regra do backend).

    Precisa ser *na epoca do evento*: `higher_timeframe_direction` e um unico
    valor do snapshot atual, e condicionar historico nele seria anacronico.
    """
    timeline: list[tuple[object, MarketDirection]] = []
    trend = MarketDirection.NEUTRAL
    for ev in sorted(events, key=lambda e: e.timestamp):
        if ev.provisional:
            continue
        if ev.event in TREND_EVENTS:
            trend = ev.direction
        elif ev.event is StructureEvent.CHOCH_FAILED:
            trend = (
                MarketDirection.BEARISH
                if ev.direction is MarketDirection.BULLISH
                else MarketDirection.BULLISH
            )
        else:
            continue
        timeline.append((ev.timestamp, trend))
    return timeline


def trend_at(timeline: list[tuple[object, MarketDirection]], ts: object) -> MarketDirection:
    lo, hi = 0, len(timeline)
    while lo < hi:
        mid = (lo + hi) // 2
        if timeline[mid][0] <= ts:  # type: ignore[operator]
            lo = mid + 1
        else:
            hi = mid
    return timeline[lo - 1][1] if lo else MarketDirection.NEUTRAL


def summarize(rows: list[tuple[float, float, float]]) -> str:
    if len(rows) < 5:
        return f"n={len(rows):4}  (amostra pequena demais)"
    rets = [r[0] for r in rows]
    mfes = [r[1] for r in rows]
    maes = [r[2] for r in rows]
    held = sum(r > 0 for r in rets) / len(rets)
    # MFE/MAE e sem escala: sobe so se um lado abriu mais que o outro.
    ratio = stats.mean(mfes) / stats.mean(maes) if stats.mean(maes) else float("nan")
    return (
        f"n={len(rets):5}  a favor {held:5.1%}  MFE/MAE {ratio:4.2f}  "
        f"ret {stats.mean(rets):+6.2f}% (med {stats.median(rets):+6.2f}%)"
    )


def main() -> None:
    rng = random.Random(SEED)
    provider = default_ohlcv_provider()
    # (entrada, horizonte, grupo) -> linhas
    groups: dict[tuple[str, int, str], list[tuple[float, float, float]]] = defaultdict(list)
    coverage = Counter()

    for symbol in SYMBOLS:
        for tf in TIMEFRAMES:
            try:
                data = load_dashboard_data(
                    symbol=symbol, timeframe=tf, limit=LIMIT, compute_narrative=False
                )
                run = _run_internal_structure(provider, symbol, tf, LIMIT, False)
            except Exception as exc:  # noqa: BLE001 - varredura, um par ausente nao para a corrida
                print(f"  ! {symbol} {tf.value}: {exc}")
                continue
            candles = data.candles
            if not data.market_control or len(candles) < max(HORIZONS) + 2:
                continue
            regimes = {p.timestamp: p.regime for p in data.market_control.series}
            index = {c.timestamp: i for i, c in enumerate(candles)}

            # Quando cada evento passa a existir. O replay roda sobre a serie
            # bufferizada; o resultado volta em timestamp para casar com a
            # janela visivel mesmo que um candle novo tenha impresso no meio.
            events = [
                ev
                for ev in data.internal_structure_events
                if ev.event in TREND_EVENTS and not ev.provisional
            ]
            # Um evento anterior ao WARMUP ja existe no primeiro prefixo
            # avaliado: o replay o dataria em WARMUP, o que seria a distancia
            # ate o inicio do scan e nao um atraso de confirmacao. Nao e datavel.
            buffered_index = {c.timestamp: n for n, c in enumerate(run.buffered_candles)}
            targets = {
                key
                for key in confirmed_keys(events)
                if buffered_index.get(key[0], -1) >= WARMUP  # type: ignore[arg-type]
            }
            know = scan_knowability(
                run.buffered_candles,
                targets,
                symbol=symbol,
                timeframe=tf,
                limit=LIMIT,
                warmup=WARMUP,
            )
            knowable_ts = {
                key: run.buffered_candles[cut].timestamp for key, cut in know.first_seen.items()
            }

            # Tendencia da HTF *na epoca de cada evento*, nao a do snapshot.
            timeline: list[tuple[object, MarketDirection]] = []
            htf = _HIGHER_TIMEFRAME_MAP.get(tf)
            if htf is not None:
                try:
                    snap = load_timeframe_structure(symbol=symbol, timeframe=htf, limit=LIMIT)
                    timeline = htf_trend_timeline(snap.events)
                except Exception as exc:  # noqa: BLE001
                    print(f"  ! HTF {symbol} {htf.value}: {exc}")

            for ev in events:
                i = index.get(ev.timestamp)
                if i is None:
                    continue
                coverage["eventos"] += 1
                regime = regimes.get(ev.timestamp)
                if regime is None:
                    continue
                coverage["com_controle"] += 1

                entries: dict[str, int] = {"timestamp": i}
                known_ts = knowable_ts.get(key_of(ev))
                known_i = index.get(known_ts) if known_ts is not None else None
                if known_i is not None:
                    entries["conhecivel"] = known_i
                    coverage["datados"] += 1
                    coverage["atraso_total"] += known_i - i
                    if key_of(ev) in know.unstable:
                        coverage["instaveis"] += 1

                bullish = ev.direction is MarketDirection.BULLISH
                label = classify(regime, ev.direction)
                htf_dir = trend_at(timeline, ev.timestamp) if timeline else MarketDirection.NEUTRAL
                aligned = htf_dir is ev.direction

                for h in HORIZONS:
                    for entry_name, entry in entries.items():
                        if entry + h >= len(candles):
                            continue
                        row = excursion(candles, entry, bullish, h)
                        groups[(entry_name, h, label)].append(row)
                        if htf_dir is not MarketDirection.NEUTRAL:
                            side = "HTF_alinhado" if aligned else "HTF_contra"
                            groups[(entry_name, h, f"{label}+{side}")].append(row)
                    for _ in range(CONTROL_DRAWS):
                        j = rng.randint(0, len(candles) - h - 2)
                        row = excursion(candles, j, bullish, h)
                        for entry_name in ENTRIES:
                            groups[(entry_name, h, "CONTROLE")].append(row)

            print(f"  {symbol:9} {tf.value:4} ok")

    print()
    print(
        f"eventos: {coverage['eventos']}  com OI: {coverage['com_controle']}  "
        f"datados por replay: {coverage['datados']}"
    )
    if coverage["datados"]:
        print(
            f"atraso medio de confirmacao: "
            f"{coverage['atraso_total'] / coverage['datados']:.1f} candles  |  "
            f"repaint (apareceu, sumiu, voltou): "
            f"{coverage['instaveis']}/{coverage['datados']} "
            f"({coverage['instaveis'] / coverage['datados']:.1%})"
        )
    print("controle casado em simbolo + timeframe + direcao")

    print()
    print("=== entrada no timestamp (enviesada) vs no candle conhecivel (honesta) ===")
    print("celula = (a favor %)|(MFE/MAE)")
    for h in HORIZONS:
        print(f"\n  horizonte {h} candles")
        print(f"    {'grupo':27} " + " ".join(f"{name:>10}" for name in ENTRIES))
        for label in (
            "dinheiro_novo",
            "saida",
            "flat",
            "dinheiro_novo+HTF_alinhado",
            "saida+HTF_alinhado",
            "CONTROLE",
        ):
            cells = [_cell(groups[(name, h, label)]) for name in ENTRIES]
            print(f"    {label:27} " + " ".join(cells))


def _cell(rows: list[tuple[float, float, float]]) -> str:
    """`(a favor %)|(MFE/MAE)` — as duas metricas sem escala, lado a lado."""
    rets = [r[0] for r in rows]
    held = sum(r > 0 for r in rets) / len(rets) * 100
    mfe = stats.mean(r[1] for r in rows)
    mae = stats.mean(r[2] for r in rows)
    return f"{held:4.0f}%|{mfe / mae if mae else float('nan'):4.2f}"


if __name__ == "__main__":
    main()
