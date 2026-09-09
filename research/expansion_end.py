"""Etapa 2.6: da para saber, NA HORA, que uma corrida expansiva acabou?

A Etapa 2.5 falhou por um motivo especifico e nao por parametro ruim: o gate
aprovava tambem os BOS do MEIO de uma corrida, e aquelas pernas retomam 100%
das vezes. Research so nao os via porque rotulava "expansao" olhando o futuro
(o ULTIMO BOS da corrida). Este modulo pergunta a coisa que falta:

    no candle do BOS -- ou algumas velas depois, sem olhar adiante --
    da para distinguir um BOS INTERMEDIARIO de um BOS TERMINAL?

Dataset
=======

Uma observacao por BOS nao-provisional que pertence a uma corrida de mesma
direcao (`research/expansion_stall._runs`, a definicao da Etapa 0.5). O rotulo
usa futuro -- e so rotulo, nunca feature:

- `CONTINUES` -- ha outro BOS da mesma direcao depois, na MESMA corrida;
- `TERMINAL`  -- este foi o ultimo BOS daquela corrida.

Features
========

Todas lidas em `candles[:bos_index + 1]` e em eventos ate o BOS. `frozen_atr`
e o mesmo `frozen_atr_pct` da producao (media expandida do true range%), a
unidade de tudo que e distancia. A "perna" de um BOS e o trecho do BOS anterior
da corrida (ou do inicio dela) ate este BOS.

Espera
======

Um BOS terminal nao precisa ser reconhecido no proprio candle. `no_new_bos_for
= M` confirma o fim M velas depois, se nenhum advance apareceu -- causal por
construcao, e o proprio ato de esperar ja e um classificador. Esta e a
hipotese central da etapa.

Holdout
=======

70% cronologicos mais antigos para escolher, 30% mais recentes para reportar
congelado. A Etapa 0.6 nao tinha isso.

    poetry run python -m research.expansion_end
    poetry run python -m research.expansion_end --json research/expansion_end_baseline.json
"""

from __future__ import annotations

import argparse
import bisect
import json
import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.liquidity.structural_stall import frozen_atr_pct
from research.expansion_stall import _runs
from research.range_choch import LIMIT
from research.structural_stall_validation import (
    QUICK,
    SYMBOLS,
    TIMEFRAMES,
    RunData,
    summarize,
    triggers_of_leg,
)
from research.structural_stall_validation import collect as svv_collect

#: Velas de espera testadas para `no_new_bos_for`.
WAITS = (5, 10, 15, 20, 30, 40)
#: Horizontes em que um "terminal" errado ainda produz BOS (o pior erro).
REFIRE = (5, 10, 20, 40)
#: Fatia cronologica de descoberta.
DISCOVERY_SHARE = 0.7
#: Marcacoes minimas no discovery para uma regra ser eleita. Sem esse piso a
#: escolha por precisao cai sempre numa regra de 30 casos que nao replica --
#: foi o que aconteceu na primeira passada desta etapa.
MIN_SUPPORT = 100

ADVANCES = (
    StructureEvent.BREAK_OF_STRUCTURE,
    StructureEvent.CHANGE_OF_CHARACTER,
    StructureEvent.CHOCH_FAILED,
)


@dataclass
class Observation:
    """Um BOS de corrida, o que se sabia dele na hora, e o que veio depois."""

    symbol: str
    timeframe: str
    timestamp: str
    index: int
    direction: str
    label: str                       # CONTINUES | TERMINAL
    # --- A: a corrida ate aqui
    run_position: int
    run_displacement_atr: float
    run_bars: int
    leg_displacement_atr: float
    leg_bars: int
    displacement_ratio: float        # perna atual / perna anterior
    bars_ratio: float
    # --- B: eficiencia da perna
    efficiency: float                # deslocamento liquido / range total
    atr_per_bar: float
    mae_atr: float
    mfe_atr: float
    # --- C: extensao alem do nivel rompido
    extension_close_atr: float
    extension_wick_atr: float
    close_position: float            # onde o close ficou dentro da vela (0..1)
    close_to_extreme_atr: float
    # --- D: o pullback anterior
    pullback_depth_atr: float
    pullback_bars: int
    pullback_over_impulse: float
    pullback_deeper: int
    pullback_longer: int
    # --- E: volatilidade
    atr_now: float
    atr_ratio: float                 # ATR agora / ATR no 1o BOS da corrida
    # --- F: o que o stream ja dizia
    events_since_previous_bos: int
    provisional_since: int
    failed_since: int
    sweeps_since: int
    swings_since: int
    reference_structural: int        # 1 | 0 | -1 (desconhecido)
    #: Velas existentes depois do BOS na janela. Nao e futuro: e quanto do
    #: futuro JA passou -- sem isso a espera seria "confirmada" antes de as M
    #: velas existirem, que e o unico jeito de a regra olhar adiante.
    bars_available: int
    # --- futuro (rotulo/avaliacao apenas)
    bars_to_next_bos: int | None
    bars_to_next_advance: int | None


