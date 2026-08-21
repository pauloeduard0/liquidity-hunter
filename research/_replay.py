"""Replay incremental: quando cada evento de estrutura passa a existir.

Um `MarketStructure` carrega o timestamp do candle que quebrou o nivel, mas so e
emitido depois que o pivo confirmador se formou — varios candles adiante. Medir
qualquer coisa a partir do timestamp do evento usa informacao que ainda nao
existia naquele candle.

Este modulo roda a pipeline de producao (`_run_internal_structure`) sobre
prefixos crescentes de uma serie ja baixada e devolve, por evento, o primeiro
indice em que ele aparece — o indice *conhecivel*. O provider e um dublê que
fatia a serie em memoria, entao nao ha refetch: uma passada custa ~17ms sobre
1500 candles.

Usado por `research/event_lag.py` (estatistica do atraso) e por
`research/control_continuation.py` (entrada honesta na medicao de continuacao).
"""

from dataclasses import dataclass, field

from liquidity_hunter.app.dashboard_data import _run_internal_structure
from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.data import OHLCVProvider

TREND_EVENTS = {StructureEvent.BREAK_OF_STRUCTURE, StructureEvent.CHANGE_OF_CHARACTER}

# Um evento e identificado por algo que as passagens de composicao nao reescrevem.
Key = tuple[object, StructureEvent, MarketDirection]


class PrefixProvider(OHLCVProvider):
    """Dublê de `OHLCVProvider` que serve prefixos de uma serie ja baixada."""

    max_fetch_limit = 10_000

    def __init__(self, candles: list[Candle]) -> None:
        self.candles = candles

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        return self.candles[-limit:]


def key_of(event: MarketStructure) -> Key:
    return (event.timestamp, event.event, event.direction)


def confirmed_keys(events: list[MarketStructure]) -> set[Key]:
    """As marcas confirmadas (nao-provisorias) de BOS/CHoCH de uma passada."""
    return {key_of(e) for e in events if e.event in TREND_EVENTS and not e.provisional}


@dataclass(frozen=True)
class Knowability:
    """Quando cada evento apareceu, e se ele se manteve depois de aparecer."""

    # key -> primeiro indice (na serie bufferizada) em que o evento existe
    first_seen: dict[Key, int] = field(default_factory=dict)
    # eventos que apareceram, sumiram e voltaram: repaint, nao acionaveis
    unstable: set[Key] = field(default_factory=set)


def scan_knowability(
    buffered: list[Candle],
    targets: set[Key],
    *,
    symbol: str,
    timeframe: TimeFrame,
    limit: int,
    warmup: int = 400,
    confluence_filter: bool = False,
) -> Knowability:
    """Roda a pipeline sobre prefixos crescentes e datar a aparicao de cada alvo.

    `warmup` e o primeiro prefixo avaliado — abaixo dele a pipeline nao tem
    bootstrap nem ancora estrutural suficientes para se comportar como em
    producao, e o que ela emitisse ali nao seria representativo.
    """
    result = Knowability()
    if not targets:
        return result
    for cut in range(warmup, len(buffered)):
        partial = _run_internal_structure(
            PrefixProvider(buffered[: cut + 1]), symbol, timeframe, limit, confluence_filter
        )
        seen = confirmed_keys(partial.events)
        for key in targets:
            if key in seen:
                result.first_seen.setdefault(key, cut)
            elif key in result.first_seen:
                result.unstable.add(key)
    return result
