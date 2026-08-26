"""Quanto tempo depois do toque no bloco a retomada ainda conta?

`MAX_WAIT_CANDLES = 20` nunca foi medido -- entrou como numero redondo. O caso
que levantou a duvida e do leitor, em BTCUSDT M15: o bloco foi tocado as 11:00
do dia 21/06/2026 e a rejeicao na EMA9 imprimiu as 17:45, **25 velas depois**,
cortada pela janela por cinco velas. O toque aconteceu; a pergunta e se o
vinculo entre o teste e a retomada expira, e quando.

Medido em UMA passada, com a janela aberta em `WIDE` e a espera de cada
gatilho emitida como campo, em vez de N varreduras cumulativas. A diferenca
importa: faixas cumulativas ja enganaram este estudo uma vez -- toda janela
larga contem a estreita, entao o lucro do nucleo aparece em todas e a morte
so aparece quando as faixas sao **disjuntas**.

**Previsao registrada antes de rodar** (para o resultado nao ser lido depois
como se fosse esperado): espera maior deve pagar menos, porque o vinculo com o
teste enfraquece e o preco ja andou -- espero queda monotonica do acerto ao
longo das faixas, com a faixa 21-40 ainda acima do controle aleatorio e a
faixa 60+ indistinguivel dele. Se a faixa 21-40 vier IGUAL ou MELHOR que a
0-20, a janela atual esta cortando trade bom e o numero deve subir.

Run:
    poetry run python -m research.wait_window --symbols BTCUSDT ETHUSDT
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from statistics import fmean

from liquidity_hunter.app import block_reclaim as BR
from liquidity_hunter.app.block_reclaim import STRICT_WICK_FRACTION, detect_block_reclaims
from liquidity_hunter.app.dashboard_data import load_dashboard_data
from liquidity_hunter.core.domain import MarketDirection, POIZoneKind, TimeFrame
from liquidity_hunter.data.exceptions import DataProviderError
from liquidity_hunter.indicators import ema_series
from pydantic import ValidationError
from research._paginated import NoFuturesProvider, PaginatedFuturesProvider
from research._symbols import UNIVERSE
from research.deep_reclaim import (
    COST_PCT,
    DEFAULT_GAP,
    MIN_VWAP_CANDLES,
    _atr,
    outcome,
    stops,
    visit_start,
)

#: Aberta o bastante para a cauda aparecer; as faixas fazem o corte depois.
WIDE = 120
BANDS = ((0, 20), (21, 40), (41, 60), (61, 90), (91, WIDE))
TARGETS = ((2.0, "2R"), (2.5, "2,5R"), (3.0, "3R"))
HORIZON = 40


def _wait_of(candles, zone, reclaim, i0: int, vwap_at) -> int | None:
    """Velas entre o FIM da visita que gerou o gatilho e o proprio gatilho."""
    bull = zone.direction is MarketDirection.BULLISH
    for start, end, _first in BR._visits(candles, zone, vwap_at, bullish=bull):
        if candles[start].timestamp == reclaim.test_start_timestamp:
            return i0 - end
    return None


def collect(symbols: Sequence[str], timeframe: TimeFrame, limit: int) -> list[dict]:
    provider, futures = PaginatedFuturesProvider(), NoFuturesProvider()
    rows: list[dict] = []
    for n, symbol in enumerate(symbols, 1):
        try:
            data = load_dashboard_data(
                provider=provider, symbol=symbol, timeframe=timeframe, limit=limit,
                futures_provider=futures, compute_narrative=False,
            )
        except (DataProviderError, ValidationError) as exc:
            print(f"  ! {symbol} pulado: {type(exc).__name__}", flush=True)
            continue
        candles = data.candles
        if len(candles) < 400 or data.vwap is None:
            continue
        idx = {c.timestamp: i for i, c in enumerate(candles)}
        vwap_at = {p.timestamp: p.value for p in data.vwap.points}
        e9 = ema_series(candles, 9)
        blocks = {
            (z.price_low, z.price_high, z.ob_candle_timestamp): z
            for z in data.poi_zones if z.kind is POIZoneKind.ORDER_BLOCK
        }
        kept = 0
        for rec in detect_block_reclaims(
            candles, data.poi_zones, data.vwap, symbol=symbol, timeframe=timeframe,
            ema=e9, require_pinbar_color="l2", min_tail_fraction=STRICT_WICK_FRACTION,
            max_wait_candles=WIDE,
        ):
            if rec.provisional:
                continue
            i0 = idx[rec.timestamp]
            if i0 + HORIZON >= len(candles):
                continue
            atr = _atr(candles, i0)
            if not atr or rec.vwap_candles < MIN_VWAP_CANDLES:
                continue
            bull = rec.direction is MarketDirection.BULLISH
            if (rec.test_extreme < rec.block_price_low if bull
                    else rec.test_extreme > rec.block_price_high):
                continue
            sign = 1.0 if bull else -1.0
            if i0 - 1 < 10 or e9[i0 - 1] is None or e9[i0 - 10] is None:
                continue
            if sign * (e9[i0 - 1] - e9[i0 - 10]) / atr <= 0:
                continue
            zone = blocks.get(
                (rec.block_price_low, rec.block_price_high, rec.block_timestamp))
            wait = None if zone is None else _wait_of(candles, zone, rec, i0, vwap_at)
            if wait is None or wait < 0:
                continue
            touches = [
                j for j, c in enumerate(candles[: i0 + 1])
                if c.low <= rec.block_price_high and c.high >= rec.block_price_low
            ]
            stop = stops(candles, i0, bull=bull, block_low=rec.block_price_low,
                         block_high=rec.block_price_high, touches=touches)[
                             f"visit{DEFAULT_GAP}"]
            entry = rec.reclaim_price
            r = (entry - stop) if bull else (stop - entry)
            if r <= 0:
                continue
            row = {"symbol": symbol, "wait": wait, "r_atr": r / atr,
                   "r_pct": r / entry,
                   "visit_candles": i0 - visit_start(touches, i0, DEFAULT_GAP) + 1}
            for target, _ in TARGETS:
                row[target] = outcome(candles, i0, entry, stop, r,
                                      bull=bull, target=target, horizon=HORIZON)
            rows.append(row)
            kept += 1
        print(f"[{n}/{len(symbols)}] {symbol:12s} {kept} gatilhos", flush=True)
    return rows


def report(rows: list[dict]) -> None:
    print(f"\n{'espera (velas)':<16}{'n':>6}"
          + "".join(f"{'  ' + lab + ' acerto':>13}{'liquido':>9}" for _, lab in TARGETS))
    for lo, hi in BANDS:
        sub = [r for r in rows if lo <= r["wait"] <= hi]
        line = f"{f'{lo}-{hi}':<16}{len(sub):>6}"
        if len(sub) < 20:
            print(line + "   (poucos)")
            continue
        for target, _ in TARGETS:
            hit = sum(1 for r in sub if r[target] >= target) / len(sub)
            net = fmean(r[target] - COST_PCT / r["r_pct"] for r in sub)
            line += f"{hit:>12.1%}{net:>+9.3f}"
        print(line)
    print(f"\nmediana da espera: "
          f"{sorted(r['wait'] for r in rows)[len(rows)//2] if rows else 0} velas")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="+", default=list(UNIVERSE))
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--limit", type=int, default=60_000)
    a = p.parse_args()
    report(collect(a.symbols, TimeFrame(a.timeframe), a.limit))


if __name__ == "__main__":
    main()
