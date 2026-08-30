"""A falha do CHoCH fraco deveria exigir o pivô contrário? (medição da flag)

A observação sob teste
----------------------
Um CHoCH de baixa quebra um **fundo**. O preço voltar por cima desse fundo é o
retrace mais ordinário que existe depois de uma reversão — e hoje é o que mata
o CHoCH (`choch_weak_ref_fail_at_broken_level`). SOLUSDT H4 2026-07-13: CHoCH
em 74.06 contra a referência fraca 76.24, morto em 07-14 por um fechamento de
77.35 que ficou **abaixo de todos os topos mais baixos da perna** (79.64 /
78.86 / 78.19); o preço então caiu para 72.30 e o primeiro BOS de baixa da
perna chegou quatro dias atrasado.

`choch_weak_fail_clear_counter_pivot` arma a falha no pivô contrário mais
próximo (trailing, com piso no nível quebrado). A pergunta desta medição é
simples e é a única que autoriza ligar a flag:

    **Os ✕ que a regra apaga são os ✕ ERRADOS?**

O que é medido
--------------
Cada combo roda duas vezes (flag off / on) sobre as MESMAS velas. Cada
`CHOCH_FAILED` não-provisional do baseline é emparelhado com o CHoCH que ele
mata e classificado:

* ``removed``  — a regra apagou essa falha;
* ``delayed``  — a mesma falha (mesma direção) sobrevive, mas mais tarde;
* ``kept``     — inalterada.

A métrica é a de `choch_fail_reversion.py`, para poder comparar: ``resumed@H``
= algum dos H candles seguintes ao ✕ **fecha além do extremo do próprio CHoCH**
na direção do CHoCH. Se a reversão retomou, o ✕ estava errado.

Leitura: a regra se justifica se ``removed`` retomar **mais** que ``kept``.
Empate significa que ela está apagando falhas ao acaso — e aí o custo em
fixtures não se paga. O placebo casado em impulso entra como piso da escala (a
regra da disciplina: placebo > aleatório), medido no candle que matou.

Uso
---
    poetry run python research/choch_weak_fail_counter_pivot.py
    poetry run python research/choch_weak_fail_counter_pivot.py \
        --symbols BTCUSDT SOLUSDT --timeframes 4h 1d
"""

from __future__ import annotations

import argparse
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.core.domain.enums import MarketDirection, StructureEvent

BULL = MarketDirection.BULLISH
FLAG = "_CHOCH_WEAK_FAIL_CLEAR_COUNTER_PIVOT"

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "LINKUSDT",
    "AAVEUSDT",
    "DOGEUSDT",
    "NEARUSDT",
    "AVAXUSDT",
    "ENAUSDT",
    "ADAUSDT",
]
TIMEFRAMES = ["15m", "1h", "4h", "1d"]
HORIZONS = (10, 20, 40, 80)


@dataclass
class Ev:
    arm: str  # "removed" | "delayed" | "kept" | "momentum"
    symbol: str
    timeframe: str
    direction: MarketDirection
    index: int
    target: float
    life: int
    delay: int = 0  # candles the failure was pushed back (arm "delayed")
    resumed: dict[int, bool] = field(default_factory=dict)


def _atr(candles: Sequence[Candle]) -> float:
    trs = [
        max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
        for p, c in zip(candles, candles[1:], strict=False)
    ]
    return statistics.mean(trs) if trs else 0.0


def _measure(ev: Ev, candles: Sequence[Candle], horizons: Sequence[int]) -> Ev:
    for h in horizons:
        window = candles[ev.index + 1 : ev.index + 1 + h]
        if not window:
            continue
        ev.resumed[h] = any(
            (c.close > ev.target) if ev.direction is BULL else (c.close < ev.target)
            for c in window
        )
    return ev


def _load(symbol: str, timeframe: TimeFrame, limit: int, flag: bool):  # type: ignore[no-untyped-def]
    setattr(dd, FLAG, flag)
    return dd.load_dashboard_data(
        symbol=symbol, timeframe=timeframe, limit=limit, compute_narrative=False
    )