def _index_map(candles: Sequence[Candle]) -> dict[Any, int]:
    return {candle.timestamp: index for index, candle in enumerate(candles)}


def _leg_stats(
    candles: Sequence[Candle], start: int, end: int, bullish: bool, entry: float, atr: float
) -> tuple[float, float, float, float]:
    """(eficiencia, MAE, MFE, atr por vela) do trecho `start..end`."""
    window = candles[start : end + 1]
    if not window or atr <= 0 or entry <= 0:
        return 0.0, 0.0, 0.0, 0.0
    high = max(c.high for c in window)
    low = min(c.low for c in window)
    net = (window[-1].close - window[0].close) if bullish else (window[0].close - window[-1].close)
    span = high - low
    efficiency = net / span if span > 0 else 0.0
    origin = window[0].close
    mae = ((origin - low) if bullish else (high - origin)) / entry / atr
    mfe = ((high - origin) if bullish else (origin - low)) / entry / atr
    bars = max(len(window) - 1, 1)
    return efficiency, mae, mfe, abs(net) / entry / atr / bars


def observations_of_run(
    events: Sequence[MarketStructure], candles: Sequence[Candle]
) -> list[Observation]:
    """Uma observacao por BOS de corrida, com features estritamente causais."""
    index_by_ts = _index_map(candles)
    out: list[Observation] = []
    advance_indices = sorted(
        index
        for event in events
        if not event.provisional
        and event.event in ADVANCES
        and (index := index_by_ts.get(event.timestamp)) is not None
    )
    for run in _runs(events):
        # Corridas de 1 BOS entram: sao TERMINAL por definicao, e sao a maioria
        # do universo -- deixa-las de fora inflaria a precisao de qualquer regra.
        first = run[0]
        first_index = index_by_ts.get(first.timestamp)
        if first_index is None:
            continue
        bullish = first.direction is MarketDirection.BULLISH
        atr_first = frozen_atr_pct(candles, first_index)
        for position, event in enumerate(run):
            index = index_by_ts.get(event.timestamp)
            if index is None:
                continue
            atr = frozen_atr_pct(candles, index)
            entry = event.price_level
            if atr <= 0 or entry <= 0:
                continue
            previous = run[position - 1] if position else None
            previous_index = (
                index_by_ts.get(previous.timestamp) if previous is not None else None
            )
            leg_start = previous_index if previous_index is not None else first_index
            leg_bars = max(index - leg_start, 1)
            leg_displacement = (
                abs(entry - previous.price_level) / entry / atr if previous is not None else 0.0
            )
            run_displacement = abs(entry - first.price_level) / first.price_level / atr

            # Perna anterior (para as razoes) -- so olha para tras.
            if position >= 2:
                before = run[position - 2]
                before_index = index_by_ts.get(before.timestamp)
                prev_bars = max((previous_index or 0) - (before_index or 0), 1)
                prev_disp = (
                    abs((previous.price_level if previous else entry) - before.price_level)
                    / entry
                    / atr
                )
            else:
                prev_bars, prev_disp = 0, 0.0
            displacement_ratio = leg_displacement / prev_disp if prev_disp > 0 else -1.0
            bars_ratio = leg_bars / prev_bars if prev_bars > 0 else -1.0

            efficiency, mae, mfe, atr_per_bar = _leg_stats(
                candles, leg_start, index, bullish, entry, atr
            )

            candle = candles[index]
            reference = event.reference_price_level or entry
            extension_close = (
                (candle.close - reference) if bullish else (reference - candle.close)
            ) / entry / atr
            extension_wick = (
                (candle.high - reference) if bullish else (reference - candle.low)
            ) / entry / atr
            span = candle.high - candle.low
            close_position = (
                ((candle.close - candle.low) if bullish else (candle.high - candle.close)) / span
                if span > 0
                else 0.5
            )
            leg_window = candles[leg_start : index + 1]
            leg_extreme = (
                max(c.high for c in leg_window) if bullish else min(c.low for c in leg_window)
            )
            close_to_extreme = abs(leg_extreme - candle.close) / entry / atr

            # Pullback: o trecho entre o extremo da perna anterior e a retomada.
            pullback_depth, pullback_bars = 0.0, 0
            if previous_index is not None:
                segment = candles[previous_index : index + 1]
                if segment:
                    pivot = (
                        min(range(len(segment)), key=lambda i: segment[i].low)
                        if bullish
                        else max(range(len(segment)), key=lambda i: segment[i].high)
                    )
                    anchor = segment[0].close
                    depth = (
                        (anchor - segment[pivot].low)
                        if bullish
                        else (segment[pivot].high - anchor)
                    )
                    pullback_depth = max(depth, 0.0) / entry / atr
                    pullback_bars = pivot
            pullback_over_impulse = (
                pullback_depth / leg_displacement if leg_displacement > 0 else -1.0
            )

            since = [
                e
                for e in events
                if previous is not None
                and previous.timestamp < e.timestamp <= event.timestamp
                and e is not event
            ]
            label_next = next(
                (
                    other
                    for other in run[position + 1 :]
                ),
                None,
            )
            next_index = (
                index_by_ts.get(label_next.timestamp) if label_next is not None else None
            )
            next_advance = next((i for i in advance_indices if i > index), None)

            out.append(
                Observation(
                    symbol=event.symbol,
                    timeframe=event.timeframe.value,
                    timestamp=str(event.timestamp),
                    index=index,
                    direction=event.direction.value,
                    label="CONTINUES" if label_next is not None else "TERMINAL",
                    run_position=position + 1,
                    run_displacement_atr=run_displacement,
                    run_bars=index - first_index,
                    leg_displacement_atr=leg_displacement,
                    leg_bars=leg_bars,
                    displacement_ratio=displacement_ratio,
                    bars_ratio=bars_ratio,
                    efficiency=efficiency,
                    atr_per_bar=atr_per_bar,
                    mae_atr=mae,
                    mfe_atr=mfe,
                    extension_close_atr=extension_close,
                    extension_wick_atr=extension_wick,
                    close_position=close_position,
                    close_to_extreme_atr=close_to_extreme,
                    pullback_depth_atr=pullback_depth,
                    pullback_bars=pullback_bars,
                    pullback_over_impulse=pullback_over_impulse,
                    pullback_deeper=-1,
                    pullback_longer=-1,
                    atr_now=atr,
                    atr_ratio=atr / atr_first if atr_first > 0 else -1.0,
                    events_since_previous_bos=len(since),
                    provisional_since=sum(1 for e in since if e.provisional),
                    failed_since=sum(
                        1 for e in since if e.event is StructureEvent.CHOCH_FAILED
                    ),
                    sweeps_since=sum(
                        1 for e in since if e.event is StructureEvent.LIQUIDITY_SWEEP
                    ),
                    swings_since=sum(
                        1
                        for e in since
                        if e.event
                        in (
                            StructureEvent.HIGHER_HIGH,
                            StructureEvent.HIGHER_LOW,
                            StructureEvent.LOWER_HIGH,
                            StructureEvent.LOWER_LOW,
                        )
                    ),
                    reference_structural=(
                        -1
                        if event.reference_structural is None
                        else int(event.reference_structural)
                    ),
                    bars_available=len(candles) - 1 - index,
                    bars_to_next_bos=(next_index - index) if next_index is not None else None,
                    bars_to_next_advance=(
                        (next_advance - index) if next_advance is not None else None
                    ),
                )
            )
    # `pullback_deeper` / `pullback_longer` comparam com a observacao anterior da
    # mesma corrida -- resolvidos numa segunda passada, ainda so para tras.
    for position, observation in enumerate(out):
        if observation.run_position <= 1:
            continue
        previous = out[position - 1]
        if previous.timestamp >= observation.timestamp:
            continue
        observation.pullback_deeper = int(
            observation.pullback_depth_atr > previous.pullback_depth_atr
        )
        observation.pullback_longer = int(observation.pullback_bars > previous.pullback_bars)
    return out


