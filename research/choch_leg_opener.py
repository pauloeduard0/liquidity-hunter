"""Etapa 4.1: um `CHoCH` confirmado tambem ABRE uma perna?

`detect_structural_stall` so aceita `BREAK_OF_STRUCTURE` como opener:

    opener, advance_index = advance
    if opener.event is not StructureEvent.BREAK_OF_STRUCTURE:
        return None

O docstring justifica assim: "uma perna que terminou em CHoCH ja foi fechada
pela maquina, entao nao ha o que chamar de velho". Isso e verdade quando o
CHoCH **fecha** a perna anterior -- e e so metade do que ele faz. O ZEC D1
mostrou a outra metade (`research/zec_d1_structure_lag.py`):

    CHoCH bearish 2026-06-04  -> abre a perna bearish
    nenhum advance por 85 dias
    recuperacao de +220% no meio disso
    StructuralStall == None o tempo inteiro

A perna que ficou velha era a perna ABERTA pelo CHoCH, e o guard nao consegue
enxerga-la por construcao.

Hipotese medida aqui
====================

Uma perna estrutural e aberta por `BREAK_OF_STRUCTURE` **ou** por
`CHANGE_OF_CHARACTER` nao-provisional, e **nao** por `CHOCH_FAILED`. A
assimetria e semantica, nao conveniencia: BOS e CHoCH deixam uma estrutura
NOVA em vigor (continuacao e mudanca), enquanto o `✕` invalida uma tentativa --
o que ele deixa em pe e a perna que ja existia antes, e essa perna tem o
proprio opener. (`research/choch_failed_stall_audit.py` ja mediu o `✕` como
opener e concluiu que ele nao custa STALE nenhum; aqui ele fica de fora de
proposito, e a Etapa 4.1 isola UMA variavel.)

O que muda e o que nao muda
===========================

Muda uma coisa so: o conjunto de eventos aceitos como opener. N=50, K=6, ATR
congelado no opener, extremo em CLOSE, normalizacao pelo `price_level` do
opener, "o proximo advance de qualquer tipo encerra a perna", causalidade --
tudo identico, e `detect_stall` abaixo e copia verbatim da producao com o
guard parametrizado, justamente para que a diferenca seja auditavel numa linha.

O invariante obrigatorio (secao 4 do pedido): para toda perna cujo opener JA e
um BOS, as duas variantes tem de devolver o mesmo `stale_since`,
`bars_since_advance`, `retracement_atr`, `frozen_atr_pct` e `leg_extreme_*`.
Isso e verificado caso a caso na medicao (`identical`) e prendido em
`research/test_choch_leg_opener.py`.

Metrica
=======

O alvo nao e prever reversao -- e "esta perna deixou de estar ativa?". Logo a
metrica que condena e `quick_resume`: um STALE desmentido por um BOS da propria
direcao logo em seguida foi um erro de momento, a perna estava viva. Mesmas
definicoes de `research/structural_stall_validation.py`: desfecho pelo primeiro
BOS nao-provisional depois do trigger (`resumed` se for da direcao da perna,
`reversed` se oposto, `open` se a janela acabar antes).

Holdout: 70% mais antigo = discovery, 30% recente = holdout, por tempo. Nao ha
threshold sendo calibrado (N e K estao congelados de proposito), mas o
comportamento das pernas abertas por CHoCH nao pode existir so num trecho.

Uso
===

    poetry run python -m research.choch_leg_opener
    poetry run python -m research.choch_leg_opener --json \
        research/choch_leg_opener_baseline.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructuralStall,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.liquidity.structural_stall import (
    DEFAULT_STALL_BARS,
    DEFAULT_STALL_RETRACEMENT_ATR,
    _last_advance,
    frozen_atr_pct,
)
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series
from research.stall_replay_stability import REFRESHES, RETRO_MARGIN, STEP

#: O painel pedido para a etapa (o de `structural_stall_validation` + ZEC).
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "AAVEUSDT", "ZECUSDT"]


def expanded_symbols() -> list[str]:
    """Todo simbolo do cache com os quatro timeframes.

    O painel de 6 produz uma dezena de stalls novos -- pouco para decidir
    producao, e principalmente pouco para o holdout. Esta lista existe so para
    testar se o `quick_resume` observado no painel sobrevive com mais n; nao
    substitui o painel pedido, e reportada ao lado dele.
    """
    names = sorted(
        path.name.removesuffix("_1d.json") for path in CACHE_DIR.glob("*_1d.json")
    )
    return [
        name
        for name in names
        if all(
            (CACHE_DIR / f"{name}_{tf.value}.json").exists() for tf in TIMEFRAMES
        )
    ]
TIMEFRAMES = [TimeFrame.M15, TimeFrame.H1, TimeFrame.H4, TimeFrame.D1]

N = DEFAULT_STALL_BARS
K = DEFAULT_STALL_RETRACEMENT_ATR

#: O guard de hoje.
BOS_ONLY = frozenset({StructureEvent.BREAK_OF_STRUCTURE})
#: O guard proposto. `CHOCH_FAILED` fica de fora de proposito (secao 8).
BOS_OR_CHOCH = frozenset(
    {StructureEvent.BREAK_OF_STRUCTURE, StructureEvent.CHANGE_OF_CHARACTER}
)

#: O que encerra uma perna: qualquer advance, exatamente como
#: `_advance_boundaries` / `_last_advance`. Inclui `CHOCH_FAILED`, e e por isso
#: que a secao 8 do pedido ja esta satisfeita sem regra nova: um `✕` depois do
#: CHoCH fecha a janela da perna como qualquer outro advance.
ADVANCES = frozenset(
    {
        StructureEvent.BREAK_OF_STRUCTURE,
        StructureEvent.CHANGE_OF_CHARACTER,
        StructureEvent.CHOCH_FAILED,
    }
)

QUICK = (5, 10, 20, 40, 80)
WINDOWS = 3
#: Fracao mais antiga da amostra que serve de discovery (o resto e holdout).
DISCOVERY_FRACTION = 0.70

#: O caso que originou a etapa.
ZEC_CASE = ("ZECUSDT", TimeFrame.D1, "2026-06-04T00:00:00+00:00")


# --------------------------------------------------------------------------
# 2. o detector contrafactual
# --------------------------------------------------------------------------


def detect_stall(
    candles: Sequence[Candle],
    events: Sequence[MarketStructure],
    *,
    openers: frozenset[StructureEvent] = BOS_ONLY,
    n: int = N,
    k_atr: float = K,
) -> StructuralStall | None:
    """`liquidity.structural_stall.detect_structural_stall` com o guard aberto.

    Copia verbatim da producao a menos de uma linha:

        - if opener.event is not StructureEvent.BREAK_OF_STRUCTURE:
        + if opener.event not in openers:

    Com `openers=BOS_ONLY` tem de ser indistinguivel da producao -- e o que
    `test_choch_leg_opener.py::test_bos_only_e_a_producao` verifica em series
    reais, e o que autoriza ler qualquer diferenca medida como efeito do CHoCH
    e de mais nada.
    """
    if n < 0 or k_atr < 0:
        raise ValueError("n and k_atr must be non-negative")
    if len(candles) < 2:
        return None

    index_by_timestamp = {candle.timestamp: index for index, candle in enumerate(candles)}
    advance = _last_advance(events, index_by_timestamp)
    if advance is None:
        return None
    opener, advance_index = advance
    if opener.event not in openers:
        return None

    atr = frozen_atr_pct(candles, advance_index)
    if atr <= 0:
        return None

    bullish = opener.direction is MarketDirection.BULLISH
    entry = opener.price_level
    if entry <= 0:
        return None

    extreme = candles[advance_index].close
    extreme_timestamp = candles[advance_index].timestamp
    for index in range(advance_index, len(candles)):
        close = candles[index].close
        if bullish:
            if close > extreme:
                extreme, extreme_timestamp = close, candles[index].timestamp
            give_back = extreme - close
        else:
            if close < extreme:
                extreme, extreme_timestamp = close, candles[index].timestamp
            give_back = close - extreme
        bars = index - advance_index
        retracement = give_back / entry / atr
        if bars >= n and retracement >= k_atr:
            return StructuralStall(
                stale_since=candles[index].timestamp,
                direction=opener.direction,
                last_advance_timestamp=opener.timestamp,
                last_advance_price=entry,
                bars_since_advance=bars,
                retracement_atr=retracement,
                frozen_atr_pct=atr,
                leg_extreme_price=extreme,
                leg_extreme_timestamp=extreme_timestamp,
            )
    return None


# --------------------------------------------------------------------------
# pernas e gatilhos
# --------------------------------------------------------------------------


@dataclass
class LegStall:
    """Uma perna, seu opener, e o STALE que ela produziria (ou nao)."""

    symbol: str
    timeframe: str
    window: int
    opener_event: str
    opener_timestamp: str
    opener_index: int
    direction: str
    #: A tendencia que o stream deixa DEPOIS do opener (secao 9). Tem de bater
    #: com `direction`; divergencia e reportada, nao silenciada.
    trend_after: str | None
    reference_structural: bool | None
    #: Ate o proximo advance (ou o fim da janela).
    leg_bars: int
    closed_by: str | None
    #: Retracao maxima alcancada dentro da perna, em ATR congelado.
    max_retracement_atr: float
    # --- gatilho ---
    stale_since: str | None = None
    bars_since_advance: int | None = None
    retracement_atr: float | None = None
    frozen_atr: float | None = None
    leg_extreme_price: float | None = None
    leg_extreme_timestamp: str | None = None
    stale_price: float | None = None
    # --- desfecho (avaliacao, nunca gatilho) ---
    outcome: str | None = None  # resumed | reversed | open
    bars_to_outcome: int | None = None


def advance_indices(
    events: Sequence[MarketStructure], by_ts: dict[datetime, int]
) -> list[tuple[MarketStructure, int]]:
    """Os advances nao-provisionais, na ordem, com o candle de cada um."""
    out = []
    for event in events:
        if event.provisional or event.event not in ADVANCES:
            continue
        index = by_ts.get(event.timestamp)
        if index is not None:
            out.append((event, index))
    out.sort(key=lambda pair: pair[1])
    return out


def trend_at(events: Sequence[MarketStructure], upto: datetime) -> MarketDirection | None:
    """A tendencia do stream ate `upto`, com a semantica do Tide/`_advance_boundaries`."""
    trend: MarketDirection | None = None
    for event in events:
        if event.provisional or event.event not in ADVANCES or event.timestamp > upto:
            continue
        if event.event is StructureEvent.CHOCH_FAILED:
            trend = (
                MarketDirection.BEARISH
                if event.direction is MarketDirection.BULLISH
                else MarketDirection.BULLISH
            )
        else:
            trend = event.direction
    return trend


def legs_of_run(
    run, symbol: str, timeframe: TimeFrame, window: int, openers: frozenset[StructureEvent]
) -> list[LegStall]:
    """Toda perna aberta por um evento de `openers`, com seu gatilho e desfecho.

    A perna vai do opener ate o PROXIMO advance (qualquer tipo) -- e a janela
    em que `_last_advance` devolveria esse opener, ou seja, exatamente o
    intervalo em que a producao poderia reportar esse stall. Chamar
    `detect_stall` sobre o prefixo que termina no fim da perna reproduz o que a
    producao veria ali; `test_...::test_causalidade_por_truncamento` verifica
    que truncar antes nao muda o gatilho.
    """
    candles = run.candles
    by_ts = {candle.timestamp: index for index, candle in enumerate(candles)}
    advances = advance_indices(run.events, by_ts)
    out: list[LegStall] = []

    for position, (opener, index) in enumerate(advances):
        if opener.event not in openers:
            continue
        following = advances[position + 1] if position + 1 < len(advances) else None
        # A perna vai ate a vela ANTERIOR ao proximo advance: naquela vela o
        # `_last_advance` ja e o novo evento, e a producao passa a reportar a
        # perna seguinte. Incluir o candle do proximo advance faria a janela
        # devolver o stall da perna errada (ou nenhum).
        end = following[1] - 1 if following else len(candles) - 1
        if end <= index:
            continue

        atr = frozen_atr_pct(candles, index)
        entry = opener.price_level
        bullish = opener.direction is MarketDirection.BULLISH
        extreme = candles[index].close
        max_retracement = 0.0
        for i in range(index, end + 1):
            close = candles[i].close
            extreme = max(extreme, close) if bullish else min(extreme, close)
            give_back = (extreme - close) if bullish else (close - extreme)
            if atr > 0 and entry > 0:
                max_retracement = max(max_retracement, give_back / entry / atr)

        leg = LegStall(
            symbol=symbol,
            timeframe=timeframe.value,
            window=window,
            opener_event=opener.event.value,
            opener_timestamp=opener.timestamp.isoformat(),
            opener_index=index,
            direction=opener.direction.value,
            trend_after=(t.value if (t := trend_at(run.events, opener.timestamp)) else None),
            reference_structural=opener.reference_structural,
            leg_bars=end - index,
            closed_by=(following[0].event.value if following else None),
            max_retracement_atr=round(max_retracement, 2),
        )

        # O gatilho, pela MESMA funcao que a producao usaria, sobre o prefixo
        # que termina no fim da perna. Nenhum candle depois do fim da perna
        # participa -- e nenhum evento posterior tampouco.
        stall = detect_stall(
            candles[: end + 1],
            [e for e in run.events if e.timestamp <= candles[end].timestamp],
            openers=openers,
        )
        if stall is not None and stall.last_advance_timestamp == opener.timestamp:
            trigger_index = by_ts[stall.stale_since]
            leg.stale_since = stall.stale_since.isoformat()
            leg.bars_since_advance = stall.bars_since_advance
            leg.retracement_atr = stall.retracement_atr
            leg.frozen_atr = stall.frozen_atr_pct
            leg.leg_extreme_price = stall.leg_extreme_price
            leg.leg_extreme_timestamp = stall.leg_extreme_timestamp.isoformat()
            leg.stale_price = candles[trigger_index].close
            leg.outcome, leg.bars_to_outcome = _outcome(
                run.events, by_ts, trigger_index, bullish
            )
        out.append(leg)
    return out


def _outcome(
    events: Sequence[MarketStructure],
    by_ts: dict[datetime, int],
    trigger_index: int,
    bullish: bool,
) -> tuple[str, int | None]:
    """Primeiro BOS nao-provisional depois do gatilho (avaliacao retrospectiva).

    Mesma definicao de `research/structural_stall_validation.py`: `resumed` se
    for da direcao da perna (o STALE foi desmentido), `reversed` se for oposto,
    `open` se a janela acabar antes. Retrospectivo de proposito -- avalia o
    gatilho, nunca o dispara.
    """
    for event in events:
        if event.provisional or event.event is not StructureEvent.BREAK_OF_STRUCTURE:
            continue
        index = by_ts.get(event.timestamp)
        if index is None or index <= trigger_index:
            continue
        same = (event.direction is MarketDirection.BULLISH) == bullish
        return ("resumed" if same else "reversed"), index - trigger_index
    return "open", None


# --------------------------------------------------------------------------
# coleta
# --------------------------------------------------------------------------


def collect(
    symbols: Sequence[str] = SYMBOLS,
    timeframes: Sequence[TimeFrame] = TIMEFRAMES,
    windows: int = WINDOWS,
    limit: int = LIMIT,
) -> tuple[list[LegStall], list[LegStall]]:
    """As duas populacoes de pernas: guard atual e guard proposto."""
    current: list[LegStall] = []
    proposed: list[LegStall] = []
    for symbol in symbols:
        for timeframe in timeframes:
            if not (CACHE_DIR / f"{symbol}_{timeframe.value}.json").exists():
                print(f"! sem cache: {symbol} {timeframe.value}")
                continue
            try:
                series = load_series(symbol, timeframe)
            except Exception as error:  # noqa: BLE001 - cache com vela corrompida
                # `data/providers/binance` recusa a vela e o dominio esta
                # certo: a serie e que esta suja (ver
                # `project_egld_invalid_candle_bug`). Pular o combo e
                # reportar; interromper a varredura inteira por um simbolo
                # seria pior.
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
                    # Perp deslistado: a Binance segue emitindo velas de
                    # amplitude zero no preco de liquidacao, o detector
                    # confirma uma caixa de altura zero e o dominio recusa
                    # (ver `project_degenerate_consolidation_range`). Nao e
                    # mercado -- pular a janela.
                    print(f"! janela invalida: {symbol} {timeframe.value} w{window}: {error}")
                    continue
                current.extend(legs_of_run(run, symbol, timeframe, window, BOS_ONLY))
                proposed.extend(legs_of_run(run, symbol, timeframe, window, BOS_OR_CHOCH))
    return current, proposed


# --------------------------------------------------------------------------
# 3/4. comparacao
# --------------------------------------------------------------------------


def key_of(leg: LegStall) -> tuple:
    """Identidade de uma perna.

    `opener_event` faz parte da chave porque um mesmo candle pode carregar um
    BOS e um CHoCH: sem ele uma perna nova de CHoCH casaria com a perna de BOS
    do mesmo timestamp e o invariante da secao 4 seria avaliado contra o
    objeto errado.
    """
    return (leg.symbol, leg.timeframe, leg.window, leg.opener_timestamp, leg.opener_event)


#: Os campos que o invariante da secao 4 exige identicos.
INVARIANT_FIELDS = (
    "stale_since",
    "bars_since_advance",
    "retracement_atr",
    "frozen_atr",
    "leg_extreme_price",
    "leg_extreme_timestamp",
)


def compare(current: Sequence[LegStall], proposed: Sequence[LegStall]) -> dict:
    """Custo exato de aceitar CHoCH: o que aparece, e o que NAO pode ter mudado."""
    by_key = {key_of(leg): leg for leg in current}
    identical, changed, new_stalls, new_legs_no_stall = 0, [], [], 0
    for leg in proposed:
        old = by_key.get(key_of(leg))
        if old is None:
            # Perna que so existe no guard proposto: aberta por CHoCH.
            if leg.stale_since:
                new_stalls.append(leg)
            else:
                new_legs_no_stall += 1
            continue
        diff = {
            field_name: (getattr(old, field_name), getattr(leg, field_name))
            for field_name in INVARIANT_FIELDS
            if getattr(old, field_name) != getattr(leg, field_name)
        }
        if diff:
            changed.append({"key": list(key_of(leg)), "diff": diff})
        else:
            identical += 1
    return {
        "legs_current": len(current),
        "legs_proposed": len(proposed),
        "legs_added": len(proposed) - len(current),
        "stalls_current": sum(1 for leg in current if leg.stale_since),
        "stalls_proposed": sum(1 for leg in proposed if leg.stale_since),
        "bos_legs_identical": identical,
        "bos_legs_changed": changed,
        "new_stalls": [asdict(leg) for leg in new_stalls],
        "new_choch_legs_without_stall": new_legs_no_stall,
    }


def outcome_mix(legs: Sequence[LegStall]) -> dict:
    """`resumed`/`reversed`/`open` e os `quick_resume_*` -- a metrica que condena."""
    fired = [leg for leg in legs if leg.stale_since]
    total = len(fired)
    if not total:
        return {"n": 0}
    counts = Counter(leg.outcome for leg in fired)
    out = {
        "n": total,
        "resumed": counts["resumed"] / total,
        "reversed": counts["reversed"] / total,
        "open": counts["open"] / total,
    }
    for horizon in QUICK:
        out[f"quick_resume_{horizon}"] = (
            sum(
                1
                for leg in fired
                if leg.outcome == "resumed"
                and leg.bars_to_outcome is not None
                and leg.bars_to_outcome <= horizon
            )
            / total
        )
    return out


def split_holdout(legs: Sequence[LegStall]) -> tuple[list[LegStall], list[LegStall]]:
    """70% mais antigo / 30% mais recente, por tempo do opener."""
    ordered = sorted(legs, key=lambda leg: leg.opener_timestamp)
    cut = int(len(ordered) * DISCOVERY_FRACTION)
    return ordered[:cut], ordered[cut:]


# --------------------------------------------------------------------------
# 11. replay stability
# --------------------------------------------------------------------------


def replay_stability(
    symbol: str,
    timeframe: TimeFrame,
    *,
    openers: frozenset[StructureEvent] = BOS_OR_CHOCH,
    step: int = STEP,
    refreshes: int = REFRESHES,
    limit: int = LIMIT,
) -> Counter:
    """Refreshes deslizantes, taxonomia de `research/stall_replay_stability.py`.

    Aqui filtrado aos stalls cujo opener e um CHoCH -- os NOVOS. A pergunta
    nao e se o stall e estavel em geral (ja medido), e se o que a
    generalizacao introduz repinta mais que o resto.
    """
    series = load_series(symbol, timeframe)
    tally: Counter = Counter()
    previous = None
    for refresh in range(refreshes):
        end = len(series) - (refreshes - 1 - refresh) * step
        start = end - limit - BUFFER
        if start < 0:
            continue
        run = dd._run_internal_structure(
            provider=SliceProvider(series[start:end]),
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            confluence_filter=True,
        )
        stall = detect_stall(run.candles, run.events, openers=openers)
        by_ts = {candle.timestamp: index for index, candle in enumerate(run.candles)}
        opener_event = None
        if stall is not None:
            opener_event = next(
                (
                    e.event
                    for e in run.events
                    if e.timestamp == stall.last_advance_timestamp and e.event in openers
                ),
                None,
            )
        current = (run, stall, opener_event, by_ts)
        if previous is not None:
            tally[_compare_refresh(previous, current)] += 1
        previous = current
    return tally


def _compare_refresh(previous, current) -> str:
    """Mesmas categorias de `stall_replay_stability.compare`, so para CHoCH openers."""
    _, before, before_event, _ = previous
    run_after, after, after_event, by_ts_after = current
    choch = StructureEvent.CHANGE_OF_CHARACTER
    if before_event is not choch and after_event is not choch:
        return "irrelevante"
    if before is None or before_event is not choch:
        assert after is not None
        age = len(run_after.candles) - 1 - by_ts_after.get(
            after.stale_since, len(run_after.candles) - 1
        )
        return "retroativo" if age > RETRO_MARGIN else "novo"
    if after is None:
        advance = _last_advance(run_after.events, by_ts_after)
        if advance is None or advance[0].timestamp != before.last_advance_timestamp:
            return "perna nova"
        return "sumiu"
    if after.last_advance_timestamp != before.last_advance_timestamp:
        return "perna nova"
    return "estavel" if after.stale_since == before.stale_since else "deslocado"


# --------------------------------------------------------------------------
# 7. o caso ZEC D1
# --------------------------------------------------------------------------


def zec_case() -> dict:
    """O CHoCH bearish de 2026-06-04 sob o guard proposto, com todos os numeros."""
    symbol, timeframe, opener_ts = ZEC_CASE
    series = load_series(symbol, timeframe)
    run = dd._run_internal_structure(
        provider=SliceProvider(series[-(LIMIT + BUFFER) :]),
        symbol=symbol,
        timeframe=timeframe,
        limit=LIMIT,
        confluence_filter=True,
    )
    candles = run.candles
    by_ts = {candle.timestamp: index for index, candle in enumerate(candles)}
    legs = legs_of_run(run, symbol, timeframe, 0, BOS_OR_CHOCH)
    leg = next(leg for leg in legs if leg.opener_timestamp == opener_ts)

    advances = advance_indices(run.events, by_ts)
    opener_index = by_ts[datetime.fromisoformat(opener_ts)]
    following = next(
        (event for event, index in advances if index > opener_index), None
    )
    out = asdict(leg)
    out["production_stall"] = detect_stall(candles, run.events, openers=BOS_ONLY) is not None
    out["next_advance"] = (
        {
            "event": following.event.value,
            "direction": following.direction.value,
            "timestamp": following.timestamp.isoformat(),
        }
        if following
        else None
    )
    if leg.stale_since and following:
        stale_index = by_ts[datetime.fromisoformat(leg.stale_since)]
        next_index = by_ts[following.timestamp]
        out["bars_before_next_advance"] = next_index - stale_index
        # Quanto da recuperacao ja tinha acontecido no STALE: do fundo da perna
        # ate o extremo alcancado quando o proximo advance chegou.
        bottom = min(candles[i].low for i in range(opener_index, next_index + 1))
        top = max(candles[i].high for i in range(opener_index, next_index + 1))
        span = top - bottom
        if span > 0:
            out["recovery_done_pct_at_stale"] = (
                (candles[stale_index].close - bottom) / span * 100
            )
    return out


# --------------------------------------------------------------------------
# relatorio
# --------------------------------------------------------------------------


@dataclass
class Report:
    n: int = N
    k: float = K
    comparison: dict = field(default_factory=dict)
    by_timeframe: dict = field(default_factory=dict)
    choch_legs: list[dict] = field(default_factory=list)
    outcomes_bos: dict = field(default_factory=dict)
    outcomes_new: dict = field(default_factory=dict)
    holdout: dict = field(default_factory=dict)
    direction_mismatches: list[dict] = field(default_factory=list)
    replay: dict = field(default_factory=dict)
    zec: dict = field(default_factory=dict)


def build(
    symbols: Sequence[str] = SYMBOLS,
    timeframes: Sequence[TimeFrame] = TIMEFRAMES,
    windows: int = WINDOWS,
) -> Report:
    current, proposed = collect(symbols, timeframes, windows)
    report = Report()
    report.comparison = compare(current, proposed)

    for timeframe in timeframes:
        label = timeframe.value
        cur = [leg for leg in current if leg.timeframe == label]
        pro = [leg for leg in proposed if leg.timeframe == label]
        new = [
            leg
            for leg in pro
            if leg.opener_event == StructureEvent.CHANGE_OF_CHARACTER.value
            and leg.stale_since
        ]
        report.by_timeframe[label] = {
            "legs_current": len(cur),
            "legs_proposed": len(pro),
            "stalls_current": sum(1 for leg in cur if leg.stale_since),
            "stalls_proposed": sum(1 for leg in pro if leg.stale_since),
            "new_stalls": len(new),
            "outcomes_new": outcome_mix(new),
            "outcomes_bos": outcome_mix(cur),
        }

    choch_legs = [
        leg
        for leg in proposed
        if leg.opener_event == StructureEvent.CHANGE_OF_CHARACTER.value
    ]
    report.choch_legs = [asdict(leg) for leg in choch_legs]
    report.outcomes_bos = outcome_mix(current)
    report.outcomes_new = outcome_mix(choch_legs)

    discovery, holdout = split_holdout(choch_legs)
    report.holdout = {
        "discovery": {
            "legs": len(discovery),
            "stalls": sum(1 for leg in discovery if leg.stale_since),
            **outcome_mix(discovery),
        },
        "holdout": {
            "legs": len(holdout),
            "stalls": sum(1 for leg in holdout if leg.stale_since),
            **outcome_mix(holdout),
        },
    }

    # Secao 9: a direcao da perna tem de ser a tendencia que o evento deixou.
    report.direction_mismatches = [
        {
            "symbol": leg.symbol,
            "timeframe": leg.timeframe,
            "opener": leg.opener_timestamp,
            "direction": leg.direction,
            "trend_after": leg.trend_after,
        }
        for leg in proposed
        if leg.trend_after is not None and leg.trend_after != leg.direction
    ]

    # A estabilidade e sobre repintura, nao sobre tamanho de amostra: rodar os
    # refreshes deslizantes no painel pedido basta e mantem o custo linear.
    report.replay = {
        f"{symbol} {timeframe.value}": dict(replay_stability(symbol, timeframe))
        for symbol in symbols
        if symbol in SYMBOLS
        for timeframe in timeframes
        if (CACHE_DIR / f"{symbol}_{timeframe.value}.json").exists()
    }
    report.zec = zec_case()
    return report


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument(
        "--expanded", action="store_true", help="todo simbolo do cache com os 4 TFs"
    )
    parser.add_argument("--timeframes", nargs="*", default=[t.value for t in TIMEFRAMES])
    parser.add_argument("--windows", type=int, default=WINDOWS)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    symbols = args.symbols or (expanded_symbols() if args.expanded else SYMBOLS)
    report = build(symbols, [TimeFrame(t) for t in args.timeframes], args.windows)
    print(f"simbolos: {len(symbols)}")
    c = report.comparison
    print(f"N={report.n} K={report.k}\n")
    print("== 3/4. GUARD ATUAL vs PROPOSTO ==")
    print(
        f"  pernas: {c['legs_current']} -> {c['legs_proposed']} (+{c['legs_added']})\n"
        f"  stalls: {c['stalls_current']} -> {c['stalls_proposed']} "
        f"(+{c['stalls_proposed'] - c['stalls_current']})\n"
        f"  pernas de BOS identicas bit a bit: {c['bos_legs_identical']} "
        f"| alteradas: {len(c['bos_legs_changed'])}\n"
        f"  pernas novas (CHoCH) sem stall: {c['new_choch_legs_without_stall']}"
    )
    for item in c["bos_legs_changed"]:
        print(f"  ! INVARIANTE QUEBRADO: {item}")

    print("\n== 5. POR TIMEFRAME ==")
    header = f"  {'tf':>5} {'pernas':>14} {'stalls':>13} {'novos':>6}"
    print(header + f" {'qr5':>5} {'qr10':>5} {'qr20':>5} {'qr40':>5} {'resum':>6} {'rev':>5}")
    for label, row in report.by_timeframe.items():
        o = row["outcomes_new"]
        print(
            f"  {label:>5} {row['legs_current']:>6}->{row['legs_proposed']:<7} "
            f"{row['stalls_current']:>5}->{row['stalls_proposed']:<6} "
            f"{row['new_stalls']:>6} {_pct(o.get('quick_resume_5')):>5} "
            f"{_pct(o.get('quick_resume_10')):>5} {_pct(o.get('quick_resume_20')):>5} "
            f"{_pct(o.get('quick_resume_40')):>5} {_pct(o.get('resumed')):>6} "
            f"{_pct(o.get('reversed')):>5}"
        )

    print("\n== 12. QUALIDADE: BOS (controle) vs NOVOS (CHoCH) ==")
    for label, mix in (("BOS", report.outcomes_bos), ("CHoCH", report.outcomes_new)):
        if not mix.get("n"):
            continue
        print(
            f"  {label:>6} n={mix['n']:>4}  resumed={_pct(mix['resumed'])} "
            f"reversed={_pct(mix['reversed'])} open={_pct(mix['open'])}  "
            + " ".join(f"qr{h}={_pct(mix[f'quick_resume_{h}'])}" for h in QUICK)
        )

    print("\n== 13. HOLDOUT (70/30 por tempo, pernas de CHoCH) ==")
    for label, row in report.holdout.items():
        print(
            f"  {label:>9} pernas={row['legs']:>4} stalls={row['stalls']:>4} "
            f"resumed={_pct(row.get('resumed'))} reversed={_pct(row.get('reversed'))} "
            f"qr20={_pct(row.get('quick_resume_20'))} qr40={_pct(row.get('quick_resume_40'))}"
        )

    print("\n== 9. DIRECAO (perna vs trend_after) ==")
    print(f"  divergencias: {len(report.direction_mismatches)}")
    for item in report.direction_mismatches[:10]:
        print(f"  ! {item}")

    print("\n== 11. REPLAY STABILITY (stalls com opener CHoCH) ==")
    totals: Counter = Counter()
    for label, tally in report.replay.items():
        relevant = {k: v for k, v in tally.items() if k != "irrelevante"}
        totals.update(tally)
        if relevant:
            print(f"  {label:>18}: {relevant}")
    print(f"  TOTAL: {dict(totals)}")

    print("\n== 7. ZEC D1 (CHoCH bearish 2026-06-04) ==")
    z = report.zec
    print(
        f"  stall com o guard de hoje: {z['production_stall']}\n"
        f"  stale_since={z['stale_since']} bars_since_advance={z['bars_since_advance']} "
        f"retracement_atr={z['retracement_atr'] and round(z['retracement_atr'], 2)}\n"
        f"  extremo da perna {z['leg_extreme_price']} em "
        f"{z['leg_extreme_timestamp'] and z['leg_extreme_timestamp'][:10]} | "
        f"preco no stale {z['stale_price']}\n"
        f"  proximo advance: {z['next_advance']} "
        f"({z.get('bars_before_next_advance')} dias depois do stale)\n"
        f"  recuperacao ja percorrida no stale: "
        f"{z.get('recovery_done_pct_at_stale') and round(z['recovery_done_pct_at_stale'], 1)}%\n"
        f"  desfecho: {z['outcome']} (+{z['bars_to_outcome']})"
    )

    if args.json:
        args.json.write_text(json.dumps(asdict(report), indent=1))
        print(f"\n-> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
