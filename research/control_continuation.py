"""Dinheiro novo sustenta o movimento? Continuacao apos BOS/CHoCH por quadrante de controle.

Tese do projeto (nunca medida): agressao com OI subindo (dinheiro novo) sustenta
a direcao; agressao com OI caindo (cobertura/liquidacao) nao sustenta. E dela que
vivem o card "Who's in Control", o `⚠ DON'T FADE` e a saturacao do Tide.

Metodo: para cada BOS/CHoCH nao-provisional, classifica o quadrante do
`MarketControlPoint` no candle do evento *em relacao a direcao do evento*, e mede
a excursao futura em N candles. O controle e sorteado no MESMO simbolo/timeframe
com a MESMA direcao — sem casar direcao, um periodo de alta faz qualquer coisa
parecer funcionar.

    poetry run python -m research.control_continuation
"""

import random
import statistics as stats
from collections import defaultdict

from liquidity_hunter.app import load_dashboard_data, load_timeframe_structure
from liquidity_hunter.app.dashboard_data import _HIGHER_TIMEFRAME_MAP
from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    OIRegime,
    StructureEvent,
    TimeFrame,
)

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
    "XRPUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT",
]
TIMEFRAMES = [TimeFrame.M15, TimeFrame.H1, TimeFrame.H4]
LIMIT = 1500
HORIZONS = [5, 10, 20, 40, 100]   # candles a frente
CONTROL_DRAWS = 20    # sorteios de controle por evento
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
    # (horizonte, grupo) -> linhas ; grupo condicional inclui a HTF na epoca
    groups: dict[tuple[int, str], list[tuple[float, float, float]]] = defaultdict(list)
    coverage = {"eventos": 0, "com_controle": 0, "com_htf": 0}

    for symbol in SYMBOLS:
        for tf in TIMEFRAMES:
            try:
                data = load_dashboard_data(
                    symbol=symbol, timeframe=tf, limit=LIMIT, compute_narrative=False
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  ! {symbol} {tf.value}: {exc}")
                continue
            candles = data.candles
            if not data.market_control or len(candles) < max(HORIZONS) + 2:
                continue
            regimes = {p.timestamp: p.regime for p in data.market_control.series}
            index = {c.timestamp: i for i, c in enumerate(candles)}

            # Tendencia da HTF *na epoca de cada evento*, nao a do snapshot.
            timeline: list[tuple[object, MarketDirection]] = []
            htf = _HIGHER_TIMEFRAME_MAP.get(tf)
            if htf is not None:
                try:
                    snap = load_timeframe_structure(symbol=symbol, timeframe=htf, limit=LIMIT)
                    timeline = htf_trend_timeline(snap.events)
                except Exception as exc:  # noqa: BLE001
                    print(f"  ! HTF {symbol} {htf.value}: {exc}")

            for ev in data.internal_structure_events:
                if ev.event not in TREND_EVENTS or ev.provisional:
                    continue
                i = index.get(ev.timestamp)
                if i is None:
                    continue
                coverage["eventos"] += 1
                regime = regimes.get(ev.timestamp)
                if regime is None:
                    continue
                coverage["com_controle"] += 1

                bullish = ev.direction is MarketDirection.BULLISH
                label = classify(regime, ev.direction)
                htf_dir = trend_at(timeline, ev.timestamp) if timeline else MarketDirection.NEUTRAL
                if htf_dir is not MarketDirection.NEUTRAL:
                    coverage["com_htf"] += 1
                aligned = htf_dir is ev.direction

                for h in HORIZONS:
                    if i + h >= len(candles):
                        continue
                    row = excursion(candles, i, bullish, h)
                    groups[(h, label)].append(row)
                    if htf_dir is not MarketDirection.NEUTRAL:
                        side = "HTF_alinhado" if aligned else "HTF_contra"
                        groups[(h, side)].append(row)
                        groups[(h, f"{label}+{side}")].append(row)
                    for _ in range(CONTROL_DRAWS):
                        j = rng.randint(0, len(candles) - h - 2)
                        groups[(h, "CONTROLE")].append(excursion(candles, j, bullish, h))

            print(f"  {symbol:9} {tf.value:4} ok")

    print()
    print(
        f"eventos: {coverage['eventos']}  com OI: {coverage['com_controle']}"
        f"  com HTF: {coverage['com_htf']}"
    )
    print("controle casado em simbolo + timeframe + direcao")

    print()
    print("=== 1. varredura de horizonte (quadrante do controle) ===")
    for h in HORIZONS:
        print(f"\n  horizonte {h} candles")
        for label in ("dinheiro_novo", "saida", "flat", "CONTROLE"):
            print(f"    {label:15} {summarize(groups[(h, label)])}")

    print()
    print("=== 2. condicionado a HTF na epoca do evento ===")
    for h in HORIZONS:
        print(f"\n  horizonte {h} candles")
        for label in (
            "HTF_alinhado",
            "HTF_contra",
            "dinheiro_novo+HTF_alinhado",
            "saida+HTF_alinhado",
            "dinheiro_novo+HTF_contra",
            "saida+HTF_contra",
            "CONTROLE",
        ):
            print(f"    {label:27} {summarize(groups[(h, label)])}")


if __name__ == "__main__":
    main()