def collect(
    symbols: Sequence[str], timeframes: Sequence[TimeFrame], windows: int, limit: int
) -> list[tuple[RunData, list[Observation]]]:
    """Janelas de producao + as observacoes de cada uma.

    Reusa `structural_stall_validation.collect` -- mesmo cache, mesmo pipeline,
    mesmas pernas -- para que a comparacao com o STALE seja sobre exatamente as
    mesmas janelas, e nao sobre um segundo carregamento.
    """
    runs = svv_collect(symbols, timeframes, windows, limit)
    return [(run, observations_of_run(run.events, run.candles)) for run in runs]


# --- analise univariada ----------------------------------------------------

FEATURES = (
    "run_position",
    "run_displacement_atr",
    "run_bars",
    "leg_displacement_atr",
    "leg_bars",
    "displacement_ratio",
    "bars_ratio",
    "efficiency",
    "atr_per_bar",
    "mae_atr",
    "mfe_atr",
    "extension_close_atr",
    "extension_wick_atr",
    "close_position",
    "close_to_extreme_atr",
    "pullback_depth_atr",
    "pullback_bars",
    "pullback_over_impulse",
    "pullback_deeper",
    "pullback_longer",
    "atr_ratio",
    "events_since_previous_bos",
    "provisional_since",
    "failed_since",
    "sweeps_since",
    "swings_since",
    "reference_structural",
)


