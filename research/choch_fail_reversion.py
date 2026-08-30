"""CHoCH ✕ — quantos morrem cedo? (e o excesso do fechamento que os mata diz algo?)

A observação sob teste
----------------------
SOLUSDT H4 2026-07-13: um CHoCH de baixa em 74.06 (referência 76.24) vive **um
dia**. Em 07-14 12h um repique fecha em 77.35, 0.26 ATR acima do gatilho
(nível + `choch_fail_level_buffer_atr` = 76.97), e o `CHOCH_FAILED` dispara.
Nas duas semanas seguintes o preço faz 73.32, 72.26, 70.51 — a reversão que o
✕ declarou morta estava certa.

A pergunta é se isso é regra ou anedota: **com que frequência a direção do
CHoCH retoma depois do ✕**, e se o *excesso* do fechamento que mata (o quanto
ele passa do gatilho, em ATR) separa os ✕ prematuros dos corretos. Se separar,
uma barreira (banda maior ou persistência na falha) tem onde morder; se não
separar, não há o que ajustar e o ✕ é só ruído irredutível.

O que é medido
--------------
Para cada `CHOCH_FAILED` não-provisional do stream **interno** (o que o gráfico
desenha), emparelhado com o CHoCH que ele mata:

* ``resumed@H`` — algum candle nos H seguintes **fecha além do extremo do
  próprio CHoCH** (`price_level`) na direção do CHoCH. É a mesma prova de
  retomada que `_drop_resumed_fizzle_markers` usa, e é o que torna o ✕
  retrospectivamente prematuro.
* ``excess`` — (fechamento que matou − gatilho) / ATR, o quanto o reclaim
  passou da banda.
* ``life`` — candles entre o CHoCH e o ✕.

Controle direcionalmente casado (a lição de `raid_reversal.py`: controle sem
casar direção mede tendência, não o evento). Para cada ✕ sorteia-se um candle
aleatório da mesma série; o alvo é posto à **mesma distância em ATR** do
fechamento, na **mesma direção**, e mede-se o mesmo "fecha além em H candles".
Sem isso, "60% retomam" pode ser só o que qualquer nível a 1 ATR faz.

Ressalva de honestidade: o ✕ é avaliado como **desenhado** (o timestamp do
evento), não como conhecível em tempo real — é a pergunta certa aqui, porque o
que se julga é a marca que o usuário vê no gráfico, não uma entrada.

Uso
---
    poetry run python research/choch_fail_reversion.py
    poetry run python research/choch_fail_reversion.py --symbols BTCUSDT SOLUSDT \
        --timeframes 4h 1d --horizons 10 20 40 80
"""

from __future__ import annotations

import argparse
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

from liquidity_hunter.app import load_dashboard_data
from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.core.domain.enums import MarketDirection, StructureEvent

BULL = MarketDirection.BULLISH
SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "LINKUSDT",
    "AAVEUSDT",
    "DOGEUSDT",
]
TIMEFRAMES = ["15m", "1h", "4h", "1d"]
HORIZONS = (10, 20, 40, 80)


@dataclass
class Ev:
    arm: str  # "fail" | "random"
    symbol: str
    timeframe: str
    direction: MarketDirection  # the direction the killed CHoCH argued for
    index: int
    target: float  # close beyond this = the reversal resumed
    excess_atr: float  # how far past the failure trigger the killing close went
    life: int  # candles the CHoCH lived before the failure
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


def run_combo(
    symbol: str,
    timeframe: TimeFrame,
    *,
    limit: int,
    horizons: Sequence[int],
    buffer_atr: float,
    rng: random.Random,
) -> list[Ev]:
    data = load_dashboard_data(
        symbol=symbol, timeframe=timeframe, limit=limit, compute_narrative=False
    )
    candles = data.candles
    if len(candles) < 120:
        return []
    index_of = {c.timestamp: i for i, c in enumerate(candles)}
    atr = _atr(candles)
    if atr <= 0:
        return []
    events = data.internal_structure_events
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
        # The trigger the reclaim had to clear: the armed level plus the noise
        # band. `reference_price_level` is that level for a level-armed failure.
        level = failure.reference_price_level
        killing_close = candles[i0].close
        if level is None:
            excess = float("nan")
        elif failure.direction is BULL:
            # A bullish CHoCH is killed by closes *below* the level.
            excess = ((level - buffer_atr * atr) - killing_close) / atr
        else:
            excess = (killing_close - (level + buffer_atr * atr)) / atr
        out.append(
            _measure(
                Ev(
                    arm="fail",
                    symbol=symbol,
                    timeframe=timeframe.value,
                    direction=failure.direction,
                    index=i0,
                    target=choch.price_level,
                    excess_atr=excess,
                    life=i0 - i_choch,
                ),
                candles,
                horizons,
            )
        )
        # Direction-matched placebo: same distance to target, random anchor.
        distance = abs(choch.price_level - killing_close)
        # ...and a *momentum*-matched placebo (the "placebo > aleatório" rule):
        # the killing candle is not a random candle, it just closed hard
        # against the CHoCH. Anchors that did the same thing over two closes
        # answer the sharper question -- is the ✕ informative, or is it only
        # "price that just ran keeps running?"
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
            base = candles[j].close
            target = base + distance if failure.direction is BULL else base - distance
            out.append(
                _measure(
                    Ev(
                        arm="momentum",
                        symbol=symbol,
                        timeframe=timeframe.value,
                        direction=failure.direction,
                        index=j,
                        target=target,
                        excess_atr=float("nan"),
                        life=0,
                    ),
                    candles,
                    horizons,
                )
            )
        for _ in range(5):
            j = rng.randrange(60, len(candles) - max(horizons) - 1)
            base = candles[j].close
            target = base + distance if failure.direction is BULL else base - distance
            out.append(
                _measure(
                    Ev(
                        arm="random",
                        symbol=symbol,
                        timeframe=timeframe.value,
                        direction=failure.direction,
                        index=j,
                        target=target,
                        excess_atr=float("nan"),
                        life=0,
                    ),
                    candles,
                    horizons,
                )
            )
    return out


