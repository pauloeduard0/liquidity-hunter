"""As marcas provisorias (`BOS?` / `CHoCH?`) chegam cedo o bastante para valer?

`research/control_continuation.py` mostrou que o evento *confirmado* chega tarde:
mediana de 13.5 candles apos o candle que ele marca, e entrado nesse momento ele
nao oferece assimetria de excursao melhor que um candle sorteado na mesma
direcao. Mas o projeto ja tem a versao adiantada disso — a marca provisoria, que
existe justamente para sinalizar o movimento antes dos pivos se formarem.

Sabia-se que ~85% dos `BOS?` e ~50% dos `CHoCH?` confirmam. Nunca se mediu se
eles *valem*: confirmar nao e pagar.

Metodo: replay incremental (`research/_replay.py`). Em cada prefixo, coleta as
marcas provisorias emitidas; a entrada e o candle do prefixo em que a marca
aparece pela primeira vez — o ultimo candle fechado naquele momento. Por
construcao nao ha lookahead: aquele prefixo e tudo que existia. O controle e
sorteado na mesma serie com a MESMA direcao.

Marcas provisorias vem em rajada (a mesma perna re-emite a marca em candles
seguidos com timestamps ligeiramente diferentes), entao marcas da mesma direcao
dentro de `DEDUP_CANDLES` contam como uma so — sem isso uma unica perna domina a
amostra.

    poetry run python -m research.provisional_edge
"""

import random
import statistics as stats
from collections import Counter, defaultdict

from liquidity_hunter.app.dashboard_data import _run_internal_structure, default_ohlcv_provider
from liquidity_hunter.core.domain import Candle, MarketDirection, StructureEvent, TimeFrame
from research._replay import confirmed_keys, scan_first_emissions

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "LINKUSDT"]
TIMEFRAMES = [TimeFrame.M15, TimeFrame.H1, TimeFrame.H4]
LIMIT = 1500
HORIZONS = [5, 10, 20, 40]
BREAKDOWN_HORIZON = 10     # horizonte usado no recorte por simbolo
CONTROL_DRAWS = 20
WARMUP = 400
DEDUP_CANDLES = 5          # marcas da mesma direcao dentro disso sao a mesma
CONFIRM_WINDOW = 5         # proximidade para casar a marca com o evento confirmado
SEED = 11


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


def cell(rows: list[tuple[float, float, float]]) -> str:
    """`n | (a favor %) | (MFE/MAE)` — as duas metricas sem escala."""
    if len(rows) < 5:
        return f"{'n/a':>17}"
    rets = [r[0] for r in rows]
    held = sum(r > 0 for r in rets) / len(rets) * 100
    mfe = stats.mean(r[1] for r in rows)
    mae = stats.mean(r[2] for r in rows)
    ratio = mfe / mae if mae else float("nan")
    return f"n={len(rets):4} {held:4.0f}% {ratio:5.2f}"


def main() -> None:
    rng = random.Random(SEED)
    groups: dict[tuple[int, str], list[tuple[float, float, float]]] = defaultdict(list)
    # A conclusao so vale se sobreviver simbolo a simbolo: um agregado pode ser
    # um ativo puxando os outros.
    by_symbol: dict[tuple[str, str], list[tuple[float, float, float]]] = defaultdict(list)
    stats_counter: Counter[str] = Counter()
    ages: list[int] = []

    provider = default_ohlcv_provider()
    for symbol in SYMBOLS:
        for tf in TIMEFRAMES:
            try:
                run = _run_internal_structure(provider, symbol, tf, LIMIT, False)
            except Exception as exc:  # noqa: BLE001 - varredura
                print(f"  ! {symbol} {tf.value}: {exc}")
                continue
            buffered = run.buffered_candles
            index = {c.timestamp: i for i, c in enumerate(buffered)}
            marks = scan_first_emissions(
                buffered,
                symbol=symbol,
                timeframe=tf,
                limit=LIMIT,
                warmup=WARMUP,
                provisional=True,
            )
            final_confirmed = confirmed_keys(run.events)

            # Rajada: uma perna re-emite a marca em candles seguidos.
            accepted: list[tuple[int, MarketDirection]] = []
            for key, cut in sorted(marks.items(), key=lambda kv: kv[1]):
                timestamp, event, direction = key
                if any(
                    direction is prev_dir and cut - prev_cut < DEDUP_CANDLES
                    for prev_cut, prev_dir in accepted
                ):
                    stats_counter["dedup"] += 1
                    continue
                accepted.append((cut, direction))

                label = "BOS?" if event is StructureEvent.BREAK_OF_STRUCTURE else "CHoCH?"
                stats_counter[label] += 1
                i = index.get(timestamp)
                if i is not None:
                    ages.append(cut - i)

                # A marca virou evento confirmado depois?
                confirmed = any(
                    ev_event is event
                    and ev_dir is direction
                    and abs(index.get(ev_ts, -10**6) - (i if i is not None else -10**6))
                    <= CONFIRM_WINDOW
                    for ev_ts, ev_event, ev_dir in final_confirmed
                )
                stats_counter[f"{label}_confirmou"] += int(confirmed)

                bullish = direction is MarketDirection.BULLISH
                for h in HORIZONS:
                    if cut + h >= len(buffered):
                        continue
                    row = excursion(buffered, cut, bullish, h)
                    groups[(h, label)].append(row)
                    groups[(h, "TODAS")].append(row)
                    if h == BREAKDOWN_HORIZON:
                        by_symbol[(symbol, "marcas")].append(row)
                    for _ in range(CONTROL_DRAWS):
                        j = rng.randint(WARMUP, len(buffered) - h - 2)
                        control_row = excursion(buffered, j, bullish, h)
                        groups[(h, "CONTROLE")].append(control_row)
                        if h == BREAKDOWN_HORIZON:
                            by_symbol[(symbol, "controle")].append(control_row)

            print(f"  {symbol:9} {tf.value:4} marcas={len(accepted):3}")

    print()
    for label in ("BOS?", "CHoCH?"):
        total = stats_counter[label]
        if total:
            rate = stats_counter[f"{label}_confirmou"] / total
            print(f"{label:7} n={total:4}  viraram evento confirmado: {rate:5.1%}")
    if ages:
        print(f"idade da marca ao aparecer: mediana {stats.median(ages):.1f} candles")
    print(f"marcas descartadas por rajada: {stats_counter['dedup']}")

    print()
    print("=== entrada no candle em que a marca aparece (sem lookahead) ===")
    print("celula = n | a favor % | MFE/MAE")
    for h in HORIZONS:
        print(f"\n  horizonte {h} candles")
        for label in ("BOS?", "CHoCH?", "TODAS", "CONTROLE"):
            print(f"    {label:10} {cell(groups[(h, label)])}")

    print()
    print(f"=== por simbolo (horizonte {BREAKDOWN_HORIZON}) — a conclusao sobrevive isolada? ===")
    for symbol in SYMBOLS:
        marks = by_symbol[(symbol, "marcas")]
        ctrl = by_symbol[(symbol, "controle")]
        print(f"  {symbol:9} marcas {cell(marks)}   controle {cell(ctrl)}")


if __name__ == "__main__":
    main()
