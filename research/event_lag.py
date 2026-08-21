"""Quando um evento de estrutura passa a EXISTIR? (atraso de confirmacao)

Um `MarketStructure` carrega o timestamp do candle que quebrou o nivel, mas so e
emitido depois que o pivo confirmador se formou — varios candles adiante. Medir
excursao a partir do timestamp do evento, portanto, usa informacao que ainda nao
existia, e infla qualquer metrica de continuacao: em
`research/control_continuation.py` o MFE/MAE de *todos* os grupos de evento
desaba para o nivel do controle assim que a entrada e adiada.

Este script mede o atraso REAL por replay incremental (`research/_replay.py`):
roda a pipeline de producao sobre prefixos crescentes da mesma serie e registra,
para cada evento final, o primeiro prefixo em que ele aparece.

Tambem mede *repaint*: eventos que aparecem, somem e voltam. Um evento instavel
nao e acionavel nem depois do atraso — ele ainda pode desaparecer.

Duas censuras conhecidas, ambas puxando o atraso medido para BAIXO:

- Eventos anteriores a `WARMUP` sao descartados (ja existem no primeiro prefixo
  avaliado, entao nao ha o que datar).
- Perto da borda direita so entram na lista final os eventos que ja confirmaram:
  um evento no candle 1490 com atraso real de 50 ainda nao teria sido emitido e
  nao aparece na amostra. A cauda longa e, portanto, subestimada.

    poetry run python -m research.event_lag
"""

import statistics as stats
from collections import Counter, defaultdict

from liquidity_hunter.app.dashboard_data import _run_internal_structure, default_ohlcv_provider
from liquidity_hunter.core.domain import StructureEvent, TimeFrame
from research._replay import confirmed_keys, scan_knowability

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TIMEFRAMES = [TimeFrame.M15, TimeFrame.H1, TimeFrame.H4]
LIMIT = 1500
WARMUP = 400


def _q(values: list[int], quantile: float) -> int:
    """Quantil simples sobre uma lista ja ordenada."""
    return values[min(len(values) - 1, int(quantile * len(values)))]


def main() -> None:
    provider = default_ohlcv_provider()
    lags: dict[StructureEvent, list[int]] = defaultdict(list)
    repaint: Counter[str] = Counter()

    for symbol in SYMBOLS:
        for tf in TIMEFRAMES:
            try:
                run = _run_internal_structure(provider, symbol, tf, LIMIT, False)
            except Exception as exc:  # noqa: BLE001 - varredura
                print(f"  ! {symbol} {tf.value}: {exc}")
                continue
            buffered = run.buffered_candles
            index = {c.timestamp: i for i, c in enumerate(buffered)}
            # Um evento anterior ao WARMUP ja existe no primeiro prefixo avaliado:
            # o replay registraria `first_seen = WARMUP` e o "atraso" seria a
            # distancia ate o inicio do scan, nao a confirmacao. Nao e datavel.
            targets = {
                key
                for key in confirmed_keys(run.events)
                if index.get(key[0], -1) >= WARMUP  # type: ignore[arg-type]
            }

            know = scan_knowability(
                buffered, targets, symbol=symbol, timeframe=tf, limit=LIMIT, warmup=WARMUP
            )
            for key, cut in know.first_seen.items():
                timestamp, event, _ = key
                i = index.get(timestamp)
                if i is None:
                    continue
                lags[event].append(cut - i)
                repaint["total"] += 1
                if key in know.unstable:
                    repaint["instavel"] += 1
            print(
                f"  {symbol:9} {tf.value:4} dataveis={len(targets):3} "
                f"medidos={len(know.first_seen):3} "
                f"nao_vistos={len(targets) - len(know.first_seen):3}"
            )

    print()
    print("=== atraso de confirmacao (candles apos o timestamp do evento) ===")
    for event, values in sorted(lags.items(), key=lambda kv: kv[0].value):
        values.sort()
        print(
            f"  {event.value:22} n={len(values):4}  mediana={stats.median(values):5.1f}  "
            f"p25={_q(values, 0.25):3}  p75={_q(values, 0.75):3}  "
            f"p90={_q(values, 0.90):3}  max={values[-1]:4}"
        )
    todos = sorted(v for vs in lags.values() for v in vs)
    if todos:
        print(f"  {'TODOS':22} n={len(todos):4}  mediana={stats.median(todos):5.1f}")
    if repaint["total"]:
        share = repaint["instavel"] / repaint["total"]
        print()
        print(
            f"repaint: {repaint['instavel']}/{repaint['total']} ({share:.1%}) dos eventos "
            "apareceram, sumiram e voltaram"
        )


if __name__ == "__main__":
    main()