def run_combo(
    symbol: str,
    timeframe: TimeFrame,
    *,
    limit: int,
    horizons: Sequence[int],
    rng: random.Random,
) -> list[Ev]:
    base = _load(symbol, timeframe, limit, False)
    flagged = _load(symbol, timeframe, limit, True)
    candles = base.candles
    if len(candles) < 120:
        return []
    index_of = {c.timestamp: i for i, c in enumerate(candles)}
    atr = _atr(candles)
    if atr <= 0:
        return []

    events = base.internal_structure_events
    after = [
        e
        for e in flagged.internal_structure_events
        if e.event is StructureEvent.CHOCH_FAILED and not e.provisional
    ]
    out: list[Ev] = []

    for position, failure in enumerate(events):
        if failure.event is not StructureEvent.CHOCH_FAILED or failure.provisional:
            continue
        choch = next(
            (
                e
                for e in reversed(events[:position])
                if e.event is StructureEvent.CHANGE_OF_CHARACTER
                and e.direction is failure.direction
                and not e.provisional
            ),
            None,
        )
        if choch is None:
            continue
        i0 = index_of.get(failure.timestamp)
        i_choch = index_of.get(choch.timestamp)
        if i0 is None or i_choch is None:
            continue

        # Same failure in the flagged stream? Same direction, and no earlier
        # than the CHoCH that armed it -- a later one is the same failure
        # confirming at the trailing level, an absent one is a real removal.
        same = [
            e
            for e in after
            if e.direction is failure.direction
            and (j := index_of.get(e.timestamp)) is not None
            and i_choch < j <= i0 + 200
        ]
        if not same:
            arm, delay = "removed", 0
        else:
            j = min(index_of[e.timestamp] for e in same)
            arm, delay = ("kept", 0) if j == i0 else ("delayed", j - i0)

        out.append(
            _measure(
                Ev(arm, symbol, timeframe.value, failure.direction, i0,
                   choch.price_level, i0 - i_choch, delay),
                candles,
                horizons,
            )
        )

        # Momentum-matched placebo at the killing candle (the floor of the
        # scale: is any of this better than "price that just ran keeps
        # running?").
        killing_close = candles[i0].close
        distance = abs(choch.price_level - killing_close)
        thrust = abs(killing_close - candles[max(i0 - 2, 0)].close)
        pool = [
            j
            for j in range(60, len(candles) - max(horizons) - 1)
            if (
                (candles[j].close - candles[j - 2].close) <= -thrust
                if failure.direction is BULL
                else (candles[j].close - candles[j - 2].close) >= thrust
            )
        ]
        for j in rng.sample(pool, min(5, len(pool))):
            b = candles[j].close
            out.append(
                _measure(
                    Ev(f"momentum:{arm}", symbol, timeframe.value, failure.direction, j,
                       b + distance if failure.direction is BULL else b - distance,
                       0, 0),
                    candles,
                    horizons,
                )
            )
    return out


def report(events: Sequence[Ev], horizons: Sequence[int]) -> None:
    print(f"\n{'arm':>17} {'N':>5} " + " ".join(f"{'resumed@' + str(h):>12}" for h in horizons))
    arms = ("removed", "momentum:removed", "delayed", "momentum:delayed",
            "kept", "momentum:kept")
    for arm in arms:
        rows = [e for e in events if e.arm == arm]
        if not rows:
            continue
        cells = []
        for h in horizons:
            vals = [e.resumed[h] for e in rows if h in e.resumed]
            cells.append(f"{(sum(vals) / len(vals) * 100 if vals else 0):>11.1f}%")
        print(f"{arm:>17} {len(rows):>5} " + " ".join(cells))

    real = [e for e in events if not e.arm.startswith("momentum")]
    lives = [e.life for e in real]
    if lives:
        lives.sort()
        q = statistics.quantiles(lives, n=4) if len(lives) > 3 else [0, 0, 0]
        print(f"\nvida do CHoCH ate o ✕: mediana {statistics.median(lives):.0f} "
              f"candles (p25 {q[0]:.0f}, p75 {q[2]:.0f})")
    delays = [e.delay for e in events if e.arm == "delayed"]
    if delays:
        print(f"atraso da falha adiada: mediana {statistics.median(delays):.0f} candles "
              f"(max {max(delays)})")

    print("\npor timeframe (resumed@40, removed vs kept):")
    for tf in sorted({e.timeframe for e in real}):
        rem = [e.resumed[40] for e in events
               if e.arm == "removed" and e.timeframe == tf and 40 in e.resumed]
        kep = [e.resumed[40] for e in events
               if e.arm == "kept" and e.timeframe == tf and 40 in e.resumed]
        if not rem and not kep:
            continue
        r = f"{sum(rem) / len(rem) * 100:5.1f}% (n={len(rem):>3})" if rem else "     -- (n=  0)"
        k = f"{sum(kep) / len(kep) * 100:5.1f}% (n={len(kep):>3})" if kep else "     -- (n=  0)"
        print(f"  {tf:>4}  removed {r}   kept {k}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS)
    parser.add_argument("--timeframes", nargs="+", default=TIMEFRAMES)
    parser.add_argument("--limit", type=int, default=1200)
    parser.add_argument("--horizons", nargs="+", type=int, default=list(HORIZONS))
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    rng = random.Random(args.seed)

    original = getattr(dd, FLAG)
    collected: list[Ev] = []
    try:
        for symbol in args.symbols:
            for name in args.timeframes:
                try:
                    evs = run_combo(
                        symbol, TimeFrame(name),
                        limit=args.limit, horizons=args.horizons, rng=rng,
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"  {symbol} {name}: {exc}")
                    continue
                real = [e for e in evs if not e.arm.startswith("momentum")]
                counts = {a: sum(1 for e in real if e.arm == a)
                          for a in ("removed", "delayed", "kept")}
                print(f"  {symbol} {name}: {len(real)} ✕  {counts}")
                collected.extend(evs)
    finally:
        setattr(dd, FLAG, original)
    report(collected, args.horizons)


if __name__ == "__main__":
    main()