def _quantile(values: Sequence[float], share: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = min(int(share * (len(ordered) - 1)), len(ordered) - 1)
    return ordered[position]


def _auc(terminal: Sequence[float], continues: Sequence[float]) -> float:
    """AUC por contagem de pares -- 0.5 = as distribuicoes nao se separam.

    Reportada como |AUC - 0.5| + 0.5 nao: fica o valor cru, para que o SENTIDO
    da separacao (maior ou menor no terminal) continue legivel.
    """
    if not terminal or not continues:
        return 0.5
    ordered = sorted(continues)
    total = 0.0
    for value in terminal:
        lower = bisect.bisect_left(ordered, value)
        equal = bisect.bisect_right(ordered, value) - lower
        total += lower + equal / 2
    return total / (len(terminal) * len(ordered))


def univariate(observations: Sequence[Observation]) -> list[dict[str, Any]]:
    terminal = [o for o in observations if o.label == "TERMINAL"]
    continues = [o for o in observations if o.label == "CONTINUES"]
    rows: list[dict[str, Any]] = []
    for name in FEATURES:
        t = [float(getattr(o, name)) for o in terminal]
        c = [float(getattr(o, name)) for o in continues]
        median_t = statistics.median(t) if t else 0.0
        median_c = statistics.median(c) if c else 0.0
        rows.append(
            {
                "feature": name,
                "n_terminal": len(t),
                "n_continues": len(c),
                "mediana_terminal": median_t,
                "mediana_continues": median_c,
                "p25_terminal": _quantile(t, 0.25),
                "p75_terminal": _quantile(t, 0.75),
                "p25_continues": _quantile(c, 0.25),
                "p75_continues": _quantile(c, 0.75),
                "diferenca_relativa": (
                    (median_t - median_c) / abs(median_c) if median_c else 0.0
                ),
                "auc": _auc(t, c),
            }
        )
    rows.sort(key=lambda row: -abs(row["auc"] - 0.5))
    return rows


# --- regras ----------------------------------------------------------------


@dataclass
class Rule:
    """Uma regra causal pequena: esperar M velas, mais ate 3 condicoes."""

    wait: int
    conditions: tuple[tuple[str, str, float], ...] = ()

    @property
    def name(self) -> str:
        parts = [f"espera>={self.wait}"]
        parts += [f"{f}{op}{value:g}" for f, op, value in self.conditions]
        return " & ".join(parts)

    def marks(self, observation: Observation) -> bool:
        """A regra marcaria este BOS como fim de expansao?

        Causal por construcao: a espera so pode ser avaliada `wait` velas depois
        do BOS, e nesse ponto `bars_to_next_advance` ja e conhecido para esse
        trecho -- nenhuma vela alem da confirmacao e lida.
        """
        if observation.bars_available < self.wait:
            return False   # as velas da confirmacao ainda nao existem
        survived = (
            observation.bars_to_next_advance is None
            or observation.bars_to_next_advance > self.wait
        )
        if not survived:
            return False
        for feature, operator, value in self.conditions:
            current = float(getattr(observation, feature))
            if operator == "<=" and not current <= value:
                return False
            if operator == ">=" and not current >= value:
                return False
        return True


def score(rule: Rule, observations: Sequence[Observation]) -> dict[str, Any]:
    marked = [o for o in observations if rule.marks(o)]
    terminal = [o for o in observations if o.label == "TERMINAL"]
    hits = [o for o in marked if o.label == "TERMINAL"]
    wrong = [o for o in marked if o.label == "CONTINUES"]
    out: dict[str, Any] = {
        "regra": rule.name,
        "marcados": len(marked),
        "precision": len(hits) / len(marked) if marked else 0.0,
        "recall": len(hits) / len(terminal) if terminal else 0.0,
        "falso_terminal": len(wrong) / len(marked) if marked else 0.0,
        "continues_mortos": len(wrong),
    }
    for horizon in REFIRE:
        out[f"refire_{horizon}"] = (
            sum(
                1
                for o in wrong
                if o.bars_to_next_bos is not None
                and o.bars_to_next_bos - rule.wait <= horizon
            )
            / len(marked)
            if marked
            else 0.0
        )
    return out


# --- valor para o STALE ----------------------------------------------------

N_STALL, K_STALL = 50, 6.0


def stall_populations(
    dataset: Sequence[tuple[RunData, list[Observation]]], rule: Rule
) -> dict[str, dict[str, Any]]:
    """As tres populacoes de STALE da pergunta 7, sobre as MESMAS janelas.

    A -- STALE generico (Etapa 1/2);
    B -- gate da Etapa 2.5 (`leg.run_bos >= 3 and displacement >= 15 ATR`, na
         forma causal: a perna abriu numa corrida que ja qualificava);
    C -- candidato a fim de expansao pela `rule`.
    """
    groups: dict[str, list[Any]] = {"A": [], "B": [], "C": []}
    legs: dict[str, int] = {"A": 0, "B": 0, "C": 0}
    for run, observations in dataset:
        by_timestamp = {o.timestamp: o for o in observations}
        for leg in run.legs:
            observation = by_timestamp.get(leg.start_timestamp)
            gate_25 = observation is not None and (
                observation.run_position >= 3 and observation.run_displacement_atr >= 15.0
            )
            gate_26 = observation is not None and gate_25 and rule.marks(observation)
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
            for key, eligible in (("A", True), ("B", gate_25), ("C", gate_26)):
                if not eligible:
                    continue
                legs[key] += 1
                if trigger is not None:
                    groups[key].append(trigger)
    return {key: summarize(groups[key], legs[key]) for key in groups}


# --- lead time -------------------------------------------------------------


def lead_times(
    dataset: Sequence[tuple[RunData, list[Observation]]], rule: Rule
) -> dict[str, Any]:
    """Velas entre BOS -> EXPANSION_END -> STALE -> proximo advance."""
    to_stall: list[int] = []
    to_advance: list[int] = []
    for run, observations in dataset:
        by_timestamp = {o.timestamp: o for o in observations}
        for leg in run.legs:
            observation = by_timestamp.get(leg.start_timestamp)
            if observation is None or not rule.marks(observation):
                continue
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
            if trigger is not None:
                to_stall.append(trigger.bars_since_advance - rule.wait)
            if observation.bars_to_next_advance is not None:
                to_advance.append(observation.bars_to_next_advance - rule.wait)
    return {
        "bos_ate_expansion_end": rule.wait,
        "expansion_end_ate_stale_med": (
            statistics.median(to_stall) if to_stall else None
        ),
        "expansion_end_antes_do_stale": sum(1 for v in to_stall if v > 0),
        "expansion_end_depois_do_stale": sum(1 for v in to_stall if v <= 0),
        "expansion_end_ate_proximo_advance_med": (
            statistics.median(to_advance) if to_advance else None
        ),
    }


# --- relatorio -------------------------------------------------------------

BTC_RUN_LAST_BOS = "2026-08-25 02:00:00+00:00"


def candidate_rules(observations: Sequence[Observation]) -> list[Rule]:
    """A familia de regras testada -- pequena e interpretavel, por desenho."""
    rules = [Rule(wait=wait) for wait in WAITS]
    for wait in (10, 20, 30):
        for feature, operator, values in (
            ("efficiency", "<=", (0.3, 0.5, 0.7)),
            ("displacement_ratio", "<=", (0.5, 1.0, 1.5)),
            ("extension_close_atr", "<=", (0.5, 1.0, 2.0)),
            ("close_to_extreme_atr", ">=", (0.5, 1.0, 2.0)),
            ("bars_ratio", ">=", (1.0, 1.5, 2.0)),
            ("atr_ratio", ">=", (1.0, 1.1)),
        ):
            rules += [Rule(wait=wait, conditions=((feature, operator, v),)) for v in values]
    return rules


def _split(observations: Sequence[Observation]) -> tuple[list[Observation], list[Observation]]:
    """70% cronologicos mais antigos / 30% mais recentes, por timestamp."""
    ordered = sorted(observations, key=lambda o: o.timestamp)
    cut = int(len(ordered) * DISCOVERY_SHARE)
    return ordered[:cut], ordered[cut:]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS)
    parser.add_argument("--timeframes", nargs="+", default=[t.value for t in TIMEFRAMES])
    parser.add_argument("--windows", type=int, default=6)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    dataset = collect(
        args.symbols, [TimeFrame(t) for t in args.timeframes], args.windows, LIMIT
    )
    observations = [o for _, group in dataset for o in group]
    terminal = sum(1 for o in observations if o.label == "TERMINAL")
    print(
        f"\n== {len(observations)} BOS de corrida: "
        f"{terminal} TERMINAL / {len(observations) - terminal} CONTINUES =="
    )
    multi = [o for o in observations if o.run_position >= 2]
    print(
        f"   (dentro de corrida, posicao >= 2: {len(multi)}, "
        f"{sum(1 for o in multi if o.label == 'TERMINAL')} terminais)"
    )

    print("\n== univariada, ordenada por separacao (AUC 0,5 = nenhuma) ==")
    header = (
        f"{'feature':<26} {'med_T':>9} {'med_C':>9} {'p25_T':>8} {'p75_T':>8} "
        f"{'dif_rel':>8} {'AUC':>6}"
    )
    print(header)
    print("-" * len(header))
    rows = univariate(observations)
    for row in rows:
        print(
            f"{row['feature']:<26} {row['mediana_terminal']:>9.2f} "
            f"{row['mediana_continues']:>9.2f} {row['p25_terminal']:>8.2f} "
            f"{row['p75_terminal']:>8.2f} {row['diferenca_relativa']:>8.2f} "
            f"{row['auc']:>6.3f}"
        )

    discovery, holdout = _split(observations)
    print(
        f"\n== regras: descoberta {len(discovery)} obs "
        f"(ate {discovery[-1].timestamp[:10] if discovery else '-'}) / "
        f"holdout {len(holdout)} obs =="
    )
    rules = candidate_rules(discovery)
    scored = sorted(
        (score(rule, discovery) | {"_rule": rule} for rule in rules),
        key=lambda row: (-row["precision"], row["refire_20"]),
    )
    eligible = [row for row in scored if row["marcados"] >= MIN_SUPPORT]
    header = (
        f"{'regra':<40} {'marc':>5} {'prec':>6} {'recall':>7} {'falso':>6} "
        f"{'rf5':>5} {'rf10':>5} {'rf20':>5} {'rf40':>5}"
    )
    print(header)
    print("-" * len(header))
    for row in scored[:15]:
        print(
            f"{row['regra']:<40} {row['marcados']:>5} {row['precision'] * 100:>5.0f}% "
            f"{row['recall'] * 100:>6.0f}% {row['falso_terminal'] * 100:>5.0f}% "
            + " ".join(f"{row[f'refire_{h}'] * 100:>4.0f}%" for h in REFIRE)
        )

    print("\n== so a espera, por tamanho (a hipotese central) ==")
    print(header)
    print("-" * len(header))
    wait_rows = [score(Rule(wait=wait), discovery) for wait in WAITS]
    for row in wait_rows:
        print(
            f"{row['regra']:<40} {row['marcados']:>5} {row['precision'] * 100:>5.0f}% "
            f"{row['recall'] * 100:>6.0f}% {row['falso_terminal'] * 100:>5.0f}% "
            + " ".join(f"{row[f'refire_{h}'] * 100:>4.0f}%" for h in REFIRE)
        )

    best = (eligible or scored)[0]["_rule"] if scored else Rule(wait=20)
    print(
        f"\n== congelada no discovery (suporte >= {MIN_SUPPORT}): {best.name} =="
    )
    for label, sample in (("discovery", discovery), ("HOLDOUT", holdout)):
        row = score(best, sample)
        print(
            f"{label:<10} marcados={row['marcados']:<5} prec={row['precision'] * 100:.0f}% "
            f"recall={row['recall'] * 100:.0f}% falso={row['falso_terminal'] * 100:.0f}% "
            + " ".join(f"rf{h}={row[f'refire_{h}'] * 100:.0f}%" for h in REFIRE)
        )

    print("\n== por timeframe (regra congelada, amostra inteira) ==")
    for value in args.timeframes:
        subset = [o for o in observations if o.timeframe == value]
        row = score(best, subset)
        print(
            f"  {value:<4} n={len(subset):<5} marcados={row['marcados']:<5} "
            f"prec={row['precision'] * 100:.0f}% recall={row['recall'] * 100:.0f}% "
            f"rf20={row['refire_20'] * 100:.0f}%"
        )

    print(f"\n== STALE N={N_STALL} K={K_STALL}: as tres populacoes ==")
    header = (
        f"{'':<28} {'pernas':>7} {'stalls':>7} {'resumed':>8} {'reversed':>9} "
        f"{'open':>6} {'qr5':>5} {'qr10':>6} {'qr20':>6}"
    )
    print(header)
    print("-" * len(header))
    populations = stall_populations(dataset, best)
    labels = {
        "A": "A generico",
        "B": "B gate 2.5",
        "C": f"C {best.name}",
    }
    for key, stats in populations.items():
        if not stats.get("triggers"):
            print(f"{labels[key]:<28} {stats['legs']:>7} {0:>7}")
            continue
        print(
            f"{labels[key][:28]:<28} {stats['legs']:>7} {stats['triggers']:>7} "
            f"{stats['resumed'] * 100:>7.0f}% {stats['reversed'] * 100:>8.0f}% "
            f"{stats['open'] * 100:>5.0f}% "
            + " ".join(f"{stats[f'quick_resume_{h}'] * 100:>4.0f}%" for h in QUICK)
        )

    print("\n== lead time (regra congelada) ==")
    for key, value in lead_times(dataset, best).items():
        print(f"  {key:<40} {value}")

    print(f"\n== BTC H1: a corrida que terminou em {BTC_RUN_LAST_BOS} ==")
    btc = [
        o
        for o in observations
        if o.symbol == "BTCUSDT" and o.timeframe == "1h" and o.timestamp <= BTC_RUN_LAST_BOS
    ]
    run_of_case = [o for o in btc if o.timestamp >= "2026-08-2"][-6:]
    header = (
        f"{'timestamp':<26} {'pos':>4} {'acum_ATR':>9} {'perna_ATR':>10} {'velas':>6} "
        f"{'efic':>6} {'ext_close':>10} {'rotulo':<10} {'marcada':>8}"
    )
    print(header)
    print("-" * len(header))
    for o in run_of_case:
        print(
            f"{o.timestamp:<26} {o.run_position:>4} {o.run_displacement_atr:>9.1f} "
            f"{o.leg_displacement_atr:>10.1f} {o.leg_bars:>6} {o.efficiency:>6.2f} "
            f"{o.extension_close_atr:>10.2f} {o.label:<10} "
            f"{('SIM' if best.marks(o) else '-'):>8}"
        )

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "regra": best.name,
                    "univariada": rows,
                    "regras": [
                        {k: v for k, v in row.items() if k != "_rule"} for row in scored
                    ],
                    "espera": wait_rows,
                    "holdout": score(best, holdout),
                    "stall": populations,
                    "observacoes": [asdict(o) for o in observations],
                },
                indent=2,
            )
        )
        print(f"\n-> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
