"""Etapa 2.7: quando o STALE dispara, o fluxo que sustentava a perna sumiu?

A Etapa 2.6 fechou negativamente a pergunta estrutural: dentro do stream
BOS/CHoCH nao ha o que distinga um BOS terminal de um intermediario. Esta
etapa muda a fonte de informacao, nao a busca -- pergunta se a PARTICIPACAO
(volume, agressao do taker, CVD, esforco x resultado, numero de negocios)
separa os STALEs que sao pausa dos que sao perda real de impulso.

Nao se preve direcao. O alvo principal nem e `resumed` vs `reversed`, e sim
`NO_RESUME_M`: a perna volta a imprimir BOS na mesma direcao dentro de M velas?

Inventario da fonte (secao 0 do pedido)
=======================================

Tudo vem do cache offline que as etapas anteriores ja usam
(`research/.klines_cache`, linhas cruas de klines da Binance, 12 colunas):

- `volume`            -- coluna 5, ja no `Candle`;
- `taker_buy_volume`  -- coluna 9, ja no `Candle`; base de `volume_delta`;
- `trades`            -- coluna 8, numero de negocios. Esta no cache e NAO no
  `Candle`; lido aqui direto da linha crua, sem download novo;
- `volume_delta` / `cumulative_volume_delta` -- `indicators/volume_delta.py`,
  funcoes puras sobre a sequencia de candles, sem estado e sem reset. O CVD
  desta etapa e ancorado no advance que abre a perna (soma dos deltas dali em
  diante), logo **nao ha reset diario** e a serie e reproduzivel em replay;
- open interest / funding: **nao ha historico local**. Os provedores de
  futuros do projeto sao de rede e nao ha cache. Ficam de fora, como pedido.

Uma ressalva de qualidade que vem do proprio loader: linhas com
`taker_buy_volume > volume` sao corrompidas e `klines_row_to_candle` marca o
split como desconhecido pondo metade do volume de cada lado -- delta zero. O
estudo conta essas velas e reporta a fracao afetada.

Populacao
=========

Um STALE causal (N=50, K=6, close, ATR frozen), observado NA VELA
`stale_since`. Todas as features leem `candles[:stale_index + 1]`.

Regioes comparadas, porque volume absoluto nao se compara entre ativos:

- **A** impulso: do advance anterior ate o advance que abriu a perna;
- **B** pos-advance: do advance ate o `stale_since`;
- **C** final: as ultimas 5/10/20 velas antes do `stale_since`.

    poetry run python -m research.stall_participation
    poetry run python -m research.stall_participation \
        --json research/stall_participation_baseline.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from liquidity_hunter.core.domain import Candle, MarketDirection, TimeFrame
from liquidity_hunter.indicators.volume_delta import volume_delta
from research.range_choch import CACHE_DIR, LIMIT
from research.structural_stall_validation import (
    SYMBOLS,
    TIMEFRAMES,
    RunData,
    Trigger,
    triggers_of_leg,
)
from research.structural_stall_validation import collect as svv_collect

N_STALL, K_STALL = 50, 6.0
#: Horizontes de "voltou a imprimir BOS na mesma direcao".
RESUME_HORIZONS = (5, 10, 20, 40, 80)
DISCOVERY_SHARE = 0.7
#: Marcacoes minimas no discovery para uma regra ser eleita (licao da 2.6).
MIN_SUPPORT = 20


def trade_counts(symbol: str, timeframe: TimeFrame) -> dict[int, float]:
    """`{open_time_ms: trades}` da linha crua -- a coluna que o `Candle` nao tem."""
    path = CACHE_DIR / f"{symbol}_{timeframe.value}.json"
    if not path.exists():
        return {}
    rows: list[list[Any]] = json.loads(path.read_text())
    return {int(row[0]): float(row[8]) for row in rows}


def _mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _ratio(numerator: float, denominator: float) -> float:
    """Razao com sentinela -1 quando o denominador nao existe."""
    return numerator / denominator if denominator > 0 else -1.0


@dataclass
class StallObservation:
    """Um STALE, o fluxo que se conhecia nele, e o que veio depois."""

    symbol: str
    timeframe: str
    stale_since: str
    leg_start: str
    direction: str
    kind: str
    # --- volume (secao 3)
    volume_b_over_a: float
    volume_last5_over_a: float
    volume_last10_over_a: float
    volume_last20_over_a: float
    volume_at_advance_over_a: float
    volume_at_stale_over_b: float
    volume_above_mean_share: float
    volume_slope_b: float
    # --- agressao (secao 4)
    delta_ratio_a: float
    delta_ratio_b: float
    delta_ratio_last10: float
    delta_ratio_last20: float
    delta_against_share_b: float
    # --- CVD (secao 5)
    cvd_change_b: float
    cvd_slope_last20: float
    cvd_new_extreme_without_price: int
    price_new_extreme_without_cvd: int
    # --- esforco x resultado / VSA (secao 6)
    effort_result_b_over_a: float
    range_last10_over_a: float
    volume_per_range_b_over_a: float
    close_position_last10: float
    narrow_range_share_last10: float
    # --- participacao bruta
    trades_b_over_a: float
    trade_size_b_over_a: float
    # --- qualidade do dado
    corrupt_share: float
    # --- alvos (nunca features)
    outcome: str
    bars_to_outcome: int | None
    resumed_within: dict[str, int]


def _slice_stats(
    candles: Sequence[Candle], trades: dict[int, float]
) -> tuple[float, float, float, float, float]:
    """(volume medio, |delta| ratio medio, range medio, trades medio, corrompidas)."""
    if not candles:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    volume = _mean([c.volume for c in candles])
    ranges = _mean([(c.high - c.low) / c.close for c in candles])
    count = _mean([trades.get(int(c.timestamp.timestamp() * 1000), 0.0) for c in candles])
    corrupt = sum(
        1 for c in candles if c.volume > 0 and abs(c.taker_buy_volume * 2 - c.volume) < 1e-9
    ) / len(candles)
    return volume, 0.0, ranges, count, corrupt


def observe(
    trigger: Trigger,
    run: RunData,
    leg_start_index: int,
    previous_advance_index: int,
    trades: dict[int, float],
) -> StallObservation | None:
    """Features de fluxo do STALE, lidas so ate `stale_since`."""
    candles = run.candles
    stale_index = next(
        (i for i, c in enumerate(candles) if str(c.timestamp) == trigger.timestamp), None
    )
    if stale_index is None or stale_index <= leg_start_index:
        return None
    bullish = trigger.direction == MarketDirection.BULLISH.value
    sign = 1.0 if bullish else -1.0

    impulse = candles[previous_advance_index : leg_start_index + 1]
    post = candles[leg_start_index : stale_index + 1]
    if len(impulse) < 3 or len(post) < 3:
        return None
    last5, last10, last20 = post[-5:], post[-10:], post[-20:]

    vol_a, _, range_a, trades_a, corrupt_a = _slice_stats(impulse, trades)
    vol_b, _, range_b, trades_b, corrupt_b = _slice_stats(post, trades)
    if vol_a <= 0 or vol_b <= 0:
        return None

    def delta_ratio(window: Sequence[Candle]) -> float:
        total_volume = sum(c.volume for c in window)
        if total_volume <= 0:
            return 0.0
        return sign * sum(volume_delta(c) for c in window) / total_volume

    # CVD ancorado no advance da perna: sem reset, causal, reproduzivel.
    cvd: list[float] = []
    running = 0.0
    for candle in post:
        running += volume_delta(candle)
        cvd.append(running)
    cvd_extreme = max(cvd) if bullish else min(cvd)
    price_extreme = (
        max(c.close for c in post) if bullish else min(c.close for c in post)
    )
    half = len(post) // 2
    late = post[half:]
    cvd_late_extreme = (
        max(cvd[half:]) if bullish else min(cvd[half:])
    )
    price_late_extreme = (
        max(c.close for c in late) if bullish else min(c.close for c in late)
    )
    cvd_new = int(abs(cvd_late_extreme - cvd_extreme) < 1e-9)
    price_new = int(abs(price_late_extreme - price_extreme) < 1e-9)

    displacement_b = abs(post[-1].close - post[0].close) / post[0].close
    displacement_a = abs(impulse[-1].close - impulse[0].close) / impulse[0].close
    effort_a = _ratio(vol_a, displacement_a)
    effort_b = _ratio(vol_b, displacement_b)

    against = sum(1 for c in post if sign * volume_delta(c) < 0) / len(post)
    total_volume_b = sum(c.volume for c in post)

    resumed_within = {
        f"no_resume_{horizon}": int(
            not (
                trigger.outcome == "resumed"
                and trigger.bars_to_outcome is not None
                and trigger.bars_to_outcome <= horizon
            )
        )
        for horizon in RESUME_HORIZONS
    }

    return StallObservation(
        symbol=trigger.symbol,
        timeframe=trigger.timeframe,
        stale_since=trigger.timestamp,
        leg_start=trigger.leg_start,
        direction=trigger.direction,
        kind=trigger.kind,
        volume_b_over_a=_ratio(vol_b, vol_a),
        volume_last5_over_a=_ratio(_mean([c.volume for c in last5]), vol_a),
        volume_last10_over_a=_ratio(_mean([c.volume for c in last10]), vol_a),
        volume_last20_over_a=_ratio(_mean([c.volume for c in last20]), vol_a),
        volume_at_advance_over_a=_ratio(candles[leg_start_index].volume, vol_a),
        volume_at_stale_over_b=_ratio(candles[stale_index].volume, vol_b),
        volume_above_mean_share=sum(1 for c in post if c.volume > vol_b) / len(post),
        volume_slope_b=_ratio(
            _mean([c.volume for c in late]), _mean([c.volume for c in post[:half]])
        ),
        delta_ratio_a=delta_ratio(impulse),
        delta_ratio_b=delta_ratio(post),
        delta_ratio_last10=delta_ratio(last10),
        delta_ratio_last20=delta_ratio(last20),
        delta_against_share_b=against,
        cvd_change_b=(sign * (cvd[-1] - cvd[0]) / total_volume_b) if total_volume_b else 0.0,
        cvd_slope_last20=(
            sign * (cvd[-1] - cvd[-min(20, len(cvd))]) / total_volume_b
            if total_volume_b
            else 0.0
        ),
        cvd_new_extreme_without_price=int(cvd_new and not price_new),
        price_new_extreme_without_cvd=int(price_new and not cvd_new),
        effort_result_b_over_a=_ratio(effort_b, effort_a) if effort_a > 0 else -1.0,
        range_last10_over_a=_ratio(_mean([(c.high - c.low) / c.close for c in last10]), range_a),
        volume_per_range_b_over_a=_ratio(_ratio(vol_b, range_b), _ratio(vol_a, range_a)),
        close_position_last10=_mean(
            [
                ((c.close - c.low) if bullish else (c.high - c.close)) / (c.high - c.low)
                for c in last10
                if c.high > c.low
            ]
        ),
        narrow_range_share_last10=sum(
            1 for c in last10 if (c.high - c.low) / c.close < range_a
        )
        / max(len(last10), 1),
        trades_b_over_a=_ratio(trades_b, trades_a),
        trade_size_b_over_a=_ratio(_ratio(vol_b, trades_b), _ratio(vol_a, trades_a)),
        corrupt_share=(corrupt_a + corrupt_b) / 2,
        outcome=trigger.outcome,
        bars_to_outcome=trigger.bars_to_outcome,
        resumed_within=resumed_within,
    )


def collect(
    symbols: Sequence[str], timeframes: Sequence[TimeFrame], windows: int, limit: int
) -> list[StallObservation]:
    """STALEs das MESMAS janelas das etapas anteriores, com fluxo anexado."""
    runs = svv_collect(symbols, timeframes, windows, limit)
    cache: dict[tuple[str, str], dict[int, float]] = {}
    out: list[StallObservation] = []
    for run in runs:
        if not run.candles:
            continue
        symbol = run.candles[0].symbol
        timeframe = run.candles[0].timeframe
        key = (symbol, timeframe.value)
        if key not in cache:
            cache[key] = trade_counts(symbol, timeframe)
        starts = sorted(leg.start_index for leg in run.legs)
        for leg in run.legs:
            trigger = triggers_of_leg(
                leg,
                run.events,
                run.candles,
                run.event_indices,
                n=N_STALL,
                k=K_STALL,
                price_mode="close",
                atr_mode="frozen",
            )
            if trigger is None:
                continue
            previous = [i for i in starts if i < leg.start_index]
            previous_index = previous[-1] if previous else max(leg.start_index - 20, 0)
            observation = observe(
                trigger, run, leg.start_index, previous_index, cache[key]
            )
            if observation is not None:
                out.append(observation)
    return out


# --- analise ---------------------------------------------------------------

FEATURES = tuple(
    field.name
    for field in fields(StallObservation)
    if field.type == "float" or field.type == "int"
)


def _quantile(values: Sequence[float], share: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(int(share * (len(ordered) - 1)), len(ordered) - 1)]


def _auc(positive: Sequence[float], negative: Sequence[float]) -> float:
    if not positive or not negative:
        return 0.5
    import bisect

    ordered = sorted(negative)
    total = 0.0
    for value in positive:
        lower = bisect.bisect_left(ordered, value)
        equal = bisect.bisect_right(ordered, value) - lower
        total += lower + equal / 2
    return total / (len(positive) * len(ordered))


def univariate(
    observations: Sequence[StallObservation], positive: str, negative: str
) -> list[dict[str, Any]]:
    """`positive`/`negative` sao `outcome` ou uma chave `no_resume_*`."""

    def group(observation: StallObservation, label: str) -> bool:
        if label.startswith("no_resume_"):
            return bool(observation.resumed_within[label])
        if label.startswith("resume_"):
            return not observation.resumed_within[label.replace("resume_", "no_resume_")]
        return observation.outcome == label

    a = [o for o in observations if group(o, positive)]
    b = [o for o in observations if group(o, negative)]
    rows: list[dict[str, Any]] = []
    for name in FEATURES:
        values_a = [float(getattr(o, name)) for o in a]
        values_b = [float(getattr(o, name)) for o in b]
        median_a = statistics.median(values_a) if values_a else 0.0
        median_b = statistics.median(values_b) if values_b else 0.0
        rows.append(
            {
                "feature": name,
                "n_pos": len(values_a),
                "n_neg": len(values_b),
                f"mediana_{positive}": median_a,
                f"mediana_{negative}": median_b,
                "p25_pos": _quantile(values_a, 0.25),
                "p75_pos": _quantile(values_a, 0.75),
                "p25_neg": _quantile(values_b, 0.25),
                "p75_neg": _quantile(values_b, 0.75),
                "auc": _auc(values_a, values_b),
            }
        )
    rows.sort(key=lambda row: -abs(row["auc"] - 0.5))
    return rows


@dataclass
class Rule:
    conditions: tuple[tuple[str, str, float], ...]

    @property
    def name(self) -> str:
        return " & ".join(f"{f}{op}{v:g}" for f, op, v in self.conditions)

    def marks(self, observation: StallObservation) -> bool:
        for feature, operator, value in self.conditions:
            current = float(getattr(observation, feature))
            if operator == "<=" and not current <= value:
                return False
            if operator == ">=" and not current >= value:
                return False
        return True


def score(rule: Rule, observations: Sequence[StallObservation], horizon: int) -> dict[str, Any]:
    key = f"no_resume_{horizon}"
    marked = [o for o in observations if rule.marks(o)]
    positives = [o for o in observations if o.resumed_within[key]]
    hits = [o for o in marked if o.resumed_within[key]]
    row: dict[str, Any] = {
        "regra": rule.name,
        "marcados": len(marked),
        "precision": len(hits) / len(marked) if marked else 0.0,
        "recall": len(hits) / len(positives) if positives else 0.0,
        "base_rate": len(positives) / len(observations) if observations else 0.0,
    }
    for other in RESUME_HORIZONS:
        row[f"resume_{other}"] = (
            sum(1 for o in marked if not o.resumed_within[f"no_resume_{other}"]) / len(marked)
            if marked
            else 0.0
        )
    return row


def candidate_rules(observations: Sequence[StallObservation]) -> list[Rule]:
    """Uma grade pequena e declarada de antemao sobre as features de fluxo.

    Nada de busca exaustiva: um limiar por feature, nos quartis da propria
    amostra de descoberta, mais os pares das duas familias (volume e agressao).
    """
    singles: list[Rule] = []
    for feature, operator in (
        ("volume_b_over_a", "<="),
        ("volume_last10_over_a", "<="),
        ("volume_last20_over_a", "<="),
        ("volume_slope_b", "<="),
        ("delta_ratio_b", "<="),
        ("delta_ratio_last20", "<="),
        ("delta_against_share_b", ">="),
        ("cvd_change_b", "<="),
        ("cvd_slope_last20", "<="),
        ("effort_result_b_over_a", ">="),
        ("volume_per_range_b_over_a", ">="),
        ("trades_b_over_a", "<="),
        ("trade_size_b_over_a", "<="),
        ("narrow_range_share_last10", ">="),
    ):
        values = sorted(float(getattr(o, feature)) for o in observations)
        if not values:
            continue
        for share in (0.25, 0.5, 0.75):
            singles.append(Rule(((feature, operator, _quantile(values, share)),)))
    pairs = [
        Rule((a.conditions[0], b.conditions[0]))
        for a in singles
        if a.conditions[0][0].startswith("volume")
        for b in singles
        if b.conditions[0][0].startswith(("delta", "cvd"))
    ]
    return singles + pairs


# --- relatorio -------------------------------------------------------------

BTC_STALE = "2026-08-28 15:00:00+00:00"
MAIN_HORIZON = 20


def _split(
    observations: Sequence[StallObservation],
) -> tuple[list[StallObservation], list[StallObservation]]:
    ordered = sorted(observations, key=lambda o: o.stale_since)
    cut = int(len(ordered) * DISCOVERY_SHARE)
    return ordered[:cut], ordered[cut:]


def _print_univariate(rows: Sequence[dict[str, Any]], positive: str, negative: str) -> None:
    header = (
        f"{'feature':<30} {'med_' + positive[:8]:>12} {'med_' + negative[:8]:>12} "
        f"{'p25_pos':>9} {'p75_pos':>9} {'AUC':>6}"
    )
    print(header)
    print("-" * len(header))
    for row in rows[:12]:
        print(
            f"{row['feature']:<30} {row[f'mediana_{positive}']:>12.3f} "
            f"{row[f'mediana_{negative}']:>12.3f} {row['p25_pos']:>9.3f} "
            f"{row['p75_pos']:>9.3f} {row['auc']:>6.3f}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS)
    parser.add_argument("--timeframes", nargs="+", default=[t.value for t in TIMEFRAMES])
    parser.add_argument("--windows", type=int, default=6)
    parser.add_argument("--horizon", type=int, default=MAIN_HORIZON)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    observations = collect(
        args.symbols, [TimeFrame(t) for t in args.timeframes], args.windows, LIMIT
    )
    outcomes = {
        name: sum(1 for o in observations if o.outcome == name)
        for name in ("resumed", "reversed", "open")
    }
    corrupt = _mean([o.corrupt_share for o in observations])
    print(f"\n== {len(observations)} STALEs (N={N_STALL}, K={K_STALL}, close, frozen) ==")
    print(f"   desfecho: {outcomes}")
    print(f"   velas com split de taker desconhecido (delta zerado): {corrupt * 100:.2f}%")
    for horizon in RESUME_HORIZONS:
        share = _mean([float(o.resumed_within[f'no_resume_{horizon}']) for o in observations])
        print(f"   NO_RESUME_{horizon:<3} base rate: {share * 100:.0f}%")

    print("\n== RESUMED vs REVERSED (OPEN reportado a parte) ==")
    _print_univariate(univariate(observations, "resumed", "reversed"), "resumed", "reversed")

    key = f"no_resume_{args.horizon}"
    print(f"\n== {key.upper()} vs retomou em <= {args.horizon} velas ==")
    _print_univariate(
        univariate(observations, key, f"resume_{args.horizon}"), key, f"resume_{args.horizon}"
    )

    discovery, holdout = _split(observations)
    print(
        f"\n== regras para {key}: descoberta {len(discovery)} "
        f"(ate {discovery[-1].stale_since[:10] if discovery else '-'}) / "
        f"holdout {len(holdout)} =="
    )
    rules = candidate_rules(discovery)
    scored = sorted(
        (score(rule, discovery, args.horizon) | {"_rule": rule} for rule in rules),
        key=lambda row: -row["precision"],
    )
    eligible = [row for row in scored if row["marcados"] >= MIN_SUPPORT]
    header = (
        f"{'regra':<58} {'marc':>5} {'prec':>6} {'recall':>7} "
        + " ".join(f"{'r' + str(h):>5}" for h in RESUME_HORIZONS)
    )
    print(header)
    print("-" * len(header))
    for row in (eligible or scored)[:10]:
        print(
            f"{row['regra'][:58]:<58} {row['marcados']:>5} {row['precision'] * 100:>5.0f}% "
            f"{row['recall'] * 100:>6.0f}% "
            + " ".join(f"{row[f'resume_{h}'] * 100:>4.0f}%" for h in RESUME_HORIZONS)
        )
    base = _mean([float(o.resumed_within[key]) for o in discovery])
    print(f"base rate no discovery: {base * 100:.0f}%")

    best = (eligible or scored)[0]["_rule"] if scored else Rule(())
    print(f"\n== congelada (suporte >= {MIN_SUPPORT}): {best.name} ==")
    for label, sample in (("discovery", discovery), ("HOLDOUT", holdout)):
        row = score(best, sample, args.horizon)
        print(
            f"{label:<10} marcados={row['marcados']:<4} prec={row['precision'] * 100:.0f}% "
            f"(base {row['base_rate'] * 100:.0f}%) recall={row['recall'] * 100:.0f}% "
            + " ".join(f"r{h}={row[f'resume_{h}'] * 100:.0f}%" for h in RESUME_HORIZONS)
        )

    print("\n== por timeframe (regra congelada, amostra inteira) ==")
    for value in args.timeframes:
        subset = [o for o in observations if o.timeframe == value]
        row = score(best, subset, args.horizon)
        print(
            f"  {value:<4} n={len(subset):<4} marcados={row['marcados']:<4} "
            f"prec={row['precision'] * 100:.0f}% (base {row['base_rate'] * 100:.0f}%) "
            f"r20={row['resume_20'] * 100:.0f}%"
        )

    print(f"\n== BTC H1, o STALE de {BTC_STALE} ==")
    case = next(
        (
            o
            for o in observations
            if o.symbol == "BTCUSDT" and o.timeframe == "1h" and o.stale_since == BTC_STALE
        ),
        None,
    )
    if case is None:
        print("  fora das janelas colhidas")
    else:
        for field in fields(StallObservation):
            if field.name in ("resumed_within",):
                continue
            print(f"  {field.name:<32} {getattr(case, field.name)}")
        print(f"  {'resumed_within':<32} {case.resumed_within}")
        print(f"  {'regra congelada dispara?':<32} {best.marks(case)}")

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "regra": best.name,
                    "univariada_resumed_reversed": univariate(
                        observations, "resumed", "reversed"
                    ),
                    "univariada_no_resume": univariate(
                        observations, key, f"resume_{args.horizon}"
                    ),
                    "regras": [
                        {k: v for k, v in row.items() if k != "_rule"} for row in scored
                    ],
                    "holdout": score(best, holdout, args.horizon),
                    "observacoes": [asdict(o) for o in observations],
                },
                indent=2,
            )
        )
        print(f"\n-> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
