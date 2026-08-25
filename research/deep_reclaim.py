"""O setup da hipotese: reclaim do bloco com stop no fundo da visita.

Setup **separado** do block reclaim de producao, medido do zero e mantido a
parte de proposito. O original fica onde esta (`app/block_reclaim.py` +
`app/paper_journal.py`, gate `r_atr<=1.0`, stop no extremo do teste, alvo 2R);
este arquivo nao o toca e nao le nenhuma decisao dele.

A hipotese e do leitor, levantada sobre dois trades reais (ZECUSDT e UNIUSDT,
agosto de 2026): o stop nao pertence ao extremo do teste, e sim ao **fundo que
mergulhou dentro do bloco** durante a visita -- o ponto que, se perdido, nega a
tese. Com o stop la, aqueles dois valiam 9RR e 3RR.

Tres diferencas em relacao ao original, e so tres:

1. **Stop**: extremo da visita ao bloco, nao extremo do teste. Onde a visita
   comeca tem mais de uma definicao defensavel, entao o padrao e `visit10`
   (toques agrupados com folga de 10 velas) e as alternativas saem como campos
   -- `visit3/20/40`, `look10/20/30/50`, `blockedge` -- para serem medidas
   depois em vez de escolhidas agora.
2. **EMA9 como parte do setup**, nao como filtro opcional: so entra com a EMA9
   inclinada a favor. Usa `ema9_slope_lag1`, que termina UMA VELA ANTES do
   gatilho -- a versao que inclui a vela de gatilho mede o proprio pinbar
   levantando a media, e essa duvida ja custou uma rodada.
3. **Cor do pinbar exigida no `l2`** (`require_pinbar_color="l2"`). O grau
   `l2` mede o corpo como `abs(close - open)` e nunca pergunta a direcao, entao
   uma vela de ALTA, de corpo pesado e nariz curto, satisfaz o `l2` *baixista*.
   No original isso foi medido e deu empate (53,7% contra 55,1%), e por isso
   continua desligado la. Aqui e ligado por decisao de leitura, nao por
   medicao: uma vela verde cujo pavio superior e MENOR que o proprio corpo nao
   e rejeicao vendedora nenhuma. `--no-color` desliga para a diferenca ser
   medida neste setup, onde o empate do original nao se transfere de graca --
   sem o gate `r_atr` e com outro stop, a entrada mais esticada do `l2` cai
   direto no denominador do R. `legacy` e `l1` ficam de fora do corte: os dois
   limitam o corpo a 35% do range, entao quase nao sobra corpo para a cor
   errar.
4. **Calda de 65% no `legacy`** (`min_tail_fraction=STRICT_WICK_FRACTION`). O
   `legacy` limita o corpo mas nao diz nada sobre o nariz, entao um *doji*
   passa: calda 58%, corpo 2,6% e 39% de pavio do lado contrario que ninguem
   pergunta -- comprador e vendedor terminando empatados, lido como rejeicao.
   Subir o piso da calda para 65% resolve sem inventar um quarto limiar, ja
   que com 65% de calda o nariz cabe em 35% por construcao. Fica so aqui: no
   original toda medicao foi feita a 0,50 e o Sharpe da uniao se apoia
   principalmente no `legacy`.
5. **`r_atr` emitido, nunca filtrado.** No original ele e o gate; aqui e uma
   pergunta em aberto, porque um stop mais fundo produz `r_atr` maior por
   construcao e o teto do original nao se transfere. O relatorio sai por faixa
   para o teto ser escolhido com o numero na frente.

O que **nao** muda: o gatilho (`detect_block_reclaims`, uniao dos tres graus de
pinbar, rotas VWAP e EMA), o piso de acumulacao da VWAP -- `vwap_candles>=4`,
que existe porque a VWAP de sessao reancora a meia-noite UTC e cruza o preco
sozinha --, o descarte do teste que **atravessou o bloco** e saiu do outro lado
(o bloco nao segurou nada; ver `docs/block_reclaim.md`), o custo de 0,10% por
ida e volta, a divisao busca/holdout congelada em `_symbols.py`, e o controle aleatorio
**casado na direcao e no simbolo** (um controle sem direcao faz qualquer
periodo que tendeu parecer preditivo).

Alvos 2R e 3R, horizontes 40 e 120 velas.

Run:
    poetry run python -m research.deep_reclaim --out /tmp/deep_reclaim.json
    poetry run python -m research.deep_reclaim --report-only /tmp/deep_reclaim.json
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Sequence
from pathlib import Path
from statistics import fmean

from liquidity_hunter.app.block_reclaim import (
    STRICT_WICK_FRACTION,
    detect_block_reclaims,
)
from liquidity_hunter.app.dashboard_data import load_dashboard_data
from liquidity_hunter.core.domain import Candle, MarketDirection, TimeFrame
from liquidity_hunter.data.exceptions import DataProviderError
from liquidity_hunter.indicators import ema_series
from pydantic import ValidationError
from research._paginated import NoFuturesProvider, PaginatedFuturesProvider
from research._symbols import UNIVERSE, sample_of

#: Ida e volta, taker nas duas pontas. O custo em R e `COST_PCT / r_pct`, e e
#: por isso que o stop importa duas vezes: ele decide o denominador.
COST_PCT = 0.0010
TARGETS = (2.0, 3.0)
HORIZONS = (40, 120)
ATR_PERIOD = 14
#: Piso de acumulacao da VWAP, medido em `research/vwap_age_walkforward.py`.
MIN_VWAP_CANDLES = 4
#: Folgas de agrupamento de toques que definem "a visita". O padrao e 10.
MERGE_GAPS = (3, 10, 20, 40)
DEFAULT_GAP = 10
#: Stops alternativos por lookback fixo, para a comparacao.
LOOKBACKS = (10, 20, 30, 50)
RANDOM_REPS = 1


def _atr(candles: Sequence[Candle], i: int) -> float | None:
    if i < ATR_PERIOD:
        return None
    trs = [
        max(candles[j].high - candles[j].low,
            abs(candles[j].high - candles[j - 1].close),
            abs(candles[j].low - candles[j - 1].close))
        for j in range(i - ATR_PERIOD + 1, i + 1)
    ]
    return fmean(trs) or None


def visit_start(touches: Sequence[int], i0: int, gap: int) -> int:
    """Onde comecou a visita que contem (ou precede) o gatilho, com esta folga.

    Caminhando para tras a partir do ultimo toque em `i0` ou antes, qualquer
    toque anterior a menos de `gap` velas pertence a mesma visita.
    """
    prior = [t for t in touches if t <= i0]
    if not prior:
        return i0
    start = prior[-1]
    for t in reversed(prior[:-1]):
        if start - t <= gap:
            start = t
        else:
            break
    return start


def stops(
    candles: Sequence[Candle], i0: int, *, bull: bool,
    block_low: float, block_high: float, touches: Sequence[int],
) -> dict[str, float]:
    """Cada definicao de stop, nomeada. `visit10` e a da hipotese."""
    out: dict[str, float] = {}
    for g in MERGE_GAPS:
        w = candles[visit_start(touches, i0, g) : i0 + 1]
        out[f"visit{g}"] = min(c.low for c in w) if bull else max(c.high for c in w)
    for k in LOOKBACKS:
        w = candles[max(0, i0 - k + 1) : i0 + 1]
        out[f"look{k}"] = min(c.low for c in w) if bull else max(c.high for c in w)
    out["blockedge"] = block_low if bull else block_high
    return out


def outcome(candles, i0, entry, stop, r, *, bull, target, horizon) -> float:
    """R realizado: alvo, stop, ou o que estiver aberto no fim do horizonte."""
    w = candles[i0 + 1 : i0 + 1 + horizon]
    for c in w:
        if (c.low <= stop) if bull else (c.high >= stop):
            return -1.0
        if (c.high >= entry + target * r) if bull else (c.low <= entry - target * r):
            return target
    if not w:
        return 0.0
    move = w[-1].close - entry
    return (move if bull else -move) / r


def _row_outcomes(candles, i0, entry, stop, r, *, bull) -> dict[str, float]:
    out = {}
    for target in TARGETS:
        tag = str(target).replace(".", "").rstrip("0") or "0"
        for h in HORIZONS:
            out[f"r{tag}_h{h}"] = outcome(
                candles, i0, entry, stop, r, bull=bull, target=target, horizon=h)
    return out


def run(symbols, timeframe, limit, out, *, gap, require_ema9, min_vwap,
        drop_pierced=True, pinbar_color="l2"):
    provider, futures = PaginatedFuturesProvider(), NoFuturesProvider()
    rng = random.Random(7)
    rows: list[dict] = []
    dropped = {"ema9": 0, "vwap": 0, "atravessou": 0}
    for n, symbol in enumerate(symbols, 1):
        try:
            data = load_dashboard_data(
                provider=provider, symbol=symbol, timeframe=timeframe, limit=limit,
                futures_provider=futures, compute_narrative=False,
            )
        except (DataProviderError, ValidationError) as exc:
            first = str(exc).splitlines()
            detail = first[1].strip() if len(first) > 1 else (first[0] if first else "")
            print(f"  ! {symbol} pulado: {type(exc).__name__}: {detail[:120]}", flush=True)
            continue
        candles = data.candles
        if len(candles) < 400 or data.vwap is None:
            continue
        idx = {c.timestamp: i for i, c in enumerate(candles)}
        e9 = ema_series(candles, 9)
        reclaims = detect_block_reclaims(
            candles, data.poi_zones, data.vwap, symbol=symbol,
            timeframe=timeframe, ema=e9, require_pinbar_color=pinbar_color,
            min_tail_fraction=STRICT_WICK_FRACTION,
        )
        kept = 0
        for rec in reclaims:
            if rec.provisional:
                continue
            i0 = idx[rec.timestamp]
            if i0 + max(HORIZONS) >= len(candles):
                continue
            atr = _atr(candles, i0)
            if not atr:
                continue
            if rec.vwap_candles < min_vwap:
                dropped["vwap"] += 1
                continue
            bull = rec.direction is MarketDirection.BULLISH
            # O bloco atravessado de ponta a ponta pelo pavio da propria visita
            # do gatilho: nenhuma vela fechou alem dele, entao o detector ainda
            # o considera vivo, mas nao sobrou ordem para reagir. Emitido tambem
            # como campo, para o custo do corte ser mensuravel aqui tambem.
            pierced = (
                rec.test_extreme < rec.block_price_low if bull
                else rec.test_extreme > rec.block_price_high
            )
            if pierced and drop_pierced:
                dropped["atravessou"] += 1
                continue
            sign = 1.0 if bull else -1.0
            # A inclinacao termina na vela ANTERIOR ao gatilho: um pinbar de
            # reclaim forte levanta a EMA9 sozinho, e sem a defasagem nao da
            # para saber se o eixo e contexto ou e o gatilho dito de novo.
            slope = (
                None if i0 - 1 < 10 or e9[i0 - 1] is None or e9[i0 - 10] is None
                else sign * (e9[i0 - 1] - e9[i0 - 10]) / atr
            )
            if require_ema9 and not (slope is not None and slope > 0):
                dropped["ema9"] += 1
                continue
            entry = rec.reclaim_price
            touches = [
                j for j, c in enumerate(candles[: i0 + 1])
                if c.low <= rec.block_price_high and c.high >= rec.block_price_low
            ]
            all_stops = stops(
                candles, i0, bull=bull, block_low=rec.block_price_low,
                block_high=rec.block_price_high, touches=touches,
            )
            stop = all_stops[f"visit{gap}"]
            r = (entry - stop) if bull else (stop - entry)
            if r <= 0:
                continue
            row = {
                "symbol": symbol, "sample": sample_of(symbol),
                "timestamp": rec.timestamp.isoformat(),
                "direction": rec.direction.value,
                "arm": f"visit{gap}",
                "entry": entry, "stop": stop,
                "r_pct": r / entry, "r_atr": r / atr,
                # o stop do setup original, para a diferenca ficar visivel
                "r_atr_extreme": abs(entry - rec.test_extreme) / atr,
                "stop_extreme": rec.test_extreme,
                "ema9_slope_lag1": slope,
                "vwap_candles": rec.vwap_candles,
                "first_test": rec.first_test,
                "pierced": pierced,
                "pinbar_grade": rec.pinbar_grade,
                "trigger_line": rec.trigger_line,
                "block_atr": (rec.block_price_high - rec.block_price_low) / atr,
                "visit_candles": i0 - visit_start(touches, i0, gap) + 1,
            }
            row.update(_row_outcomes(candles, i0, entry, stop, r, bull=bull))
            # Os stops alternativos, so o R, para medir o teto depois.
            for name, alt in all_stops.items():
                ra = (entry - alt) if bull else (alt - entry)
                row[f"r_atr_{name}"] = (ra / atr) if ra > 0 else None
            rows.append(row)
            kept += 1

            # Controle casado em simbolo E direcao, com o R deste braco: sem
            # casar a direcao, qualquer periodo que tendeu parece preditivo.
            for _ in range(RANDOM_REPS):
                j = rng.randrange(ATR_PERIOD, len(candles) - max(HORIZONS) - 1)
                centry = candles[j].close
                cstop = centry - r if bull else centry + r
                crow = {
                    "symbol": symbol, "sample": sample_of(symbol),
                    "timestamp": candles[j].timestamp.isoformat(),
                    "direction": rec.direction.value, "arm": "aleatorio",
                    "r_pct": r / centry, "r_atr": r / atr,
                }
                crow.update(_row_outcomes(candles, j, centry, cstop, r, bull=bull))
                rows.append(crow)
        print(f"[{n}/{len(symbols)}] {symbol:11s} {kept} entradas", flush=True)
    Path(out).write_text(json.dumps(rows))
    print(f"\ngravado {len(rows)} linhas -> {out}", flush=True)
    print(f"descartados: EMA9 contra {dropped['ema9']}, vwap jovem "
          f"{dropped['vwap']}, atravessou o bloco {dropped['atravessou']}")
    report(rows)


def _net(rs, key, target) -> str:
    if len(rs) < 40:
        return f"n={len(rs):5d} (poucos)"
    hit = sum(1 for r in rs if r[key] >= target - 0.01) / len(rs)
    net = fmean(r[key] - COST_PCT / r["r_pct"] for r in rs)
    tot = sum(r[key] - COST_PCT / r["r_pct"] for r in rs)
    return f"n={len(rs):5d}  acerto {hit:5.1%}  liq {net:+.3f}  total {tot:+8.1f}R"


def report(rows: Sequence[dict]) -> None:
    """Por faixa de `r_atr`, para o teto ser escolhido com o numero na frente.

    Faixas **disjuntas** de proposito: faixas cumulativas escondem que o lucro
    de um teto largo pode vir inteiro do nucleo apertado dentro dele -- foi
    exatamente o que quase passou batido na grade do setup original.
    """
    BANDS = ((0.0, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, float("inf")))
    for sample in ("search", "holdout"):
        S = [r for r in rows if r["sample"] == sample]
        real = [r for r in S if r["arm"] != "aleatorio"]
        ctrl = [r for r in S if r["arm"] == "aleatorio"]
        if len(real) < 40:
            continue
        for target in TARGETS:
            tag = str(target).replace(".", "").rstrip("0") or "0"
            key = f"r{tag}_h{HORIZONS[0]}"
            print(f"\n=== {sample} · alvo {target:g}R · h{HORIZONS[0]}")
            print(f"  {'tudo':22s} {_net(real, key, target)}")
            print(f"  {'  controle aleatorio':22s} {_net(ctrl, key, target)}")
            for lo, hi in BANDS:
                lbl = f"r_atr {lo:g}-{hi:g}".replace("-inf", "+")
                print(f"  {lbl:22s} "
                      f"{_net([r for r in real if lo < r['r_atr'] <= hi], key, target)}")
                print(f"  {'  (aleatorio)':22s} "
                      f"{_net([r for r in ctrl if lo < r['r_atr'] <= hi], key, target)}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+", default=list(UNIVERSE))
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--limit", type=int, default=60_000)
    p.add_argument("--gap", type=int, default=DEFAULT_GAP, choices=MERGE_GAPS,
                   help="folga de agrupamento que define a visita (padrao 10)")
    p.add_argument("--no-ema9", action="store_true",
                   help="nao exigir a EMA9 a favor (para medir o que ela custa)")
    p.add_argument("--min-vwap", type=int, default=MIN_VWAP_CANDLES)
    p.add_argument("--no-color", action="store_true",
                   help="aceita pinbar l2 de cor contraria (o comportamento do "
                        "setup original), para medir o que o corte custa")
    p.add_argument("--keep-pierced", action="store_true",
                   help="manter os testes que atravessaram o bloco")
    p.add_argument("--out", default="/tmp/deep_reclaim.json")
    p.add_argument("--report-only", default=None)
    a = p.parse_args()
    if a.report_only:
        report(json.loads(Path(a.report_only).read_text()))
        return
    run(a.symbols, TimeFrame(a.timeframe), a.limit, a.out,
        gap=a.gap, require_ema9=not a.no_ema9, min_vwap=a.min_vwap,
        pinbar_color=None if a.no_color else "l2",
        drop_pierced=not a.keep_pierced)


if __name__ == "__main__":
    main()
