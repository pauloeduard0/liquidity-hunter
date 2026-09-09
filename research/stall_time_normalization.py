"""Etapa 4.2: "50 barras" quer dizer a mesma coisa em M15 e em D1?

`detect_structural_stall` usa o mesmo eixo temporal em todos os timeframes:

    bars_since_advance >= 50  AND  retracement_atr >= 6

O eixo do give-back ja e normalizado -- 6 ATR sao 6 ATR em qualquer TF. O eixo
temporal nao e normalizado por nada: sao 50 velas, que valem 12 horas no M15 e
quase dois meses no D1. A validacao original (`structural_stall_validation.py`)
mediu M15/H1/H4 e concluiu que "o mesmo N funciona em todo timeframe medido" --
o D1 nao estava na amostra, e a auditoria do ZEC
(`research/zec_d1_structure_lag.py`) mostrou medianas de 60 a 108 DIAS ate o
gatilho no diario.

A hipotese testada aqui e que o eixo certo nao e nem velas nem relogio, e sim o
**ritmo estrutural do proprio mercado**: uma perna esta velha quando excedeu
varias vezes o intervalo com que aquele par/TF costuma produzir advances.

    structural_age_ratio = bars_since_advance / typical_advance_interval

Tres politicas, medidas lado a lado com o MESMO segundo eixo (K=6 ATR), para
isolar a variavel temporal:

- **A. barras fixas**  -- `bars >= N`, o baseline de producao (N=50), mais uma
  variante com N por timeframe derivada do discovery (secao 13);
- **B. relogio**       -- `elapsed >= D dias`, a alternativa ingenua: se o
  problema fosse so escala de tempo, uma janela unica em dias resolveria;
- **C. ritmo**         -- `structural_age_ratio >= R`.

Populacao
=========

**Somente pernas abertas por BOS.** A Etapa 4.1
(`research/choch_leg_opener.py`) reprovou abrir o guard para CHoCH -- os stalls
que aquilo introduzia eram desmentidos ~3x mais que os atuais -- e misturar as
duas populacoes aqui trocaria duas variaveis de uma vez. O caso do ZEC aberto
por CHoCH entra so como diagnostico, nunca na calibragem.

Ritmo estrutural, causalmente
=============================

`typical_advance_interval` no candle do opener e a mediana dos ultimos `k`
intervalos entre advances ANTERIORES a ele. Duas leituras (todos os advances, e
so BOS) x k em (3, 5, 8, 10). Sem historico suficiente o valor e `None` e a
perna fica `unavailable` naquela variante -- nao ha fallback silencioso, porque
um fallback transformaria "nao sei o ritmo" em "o ritmo e o default" e isso
apareceria como cobertura que a regra nao tem.

Metrica
=======

A de sempre, e pela mesma razao: o alvo nao e prever direcao, e "esta perna
deixou de estar ativa?". Condena `quick_resume` -- um BOS da propria direcao
logo depois do gatilho significa que a perna estava viva. Controle direcional
por construcao: a comparacao e sempre contra o baseline N=50/K=6 na MESMA
populacao de pernas.

Holdout: R e K sao escolhidos so no discovery (70% mais antigo), congelados, e
aplicados no holdout (30% recente) sem recalibrar. E o painel pequeno nunca
decide sozinho -- a Etapa 4.1 mostrou que 10 casos diziam o oposto de 142.

Uso
===

    poetry run python -m research.stall_time_normalization
    poetry run python -m research.stall_time_normalization --expanded --json \
        research/stall_time_normalization_baseline.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import (
    MarketDirection,
    MarketStructure,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.liquidity.structural_stall import (
    DEFAULT_STALL_BARS,
    DEFAULT_STALL_RETRACEMENT_ATR,
    frozen_atr_pct,
)
from research.choch_leg_opener import ADVANCES, expanded_symbols
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "AAVEUSDT", "ZECUSDT"]
TIMEFRAMES = [TimeFrame.M15, TimeFrame.H1, TimeFrame.H4, TimeFrame.D1]

N = DEFAULT_STALL_BARS
K = DEFAULT_STALL_RETRACEMENT_ATR

#: Quantos intervalos estruturais entram na mediana do ritmo.
K_GRID = (3, 5, 8, 10)
#: Grade de R. Valores interpretaveis, procurando plato -- nao otimo.
R_GRID = (1.5, 2.0, 2.5, 3.0, 4.0, 5.0)
#: Grade da politica de relogio, em dias.
DAYS_GRID = (0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0)
#: Grade de N por timeframe (secao 13), avaliada no discovery.
N_GRID = (10, 20, 30, 40, 50, 60, 80, 100, 150)

QUICK = (5, 10, 20, 40, 80)
WINDOWS = 3
DISCOVERY_FRACTION = 0.70

#: Horas por vela, para a politica de relogio.
HOURS = {
    TimeFrame.M15: 0.25,
    TimeFrame.H1: 1.0,
    TimeFrame.H4: 4.0,
    TimeFrame.D1: 24.0,
}

#: O caso diagnostico da Etapa 4.1. Fica FORA de qualquer calibragem.
ZEC_CHOCH_CASE = ("ZECUSDT", TimeFrame.D1, "2026-06-04T00:00:00+00:00")


# --------------------------------------------------------------------------
# 3. ritmo estrutural
# --------------------------------------------------------------------------


def typical_intervals(
    advance_indices: Sequence[int], upto: int, k: int
) -> float | None:
    """Mediana dos ultimos `k` intervalos entre advances ANTERIORES a `upto`.

    Causal por construcao: so entram advances com indice <= `upto`, e o
    intervalo mais recente e o que termina em `upto`. Devolve `None` quando nao
    ha `k` intervalos -- a perna fica `unavailable` nessa variante, e nao
    recebe um default disfarcado de medida.
    """
    prior = [index for index in advance_indices if index <= upto]
    if len(prior) < k + 1:
        return None
    gaps = [b - a for a, b in zip(prior[-(k + 1) :], prior[-k:], strict=False)]
    gaps = [gap for gap in gaps if gap > 0]
    if len(gaps) < k:
        return None
    return statistics.median(gaps)


# --------------------------------------------------------------------------
# pernas com caminho causal
# --------------------------------------------------------------------------


@dataclass
class Leg:
    """Uma perna aberta por BOS, seu ritmo estrutural, e o caminho vela a vela.

    `path` guarda, por vela da perna, a tripla que qualquer politica precisa:
    `(bars_since_advance, retracement_atr, elapsed_hours)`. Calcular o caminho
    uma vez e avaliar dezenas de politicas sobre ele e o que torna a grade
    barata -- e garante que todas leem exatamente os mesmos numeros.
    """

    symbol: str
    timeframe: str
    window: int
    opener_timestamp: str
    direction: str
    leg_bars: int
    #: `typical_advance_interval` por variante ("advances_5", "bos_8", ...).
    #: `None` = sem historico suficiente (unavailable).
    rhythm: dict[str, float | None]
    #: (bars, retracement_atr, elapsed_hours) por vela da perna.
    path: list[tuple[int, float, float]]
    #: (offset desde o inicio da perna, mesma direcao?) de cada BOS posterior.
    later_bos: list[tuple[int, bool]]
    #: Offset do proximo advance (o que encerra a perna), se houver.
    next_advance_offset: int | None


def legs_of_run(run, symbol: str, timeframe: TimeFrame, window: int) -> list[Leg]:
    candles = run.candles
    by_ts = {candle.timestamp: index for index, candle in enumerate(candles)}
    advances: list[tuple[MarketStructure, int]] = []
    for event in run.events:
        if event.provisional or event.event not in ADVANCES:
            continue
        index = by_ts.get(event.timestamp)
        if index is not None:
            advances.append((event, index))
    advances.sort(key=lambda pair: pair[1])
    advance_idx = [index for _, index in advances]
    bos_idx = [
        index
        for event, index in advances
        if event.event is StructureEvent.BREAK_OF_STRUCTURE
    ]
    hours = HOURS[timeframe]

    out: list[Leg] = []
    for position, (opener, index) in enumerate(advances):
        if opener.event is not StructureEvent.BREAK_OF_STRUCTURE:
            continue
        following = advances[position + 1] if position + 1 < len(advances) else None
        # A perna vai ate a vela ANTERIOR ao proximo advance: naquela vela o
        # `_last_advance` ja e o evento novo (mesma convencao da Etapa 4.1).
        end = following[1] - 1 if following else len(candles) - 1
        if end <= index:
            continue

        atr = frozen_atr_pct(candles, index)
        entry = opener.price_level
        if atr <= 0 or entry <= 0:
            continue
        bullish = opener.direction is MarketDirection.BULLISH
        extreme = candles[index].close
        path: list[tuple[int, float, float]] = []
        for i in range(index, end + 1):
            close = candles[i].close
            extreme = max(extreme, close) if bullish else min(extreme, close)
            give_back = (extreme - close) if bullish else (close - extreme)
            path.append((i - index, give_back / entry / atr, (i - index) * hours))

        later = [
            (idx - index, (event.direction is MarketDirection.BULLISH) == bullish)
            for event, idx in advances
            if idx > index and event.event is StructureEvent.BREAK_OF_STRUCTURE
        ]
        rhythm: dict[str, float | None] = {}
        for k in K_GRID:
            rhythm[f"advances_{k}"] = typical_intervals(advance_idx, index, k)
            rhythm[f"bos_{k}"] = typical_intervals(bos_idx, index, k)

        out.append(
            Leg(
                symbol=symbol,
                timeframe=timeframe.value,
                window=window,
                opener_timestamp=opener.timestamp.isoformat(),
                direction=opener.direction.value,
                leg_bars=end - index,
                rhythm=rhythm,
                path=path,
                later_bos=later,
                next_advance_offset=(following[1] - index if following else None),
            )
        )
    return out


# --------------------------------------------------------------------------
# politicas
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Policy:
    """Um eixo temporal + o eixo de give-back. `kind` diz qual eixo."""

    kind: str  # bars | clock | ratio
    label: str
    k_atr: float = K
    n: int | None = None
    days: float | None = None
    r: float | None = None
    rhythm_key: str | None = None
    #: Para `bars` com N por timeframe.
    n_by_timeframe: tuple[tuple[str, int], ...] = ()


BASELINE = Policy(kind="bars", label="N=50/K=6 (producao)", n=N)


@dataclass
class Trigger:
    symbol: str
    timeframe: str
    opener_timestamp: str
    direction: str
    bars_since_advance: int
    retracement_atr: float
    elapsed_hours: float
    structural_age_ratio: float | None
    outcome: str
    bars_to_outcome: int | None
    #: Velas entre o gatilho e o proximo advance -- a antecedencia util.
    lead_bars: int | None


def apply_policy(leg: Leg, policy: Policy) -> Trigger | None | str:
    """O primeiro candle da perna que satisfaz as duas condicoes.

    Devolve `None` quando a perna nunca dispara e a string `"unavailable"`
    quando a politica nao pode nem ser avaliada (ritmo desconhecido) -- os dois
    casos contam diferente na cobertura.
    """
    typical = None
    if policy.kind == "ratio":
        typical = leg.rhythm.get(policy.rhythm_key or "")
        if typical is None or typical <= 0:
            return "unavailable"
    threshold_n = policy.n
    if policy.n_by_timeframe:
        threshold_n = dict(policy.n_by_timeframe).get(leg.timeframe)
        if threshold_n is None:
            return "unavailable"

    for bars, retracement, elapsed in leg.path:
        if retracement < policy.k_atr:
            continue
        if policy.kind == "bars":
            assert threshold_n is not None
            ok = bars >= threshold_n
        elif policy.kind == "clock":
            assert policy.days is not None
            ok = elapsed >= policy.days * 24.0
        else:
            assert policy.r is not None and typical
            ok = bars / typical >= policy.r
        if not ok:
            continue
        outcome, bars_to = "open", None
        for offset, same in leg.later_bos:
            if offset <= bars:
                continue
            outcome, bars_to = ("resumed" if same else "reversed"), offset - bars
            break
        return Trigger(
            symbol=leg.symbol,
            timeframe=leg.timeframe,
            opener_timestamp=leg.opener_timestamp,
            direction=leg.direction,
            bars_since_advance=bars,
            retracement_atr=round(retracement, 2),
            elapsed_hours=elapsed,
            structural_age_ratio=(round(bars / typical, 2) if typical else None),
            outcome=outcome,
            bars_to_outcome=bars_to,
            lead_bars=(
                leg.next_advance_offset - bars
                if leg.next_advance_offset is not None
                else None
            ),
        )
    return None


def evaluate(legs: Sequence[Leg], policy: Policy, candles_scanned: int) -> dict:
    """As metricas da secao 7, sobre uma populacao de pernas."""
    triggers: list[Trigger] = []
    unavailable = 0
    for leg in legs:
        result = apply_policy(leg, policy)
        if result == "unavailable":
            unavailable += 1
        elif result is not None:
            triggers.append(result)
    total_legs = len(legs)
    evaluable = total_legs - unavailable
    out: dict = {
        "policy": policy.label,
        "legs": total_legs,
        "unavailable": unavailable,
        "stalls": len(triggers),
        "coverage": (len(triggers) / evaluable) if evaluable else None,
        "stalls_per_1200_candles": (
            len(triggers) / candles_scanned * 1200 if candles_scanned else None
        ),
    }
    if not triggers:
        return out
    counts = Counter(t.outcome for t in triggers)
    n = len(triggers)
    out.update(
        {
            "resumed": counts["resumed"] / n,
            "reversed": counts["reversed"] / n,
            "open": counts["open"] / n,
            "median_bars_since_advance": statistics.median(
                t.bars_since_advance for t in triggers
            ),
            "median_ratio": (
                statistics.median(
                    t.structural_age_ratio
                    for t in triggers
                    if t.structural_age_ratio is not None
                )
                if any(t.structural_age_ratio is not None for t in triggers)
                else None
            ),
            "median_lead_bars": (
                statistics.median(
                    t.lead_bars for t in triggers if t.lead_bars is not None
                )
                if any(t.lead_bars is not None for t in triggers)
                else None
            ),
        }
    )
    for horizon in QUICK:
        out[f"quick_resume_{horizon}"] = (
            sum(
                1
                for t in triggers
                if t.outcome == "resumed"
                and t.bars_to_outcome is not None
                and t.bars_to_outcome <= horizon
            )
            / n
        )
    return out


# --------------------------------------------------------------------------
# coleta
# --------------------------------------------------------------------------


def collect(
    symbols: Sequence[str],
    timeframes: Sequence[TimeFrame],
    windows: int = WINDOWS,
    limit: int = LIMIT,
) -> tuple[list[Leg], dict[str, int]]:
    legs: list[Leg] = []
    scanned: dict[str, int] = defaultdict(int)
    for symbol in symbols:
        for timeframe in timeframes:
            if not (CACHE_DIR / f"{symbol}_{timeframe.value}.json").exists():
                continue
            try:
                series = load_series(symbol, timeframe)
            except Exception as error:  # noqa: BLE001 - cache com vela corrompida
                print(f"! cache invalido: {symbol} {timeframe.value}: {error}")
                continue
            for window in range(windows):
                end = len(series) - window * limit
                start = end - limit - BUFFER
                if start < 0:
                    break
                try:
                    run = dd._run_internal_structure(
                        provider=SliceProvider(series[start:end]),
                        symbol=symbol,
                        timeframe=timeframe,
                        limit=limit,
                        confluence_filter=True,
                    )
                except Exception as error:  # noqa: BLE001 - feed morto
                    print(f"! janela invalida: {symbol} {timeframe.value}: {error}")
                    continue
                legs.extend(legs_of_run(run, symbol, timeframe, window))
                scanned[timeframe.value] += len(run.candles)
    return legs, dict(scanned)


def split_holdout(legs: Sequence[Leg]) -> tuple[list[Leg], list[Leg]]:
    """70% mais antigo / 30% mais recente, por tempo do opener, DENTRO de cada TF.

    O corte tem de ser por timeframe. As janelas de D1 cobrem anos e as de M15
    cobrem semanas, entao um corte global por timestamp joga todo o diario no
    discovery e deixa o holdout so com intraday -- foi o que aconteceu na
    primeira rodada (holdout com 0 pernas de D1 e 0 de H4), e um holdout que
    nao contem o timeframe em questao nao testa nada sobre ele.
    """
    discovery: list[Leg] = []
    holdout: list[Leg] = []
    by_tf: dict[str, list[Leg]] = defaultdict(list)
    for leg in legs:
        by_tf[leg.timeframe].append(leg)
    for group in by_tf.values():
        ordered = sorted(group, key=lambda leg: leg.opener_timestamp)
        cut = int(len(ordered) * DISCOVERY_FRACTION)
        discovery.extend(ordered[:cut])
        holdout.extend(ordered[cut:])
    return discovery, holdout


# --------------------------------------------------------------------------
# 2. o ritmo por timeframe (a pergunta 1 e 2 da entrega)
# --------------------------------------------------------------------------


def rhythm_profile(legs: Sequence[Leg]) -> dict:
    """Distribuicao do intervalo entre advances, por timeframe.

    E o numero que decide se N=50 e comparavel entre TFs: se o intervalo tipico
    for parecido em velas, 50 velas quer dizer a mesma coisa; se nao for, nao
    quer.
    """
    out: dict[str, dict] = {}
    by_tf: dict[str, list[Leg]] = defaultdict(list)
    for leg in legs:
        by_tf[leg.timeframe].append(leg)
    for timeframe, group in by_tf.items():
        values = [
            leg.rhythm["advances_5"]
            for leg in group
            if leg.rhythm["advances_5"] is not None
        ]
        lengths = [leg.leg_bars for leg in group]
        if not values:
            continue
        values.sort()
        out[timeframe] = {
            "legs": len(group),
            "median_advance_interval_bars": statistics.median(values),
            "p10": values[int(len(values) * 0.10)],
            "p90": values[int(len(values) * 0.90)],
            "median_leg_bars": statistics.median(lengths),
            "n50_in_typical_intervals": N / statistics.median(values),
            "median_advance_interval_hours": statistics.median(values)
            * HOURS[TimeFrame(timeframe)],
        }
    return out


# --------------------------------------------------------------------------
# 9. ZEC D1
# --------------------------------------------------------------------------


def zec_diagnostics(legs: Sequence[Leg]) -> dict:
    """(A) a perna de CHoCH so como diagnostico; (B) as pernas de BOS do ZEC D1."""
    from research.choch_leg_opener import BOS_OR_CHOCH
    from research.choch_leg_opener import legs_of_run as opener_legs

    symbol, timeframe, opener_ts = ZEC_CHOCH_CASE
    series = load_series(symbol, timeframe)
    run = dd._run_internal_structure(
        provider=SliceProvider(series[-(LIMIT + BUFFER) :]),
        symbol=symbol,
        timeframe=timeframe,
        limit=LIMIT,
        confluence_filter=True,
    )
    by_ts = {candle.timestamp: index for index, candle in enumerate(run.candles)}
    advance_idx = sorted(
        by_ts[e.timestamp]
        for e in run.events
        if not e.provisional and e.event in ADVANCES and e.timestamp in by_ts
    )
    choch_leg = next(
        leg
        for leg in opener_legs(run, symbol, timeframe, 0, BOS_OR_CHOCH)
        if leg.opener_timestamp == opener_ts
    )
    opener_index = by_ts[
        next(
            e.timestamp
            for e in run.events
            if e.timestamp.isoformat() == opener_ts and e.event in ADVANCES
        )
    ]
    diagnostic = {
        "note": (
            "diagnostico apenas -- perna aberta por CHoCH, fora da populacao e "
            "de qualquer calibragem (Etapa 4.1 reprovou abrir o guard)"
        ),
        "opener": opener_ts,
        "stale_since": choch_leg.stale_since,
        "bars_since_advance": choch_leg.bars_since_advance,
        "retracement_atr": choch_leg.retracement_atr,
    }
    for k in K_GRID:
        typical = typical_intervals(advance_idx, opener_index, k)
        diagnostic[f"typical_advances_{k}"] = typical
        diagnostic[f"ratio_advances_{k}"] = (
            round(choch_leg.bars_since_advance / typical, 2)
            if typical and choch_leg.bars_since_advance
            else None
        )

    zec_bos = [
        leg for leg in legs if leg.symbol == "ZECUSDT" and leg.timeframe == "1d"
    ]
    return {"choch_leg_diagnostic": diagnostic, "bos_legs": len(zec_bos)}


# --------------------------------------------------------------------------
# relatorio
# --------------------------------------------------------------------------


def policies(rhythm_key: str = "advances_5") -> list[Policy]:
    out = [BASELINE]
    out += [
        Policy(kind="clock", label=f"relogio {days}d/K=6", days=days)
        for days in DAYS_GRID
    ]
    out += [
        Policy(
            kind="ratio",
            label=f"ratio R={r}/K=6 ({rhythm_key})",
            r=r,
            rhythm_key=rhythm_key,
        )
        for r in R_GRID
    ]
    return out


def per_timeframe(
    legs: Sequence[Leg], policy: Policy, scanned: dict[str, int]
) -> dict:
    by_tf: dict[str, list[Leg]] = defaultdict(list)
    for leg in legs:
        by_tf[leg.timeframe].append(leg)
    return {
        timeframe: evaluate(group, policy, scanned.get(timeframe, 0))
        for timeframe, group in sorted(by_tf.items())
    }


def best_n_by_timeframe(discovery: Sequence[Leg], scanned: dict[str, int]) -> dict:
    """Secao 13: N por timeframe derivado do discovery, nao escolhido a mao.

    Criterio: o MENOR N cujo `quick_resume_40` fica <= o do baseline naquele
    TF. Menor N = mais cobertura e mais antecedencia; o teto de qr40 e o que
    impede comprar cobertura degradando a qualidade. Se nenhum N passar, o TF
    fica com o baseline.
    """
    by_tf: dict[str, list[Leg]] = defaultdict(list)
    for leg in discovery:
        by_tf[leg.timeframe].append(leg)
    chosen: dict[str, int] = {}
    detail: dict[str, list] = {}
    for timeframe, group in by_tf.items():
        base = evaluate(group, BASELINE, scanned.get(timeframe, 0))
        ceiling = base.get("quick_resume_40")
        rows = []
        for candidate in N_GRID:
            row = evaluate(
                group,
                Policy(kind="bars", label=f"N={candidate}", n=candidate),
                scanned.get(timeframe, 0),
            )
            rows.append(
                {
                    "n": candidate,
                    "stalls": row["stalls"],
                    "quick_resume_40": row.get("quick_resume_40"),
                }
            )
        detail[timeframe] = rows
        passing = [
            row["n"]
            for row in rows
            if row["stalls"] >= 10
            and ceiling is not None
            and row.get("quick_resume_40") is not None
            and row["quick_resume_40"] <= ceiling
        ]
        chosen[timeframe] = min(passing) if passing else N
    return {"chosen": chosen, "grid": detail}


@dataclass
class Report:
    symbols: int = 0
    legs: int = 0
    rhythm: dict = field(default_factory=dict)
    policies_overall: list[dict] = field(default_factory=list)
    policies_by_timeframe: dict = field(default_factory=dict)
    n_by_timeframe: dict = field(default_factory=dict)
    holdout: dict = field(default_factory=dict)
    zec: dict = field(default_factory=dict)
    rhythm_key_sweep: dict = field(default_factory=dict)


def build(
    symbols: Sequence[str],
    timeframes: Sequence[TimeFrame] = TIMEFRAMES,
    windows: int = WINDOWS,
) -> Report:
    legs, scanned = collect(symbols, timeframes, windows)
    report = Report(symbols=len(symbols), legs=len(legs))
    report.rhythm = rhythm_profile(legs)

    total_scanned = sum(scanned.values())
    for policy in policies():
        report.policies_overall.append(evaluate(legs, policy, total_scanned))
        report.policies_by_timeframe[policy.label] = per_timeframe(
            legs, policy, scanned
        )

    # Qual variante de ritmo (todos os advances vs so BOS, k=3..10) e mais
    # estavel? Varrida so no discovery, para nao escolher olhando o holdout.
    discovery, holdout = split_holdout(legs)
    discovery_scanned = {
        tf: int(count * DISCOVERY_FRACTION) for tf, count in scanned.items()
    }
    for key in (f"advances_{k}" for k in K_GRID):
        for r in (2.0, 3.0):
            policy = Policy(
                kind="ratio", label=f"{key} R={r}", r=r, rhythm_key=key
            )
            report.rhythm_key_sweep[policy.label] = evaluate(
                discovery, policy, sum(discovery_scanned.values())
            )
    for key in (f"bos_{k}" for k in K_GRID):
        policy = Policy(kind="ratio", label=f"{key} R=3.0", r=3.0, rhythm_key=key)
        report.rhythm_key_sweep[policy.label] = evaluate(
            discovery, policy, sum(discovery_scanned.values())
        )

    report.n_by_timeframe = best_n_by_timeframe(discovery, discovery_scanned)
    chosen = tuple(sorted(report.n_by_timeframe["chosen"].items()))
    per_tf_policy = Policy(
        kind="bars", label="N por timeframe (discovery)", n_by_timeframe=chosen
    )

    # Holdout: as politicas escolhidas no discovery, aplicadas sem recalibrar.
    holdout_scanned = {
        tf: count - discovery_scanned.get(tf, 0) for tf, count in scanned.items()
    }
    frozen = [
        BASELINE,
        per_tf_policy,
        Policy(kind="ratio", label="ratio R=2.0", r=2.0, rhythm_key="advances_5"),
        Policy(kind="ratio", label="ratio R=3.0", r=3.0, rhythm_key="advances_5"),
        Policy(kind="clock", label="relogio 10d", days=10.0),
    ]
    report.holdout = {
        policy.label: {
            "discovery": evaluate(
                discovery, policy, sum(discovery_scanned.values())
            ),
            "holdout": evaluate(holdout, policy, sum(holdout_scanned.values())),
            "holdout_by_timeframe": per_timeframe(holdout, policy, holdout_scanned),
        }
        for policy in frozen
    }
    report.zec = zec_diagnostics(legs)
    return report


def _pct(value) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def _num(value, digits: int = 1) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _row(label: str, row: dict) -> str:
    return (
        f"  {label:<28} n={row['stalls']:>5} cob={_pct(row.get('coverage')):>5} "
        f"unav={row['unavailable']:>5} "
        f"qr5={_pct(row.get('quick_resume_5')):>4} qr10={_pct(row.get('quick_resume_10')):>4} "
        f"qr20={_pct(row.get('quick_resume_20')):>4} qr40={_pct(row.get('quick_resume_40')):>4} "
        f"qr80={_pct(row.get('quick_resume_80')):>4} "
        f"res={_pct(row.get('resumed')):>4} rev={_pct(row.get('reversed')):>4} "
        f"lead={_num(row.get('median_lead_bars'), 0):>5} "
        f"bars={_num(row.get('median_bars_since_advance'), 0):>5}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--expanded", action="store_true")
    parser.add_argument("--windows", type=int, default=WINDOWS)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    symbols = args.symbols or (expanded_symbols() if args.expanded else SYMBOLS)
    report = build(symbols, TIMEFRAMES, args.windows)
    print(f"simbolos={report.symbols} pernas(BOS)={report.legs}  N={N} K={K}\n")

    print("== 1/2. RITMO ESTRUTURAL POR TIMEFRAME ==")
    print(
        f"  {'tf':>4} {'pernas':>7} {'interv.mediano':>15} {'p10':>6} {'p90':>6} "
        f"{'em horas':>10} {'perna med':>10} {'N=50 vale':>10}"
    )
    for timeframe, row in report.rhythm.items():
        print(
            f"  {timeframe:>4} {row['legs']:>7} {row['median_advance_interval_bars']:>15.0f} "
            f"{row['p10']:>6.0f} {row['p90']:>6.0f} "
            f"{row['median_advance_interval_hours']:>10.1f} "
            f"{row['median_leg_bars']:>10.0f} "
            f"{row['n50_in_typical_intervals']:>9.1f}x"
        )

    print("\n== 6/7. POLITICAS (todos os TFs juntos) ==")
    for row in report.policies_overall:
        print(_row(row["policy"], row))

    print("\n== 8. POR TIMEFRAME: baseline vs ratio ==")
    for label in (
        BASELINE.label,
        "ratio R=2.0/K=6 (advances_5)",
        "ratio R=3.0/K=6 (advances_5)",
        "relogio 10.0d/K=6",
    ):
        rows = report.policies_by_timeframe.get(label)
        if not rows:
            continue
        print(f"  -- {label}")
        for timeframe, row in rows.items():
            print(_row(f"     {timeframe}", row))

    print("\n== 13. N POR TIMEFRAME (derivado do discovery) ==")
    print(f"  escolhidos: {report.n_by_timeframe['chosen']}")

    print("\n== 11. HOLDOUT (escolhido no discovery, congelado) ==")
    for label, rows in report.holdout.items():
        print(f"  -- {label}")
        print(_row("     discovery", rows["discovery"]))
        print(_row("     holdout", rows["holdout"]))
        for timeframe, row in rows["holdout_by_timeframe"].items():
            print(_row(f"       {timeframe}", row))

    print("\n== 9. ZEC D1 ==")
    print(f"  pernas BOS no ZEC D1: {report.zec['bos_legs']}")
    print("  diagnostico (perna de CHoCH, fora da populacao):")
    for key, value in report.zec["choch_leg_diagnostic"].items():
        print(f"    {key}: {value}")

    if args.json:
        args.json.write_text(json.dumps(asdict(report), indent=1))
        print(f"\n-> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
