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

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

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

# O replay e o custo dominante de qualquer medicao (uma passada por candle), e a
# resposta so depende da serie e da configuracao — ambas identificaveis. Cachear
# em disco torna uma reanalise (recortar por simbolo, trocar horizonte, mudar o
# controle) instantanea em vez de custar a varredura inteira de novo.
CACHE_DIR = Path(__file__).parent / ".replay_cache"

# Um evento e identificado por algo que as passagens de composicao nao reescrevem.
Key = tuple[datetime, StructureEvent, MarketDirection]


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


def provisional_keys(events: list[MarketStructure]) -> set[Key]:
    """As marcas *provisorias* de BOS/CHoCH (`BOS?` / `CHoCH?`) de uma passada."""
    return {key_of(e) for e in events if e.event in TREND_EVENTS and e.provisional}


def _cache_path(buffered: list[Candle], parts: list[str]) -> Path:
    """Chave de cache: a configuracao + a identidade exata da serie."""
    fingerprint = "|".join(
        [*parts, str(len(buffered)), buffered[0].timestamp.isoformat(),
         buffered[-1].timestamp.isoformat()]
    )
    digest = hashlib.sha256(fingerprint.encode()).hexdigest()[:20]
    return CACHE_DIR / f"{digest}.json"


def _load_cache(path: Path, buffered: list[Candle]) -> dict[Key, int] | None:
    if not path.exists():
        return None
    raw = json.loads(path.read_text())
    by_iso = {c.timestamp.isoformat(): c.timestamp for c in buffered}
    out: dict[Key, int] = {}
    for ts_iso, event_value, direction_value, cut in raw:
        timestamp = by_iso.get(ts_iso)
        if timestamp is None:
            return None  # a serie mudou sob o cache; refaz
        out[(timestamp, StructureEvent(event_value), MarketDirection(direction_value))] = cut
    return out


def _store_cache(path: Path, first_seen: dict[Key, int]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = [
        [key[0].isoformat(), key[1].value, key[2].value, cut]
        for key, cut in first_seen.items()
    ]
    path.write_text(json.dumps(payload))


def scan_first_emissions(
    buffered: list[Candle],
    *,
    symbol: str,
    timeframe: TimeFrame,
    limit: int,
    warmup: int = 400,
    confluence_filter: bool = False,
    provisional: bool = False,
) -> dict[Key, int]:
    """Primeiro prefixo em que cada marca aparece, sem alvo previo.

    Diferente de `scan_knowability`, nao parte de um conjunto de alvos: registra
    toda marca que a pipeline emitir em qualquer prefixo. E o que uma marca
    provisoria exige — ela pode aparecer no live edge e sumir sem nunca entrar na
    lista final, e e exatamente essa aparicao que se quer datar.
    """
    path = _cache_path(
        buffered,
        [symbol, timeframe.value, str(limit), str(warmup),
         str(confluence_filter), str(provisional)],
    )
    cached = _load_cache(path, buffered)
    if cached is not None:
        return cached

    select = provisional_keys if provisional else confirmed_keys
    first_seen: dict[Key, int] = {}
    for cut in range(warmup, len(buffered)):
        partial = _run_internal_structure(
            PrefixProvider(buffered[: cut + 1]), symbol, timeframe, limit, confluence_filter
        )
        for key in select(partial.events):
            first_seen.setdefault(key, cut)
    _store_cache(path, first_seen)
    return first_seen


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