def _rate(evs: Sequence[Ev], horizon: int) -> tuple[int, float]:
    vals = [e.resumed[horizon] for e in evs if horizon in e.resumed]
    return len(vals), (100 * sum(vals) / len(vals) if vals else float("nan"))


def report(evs: Sequence[Ev], horizons: Sequence[int]) -> None:
    fails = [e for e in evs if e.arm == "fail"]
    randoms = [e for e in evs if e.arm == "random"]
    moment = [e for e in evs if e.arm == "momentum"]
    print(
        f"\n{len(fails)} CHoCH ✕ (não-provisionais), {len(randoms)} controles aleatórios, "
        f"{len(moment)} controles casados em impulso\n"
    )
    print(f"{'H':>4}  {'n':>5}  {'retomou':>8}  {'aleat.':>8}  {'impulso':>8}  {'vs imp.':>8}")
    for h in horizons:
        n, rate = _rate(fails, h)
        _, base = _rate(randoms, h)
        _, mom = _rate(moment, h)
        print(f"{h:>4}  {n:>5}  {rate:>7.1f}%  {base:>7.1f}%  {mom:>7.1f}%  {rate - mom:>+6.1f}pp")

    lives = [e.life for e in fails]
    if lives:
        print(
            f"\nvida do CHoCH até o ✕ (candles): mediana {statistics.median(lives):.0f}, "
            f"p25 {sorted(lives)[len(lives) // 4]}, p75 {sorted(lives)[3 * len(lives) // 4]}"
        )

    # Dose-response: does the killing close's excess over the trigger separate
    # the premature failures from the correct ones? This is what a wider band
    # or a failure-side persistence would have to exploit.
    horizon = horizons[len(horizons) // 2]
    graded = sorted(
        (e for e in fails if e.excess_atr == e.excess_atr and horizon in e.resumed),
        key=lambda e: e.excess_atr,
    )
    if len(graded) >= 8:
        print(f"\nexcesso do fechamento que mata (ATR) x retomou@{horizon}, em quartis:")
        size = len(graded) // 4
        for q in range(4):
            chunk = graded[q * size : (q + 1) * size] if q < 3 else graded[3 * size :]
            n, rate = _rate(chunk, horizon)
            lo, hi = chunk[0].excess_atr, chunk[-1].excess_atr
            print(f"  Q{q + 1} [{lo:+.2f} .. {hi:+.2f}]  n={n:<4} retomou {rate:.1f}%")

    print("\npor timeframe:")
    for tf in sorted({e.timeframe for e in fails}):
        arm = [e for e in fails if e.timeframe == tf]
        ctl = [e for e in moment if e.timeframe == tf]
        n, rate = _rate(arm, horizon)
        _, base = _rate(ctl, horizon)
        print(
            f"  {tf:>4}  n={n:<4} retomou@{horizon} {rate:5.1f}%  "
            f"impulso {base:5.1f}%  {rate - base:+.1f}pp"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS)
    parser.add_argument("--timeframes", nargs="+", default=TIMEFRAMES)
    parser.add_argument("--horizons", nargs="+", type=int, default=list(HORIZONS))
    parser.add_argument("--limit", type=int, default=1200)
    parser.add_argument(
        "--buffer-atr",
        type=float,
        default=0.5,
        help="the production choch_fail_level_buffer_atr, to reconstruct the trigger",
    )
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    evs: list[Ev] = []
    for symbol in args.symbols:
        for name in args.timeframes:
            try:
                evs.extend(
                    run_combo(
                        symbol,
                        TimeFrame(name),
                        limit=args.limit,
                        horizons=args.horizons,
                        buffer_atr=args.buffer_atr,
                        rng=rng,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - a dead feed must not kill the sweep
                print(f"skip {symbol} {name}: {exc}")
    report(evs, args.horizons)


if __name__ == "__main__":
    main()
