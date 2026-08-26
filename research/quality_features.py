"""Cinco features candidatas a separar o setup A/A+/A++ -- camada experimental.

**Nada aqui altera o setup.** O gatilho, o stop, o alvo e os gates continuam
sendo os de producao (`app.block_reclaim` + `app.paper_journal.OPERATING_GATES`),
importados e nao reescritos. Este arquivo so ANOTA cada entrada com features
novas e mede se alguma delas separa os resultados. Nenhuma feature entra em
nenhum criterio de entrada: elas sao emitidas e cortadas na analise, a mesma
disciplina que `r_atr` e `pinbar_grade` ja seguem.

A populacao base
----------------
`detect_block_reclaims` com a EMA9 como segunda rota (a fiacao de producao),
gate M15 `r_atr <= 1.0` e `vwap_candles >= 4` (`OPERATING_GATES[M15]`), stop no
extremo do teste, entrada no fechamento do reclaim. Ou seja: **exatamente as
operacoes que o plano operacional manda tomar**, e nao um superconjunto.

As cinco features (todas causais -- so leem velas ja fechadas antes da entrada)
-------------------------------------------------------------------------------
1. `vwap_ob_dist_atr`  -- |VWAP - borda mais proxima do OB| / ATR, e
   `vwap_inside_ob`. A tese da camada e que os dois niveis sao UM so; esta e a
   medida direta disso, e complementa `r_atr` (que mede ate o pavio do teste).
2. `disp_atr` / `disp_candles` / `disp_eff` -- o deslocamento que criou o OB:
   do bloco ate o extremo da perna, antes de o preco voltar para testa-lo.
   Normalizado pelo ATR **local ao bloco**, nao ao gatilho: um bloco de 300
   velas atras viveu em outra volatilidade.
3. `sweep_*` -- varredura do swing anterior antes da entrada, com ou sem
   fechamento de volta para dentro, e se a varredura aconteceu DENTRO do OB.
4. `ema_vwap_dist_atr` + `ema_vwap_aligned` -- as duas linhas juntas e
   inclinadas a favor. As inclinacoes terminam UMA VELA ANTES do gatilho: um
   pinbar forte levanta a EMA9 sozinho, e sem a defasagem o eixo seria o
   proprio gatilho dito de novo (a duvida ja custou uma rodada em
   `deep_reclaim`).
5. `pen_pct` / `pen_atr` / `wick_beyond_ob` / `close_beyond_ob` -- quanto o
   teste penetrou no bloco, e se algum fechamento passou do outro lado
   (invalidacao pelo FECHAMENTO, nunca pelo pavio).

Regua
-----
Alvos 1R, 2R, 2,5R e 3R; horizontes 40 e 120 velas. **O relatorio usa h120 por
padrao**: o h40 trunca vencedores (um trade que fez 2,5R foi registrado como
+0,97R porque o horizonte fecha a posicao a mercado), e o vies e sistematico
contra a entrada cedo e contra o `r_atr` alto. Os dois saem lado a lado.

Todo R e **liquido de custo** (`COST_PCT / r_pct`): comparar filtros que mudam
o numero de operacoes sobre R bruto favorece sempre quem opera mais.

Contra overfitting
------------------
Toda tabela sai em quatro recortes: `search`/`holdout` (divisao por simbolo
congelada em `_symbols.py`, hash do nome) e `early`/`late` (divisao temporal
60/40 do calendario). Um limiar so e reportado como promissor se sobrevive nos
dois eixos. A varredura de limiares e explicitamente exploratoria: os limiares
sao os que o leitor pediu, fixos, nao escolhidos pelo resultado -- e mesmo
assim, sao 5 features x 4 limiares x varias combinacoes, entao o melhor numero
da grade esta selecionado por construcao e so o comportamento nos recortes
independentes conta como evidencia.

Rodar
-----
    poetry run python -m research.quality_features --out /tmp/qf.json
    poetry run python -m research.quality_features --report-only /tmp/qf.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from statistics import fmean
from typing import Any

from liquidity_hunter.app.block_reclaim import detect_block_reclaims
from liquidity_hunter.app.dashboard_data import load_dashboard_data
from liquidity_hunter.app.paper_journal import (
    OPERATING_GATES,
    test_pierced_the_block,
)
from liquidity_hunter.core.domain import Candle, MarketDirection, TimeFrame
from liquidity_hunter.data.exceptions import DataProviderError
from liquidity_hunter.indicators import ema_series
from pydantic import ValidationError
from research._paginated import NoFuturesProvider, PaginatedFuturesProvider
from research._symbols import UNIVERSE, sample_of

#: Ida e volta, taker nas duas pontas -- o mesmo custo de todos os estudos.
COST_PCT = 0.0010
TARGETS = (1.0, 2.0, 2.5, 3.0)
HORIZONS = (40, 120)
MAIN_HORIZON = 120
MAIN_TARGET = 2.0
ATR_PERIOD = 14
#: Lookback do pivo usado SO para a feature 3 (varredura). Nao e o pivo do
#: detector de estrutura: aqui basta o topo/fundo local que um leitor enxerga.
SWING_LOOKBACK = 5
#: Ate quantas velas antes da entrada procurar a varredura.
SWEEP_WINDOW = 20
#: Amostra minima para uma linha ser lida como evidencia, e nao como anedota.
MIN_N = 60


class CachedProvider(PaginatedFuturesProvider):
    """`PaginatedFuturesProvider` que aceita cache vencido.

    O TTL de 6h existe para o cache nao servir uma serie velha a quem quer o
    edge vivo. Aqui a janela e historica e fechada: uma vela de 2025 nao muda,
    e refazer ~2.900 requisicoes de 1500 velas so para reescrever bytes
    identicos e o caminho mais curto para o ban que ja custou uma sessao
    (ver `project_binance_ban_request_budget`). Subclasse, para o provider de
    pesquisa continuar como esta.
    """

    def _rows(self, symbol: str, timeframe: TimeFrame, limit: int) -> list[list[Any]]:
        path = self._cache(symbol, timeframe)
        if path.exists():
            cached: list[list[Any]] = json.loads(path.read_text())
            if len(cached) >= limit:
                return cached
        return super()._rows(symbol, timeframe, limit)


# --------------------------------------------------------------------------
# utilitarios
# --------------------------------------------------------------------------


def _atr(candles: Sequence[Candle], i: int) -> float | None:
    if i < ATR_PERIOD:
        return None
    trs = [
        max(
            candles[j].high - candles[j].low,
            abs(candles[j].high - candles[j - 1].close),
            abs(candles[j].low - candles[j - 1].close),
        )
        for j in range(i - ATR_PERIOD + 1, i + 1)
    ]
    return fmean(trs) or None


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


def excursions(candles, i0, entry, stop, r, *, bull, target, horizon) -> tuple[float, float]:
    """MFE/MAE em R **ate a saida real** (alvo, stop ou fim do horizonte).

    Medir no horizonte inteiro registra o que aconteceu com um preco que a
    operacao ja nao acompanhava: um trade estopado em -1R na vela 3 ficava com
    MAE de 10R porque o preco continuou caindo por mais 117 velas. O MAE que
    interessa e o calor que a posicao ATRAVESSOU -- quanto ela chegou a doer
    antes de dar certo -- e ele termina quando a posicao termina.
    """
    w = candles[i0 + 1 : i0 + 1 + horizon]
    if not w:
        return 0.0, 0.0
    hi = entry + target * r
    lo = entry - target * r
    mfe = mae = 0.0
    for c in w:
        mfe = max(mfe, ((c.high - entry) if bull else (entry - c.low)) / r)
        mae = max(mae, ((entry - c.low) if bull else (c.high - entry)) / r)
        if (c.low <= stop) if bull else (c.high >= stop):
            break
        if (c.high >= hi) if bull else (c.low <= lo):
            break
    return max(mfe, 0.0), max(mae, 0.0)


def swing_levels(candles: Sequence[Candle], lookback: int) -> tuple[list, list]:
    """Pivos fractais, cada um datado por quando fica CONFIRMADO.

    Devolve duas listas de `(indice_de_confirmacao, preco)`. O indice de
    confirmacao e `k + lookback`: um pivo so e pivo depois que as velas da
    direita fecharam, entao usar `k` faria a feature ler o futuro.
    """
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    n = len(candles)
    for k in range(lookback, n - lookback):
        window = candles[k - lookback : k + lookback + 1]
        if candles[k].high >= max(c.high for c in window):
            highs.append((k + lookback, candles[k].high))
        if candles[k].low <= min(c.low for c in window):
            lows.append((k + lookback, candles[k].low))
    return highs, lows


def _last_level_before(pivots: Sequence[tuple[int, float]], j: int) -> float | None:
    """O ultimo pivo ja confirmado antes da vela `j`."""
    level = None
    for confirmed, price in pivots:
        if confirmed >= j:
            break
        level = price
    return level


# --------------------------------------------------------------------------
# as cinco features
# --------------------------------------------------------------------------


def f1_vwap_to_ob(vwap_value: float, lo: float, hi: float, atr: float) -> dict:
    """Distancia VWAP -> borda mais proxima do OB, e se a VWAP esta dentro."""
    inside = lo <= vwap_value <= hi
    dist = 0.0 if inside else min(abs(vwap_value - lo), abs(vwap_value - hi))
    return {"vwap_ob_dist_atr": dist / atr, "vwap_inside_ob": inside}


def f2_displacement(
    candles: Sequence[Candle], born: int, visit_start: int, *, bull: bool,
    lo: float, hi: float,
) -> dict:
    """Forca do deslocamento que criou o OB.

    Do bloco ate o extremo da perna que ele lancou, medido ANTES de o preco
    voltar para testa-lo (`visit_start`). Normalizado pelo ATR local ao bloco.
    """
    empty = {"disp_atr": None, "disp_candles": None, "disp_eff": None}
    if born is None or visit_start <= born + 1:
        return empty
    atr_b = _atr(candles, born)
    if not atr_b:
        return empty
    leg = candles[born + 1 : visit_start]
    if not leg:
        return empty
    if bull:
        k = max(range(len(leg)), key=lambda x: leg[x].high)
        travel = leg[k].high - hi
    else:
        k = min(range(len(leg)), key=lambda x: leg[x].low)
        travel = lo - leg[k].low
    n = k + 1
    if travel <= 0:
        return {"disp_atr": 0.0, "disp_candles": n, "disp_eff": 0.0}
    return {"disp_atr": travel / atr_b, "disp_candles": n, "disp_eff": travel / atr_b / n}


def f3_sweep(
    candles: Sequence[Candle], i0: int, *, bull: bool, atr: float,
    highs, lows, lo: float, hi: float, window: int,
) -> dict:
    """Varredura do swing anterior nas velas que antecedem a entrada.

    LONG varre o fundo anterior (`low < swing_low`), SHORT o topo. A varredura
    mais RECENTE dentro da janela e a que conta -- e a que o gatilho responde.
    """
    out = {
        "sweep_detected": False, "sweep_size_atr": None,
        "close_back_inside": None, "sweep_to_entry_candles": None,
        "sweep_into_ob": None,
    }
    start = max(ATR_PERIOD, i0 - window + 1)
    for j in range(i0, start - 1, -1):
        c = candles[j]
        level = _last_level_before(lows if bull else highs, j)
        if level is None:
            continue
        breached = (c.low < level) if bull else (c.high > level)
        if not breached:
            continue
        extreme = c.low if bull else c.high
        out["sweep_detected"] = True
        out["sweep_size_atr"] = abs(extreme - level) / atr
        out["close_back_inside"] = (c.close > level) if bull else (c.close < level)
        out["sweep_to_entry_candles"] = i0 - j
        out["sweep_into_ob"] = lo <= extreme <= hi
        break
    return out


def f4_ema_vwap(
    candles: Sequence[Candle], ema: Sequence[float | None], i0: int,
    vwap_at: dict, *, bull: bool, atr: float,
) -> dict:
    """Distancia EMA9 <-> VWAP e alinhamento das duas inclinacoes.

    As inclinacoes terminam na vela ANTERIOR ao gatilho, pelo mesmo motivo de
    `deep_reclaim`: o proprio pinbar levanta a EMA9.
    """
    out = {
        "ema_vwap_dist_atr": None, "ema_slope_with": None,
        "vwap_slope_with": None, "ema_vwap_aligned": None,
    }
    e_now = ema[i0] if i0 < len(ema) else None
    v_now = vwap_at.get(candles[i0].timestamp)
    if e_now is None or v_now is None:
        return out
    out["ema_vwap_dist_atr"] = abs(e_now - v_now) / atr
    sign = 1.0 if bull else -1.0
    if i0 - 10 >= 0 and ema[i0 - 1] is not None and ema[i0 - 10] is not None:
        out["ema_slope_with"] = sign * (ema[i0 - 1] - ema[i0 - 10]) > 0
    v_prev = vwap_at.get(candles[i0 - 1].timestamp) if i0 >= 1 else None
    v_back = vwap_at.get(candles[i0 - 10].timestamp) if i0 >= 10 else None
    if v_prev is not None and v_back is not None:
        out["vwap_slope_with"] = sign * (v_prev - v_back) > 0
    if out["ema_slope_with"] is not None and out["vwap_slope_with"] is not None:
        out["ema_vwap_aligned"] = out["ema_slope_with"] and out["vwap_slope_with"]
    return out


def f5_penetration(
    candles: Sequence[Candle], visit_start: int, i0: int, *,
    bull: bool, lo: float, hi: float, extreme: float, atr: float,
) -> dict:
    """Profundidade da penetracao no OB e comportamento do fechamento.

    A borda de ENTRADA e a que o preco encontra primeiro (topo do bloco numa
    compra); a borda LONGE e a que, perdida por FECHAMENTO, invalida.
    """
    height = hi - lo
    near, far = (hi, lo) if bull else (lo, hi)
    depth = (near - extreme) if bull else (extreme - near)
    depth = max(depth, 0.0)
    closed_beyond = any(
        (c.close < far) if bull else (c.close > far)
        for c in candles[visit_start : i0 + 1]
    )
    wick_beyond = (extreme < far) if bull else (extreme > far)
    return {
        "pen_pct": (depth / height) if height > 0 else None,
        "pen_atr": depth / atr,
        "wick_beyond_ob": wick_beyond,
        "close_beyond_ob": closed_beyond,
        "ob_invalidated": closed_beyond,
        "wick_only_beyond": wick_beyond and not closed_beyond,
    }


# --------------------------------------------------------------------------
# varredura
# --------------------------------------------------------------------------


def scan(symbols: Sequence[str], timeframe: TimeFrame, limit: int, out_path: str) -> list[dict]:
    provider, futures = CachedProvider(), NoFuturesProvider()
    max_r_atr, min_vwap = OPERATING_GATES[timeframe]
    rows: list[dict] = []
    t0 = time.time()
    for n, symbol in enumerate(symbols, 1):
        try:
            data = load_dashboard_data(
                provider=provider, symbol=symbol, timeframe=timeframe, limit=limit,
                futures_provider=futures, compute_narrative=False,
            )
        except (DataProviderError, ValidationError) as exc:
            detail = str(exc).splitlines()
            msg = detail[1].strip() if len(detail) > 1 else (detail[0] if detail else "")
            print(f"  ! {symbol} pulado: {type(exc).__name__}: {msg[:110]}", flush=True)
            continue
        candles = data.candles
        if len(candles) < 400 or data.vwap is None:
            continue
        idx = {c.timestamp: i for i, c in enumerate(candles)}
        vwap_at = {p.timestamp: p.value for p in data.vwap.points}
        ema = ema_series(candles, 9)
        highs, lows = swing_levels(candles, SWING_LOOKBACK)
        reclaims = detect_block_reclaims(
            candles, data.poi_zones, data.vwap,
            symbol=symbol, timeframe=timeframe, ema=ema,
        )
        kept = 0
        for rec in reclaims:
            if rec.provisional:
                continue
            i0 = idx[rec.timestamp]
            if i0 + max(HORIZONS) >= len(candles):
                continue
            # --- os gates de PRODUCAO, nao um superconjunto
            if rec.r_atr is None or rec.r_atr > max_r_atr:
                continue
            if rec.vwap_candles < min_vwap:
                continue
            # O plano operacional tambem recusa o teste que atravessou o bloco
            # (`passes_gates` -> `test_pierced_the_block`). Sem este gate a
            # populacao base nao e a que se opera, e a feature 5 aparece forte
            # so por redescobrir uma regra que ja esta ligada: os 144 trades
            # `pen >= 100%` da primeira rodada NAO existem no setup real.
            # Emitido como campo tambem, para o corte continuar mensuravel.
            pierced = test_pierced_the_block(rec)
            if pierced:
                continue
            atr = _atr(candles, i0)
            if not atr:
                continue
            bull = rec.direction is MarketDirection.BULLISH
            entry, stop = rec.reclaim_price, rec.test_extreme
            r = (entry - stop) if bull else (stop - entry)
            if r <= 0:
                continue
            lo, hi = rec.block_price_low, rec.block_price_high
            visit_start = idx[rec.test_start_timestamp]
            born = idx.get(rec.block_timestamp)

            row: dict[str, Any] = {
                "symbol": symbol, "sample": sample_of(symbol),
                "timestamp": rec.timestamp.isoformat(),
                "direction": rec.direction.value,
                "entry": entry, "stop": stop,
                "r_pct": r / entry, "r_atr": rec.r_atr,
                "vwap_candles": rec.vwap_candles,
                "first_test": rec.first_test,
                "trigger_line": rec.trigger_line,
                "pinbar_grade": rec.pinbar_grade,
                "block_age_candles": None if born is None else visit_start - born,
                "pierced": pierced,
            }
            row.update(f1_vwap_to_ob(rec.vwap_price, lo, hi, atr))
            row.update(f2_displacement(candles, born, visit_start, bull=bull, lo=lo, hi=hi))
            row.update(f3_sweep(candles, i0, bull=bull, atr=atr, highs=highs,
                                lows=lows, lo=lo, hi=hi, window=SWEEP_WINDOW))
            row.update(f4_ema_vwap(candles, ema, i0, vwap_at, bull=bull, atr=atr))
            row.update(f5_penetration(candles, visit_start, i0, bull=bull, lo=lo,
                                      hi=hi, extreme=rec.test_extreme, atr=atr))
            for target in TARGETS:
                tag = str(target).replace(".", "").rstrip("0") or "0"
                for h in HORIZONS:
                    row[f"r{tag}_h{h}"] = outcome(candles, i0, entry, stop, r,
                                                  bull=bull, target=target, horizon=h)
            for h in HORIZONS:
                mfe, mae = excursions(candles, i0, entry, stop, r, bull=bull,
                                      target=MAIN_TARGET, horizon=h)
                row[f"mfe_h{h}"], row[f"mae_h{h}"] = mfe, mae
            rows.append(row)
            kept += 1
        print(f"[{n}/{len(symbols)}] {symbol:11s} {kept:4d} entradas "
              f"({time.time() - t0:5.0f}s)", flush=True)
    Path(out_path).write_text(json.dumps(rows))
    print(f"\ngravado {len(rows)} entradas -> {out_path}", flush=True)
    return rows


# --------------------------------------------------------------------------
# analise
# --------------------------------------------------------------------------


def net(row: dict, key: str) -> float:
    return row[key] - COST_PCT / row["r_pct"]


def metrics(rows: Sequence[dict], horizon: int) -> dict:
    """O bloco de estatisticas que o relatorio pede, para um recorte."""
    n = len(rows)
    if n == 0:
        return {"n": 0}
    main = f"r2_h{horizon}"
    nets = [net(r, main) for r in rows]
    wins = [x for x in nets if x > 0]
    losses = [-x for x in nets if x < 0]
    out = {
        "n": n,
        "avg_R": fmean(nets),
        "med_R": statistics.median(nets),
        "total_R": sum(nets),
        "mfe": fmean(r[f"mfe_h{horizon}"] for r in rows),
        "mae": fmean(r[f"mae_h{horizon}"] for r in rows),
        "pf": (sum(wins) / sum(losses)) if losses else float("inf"),
    }
    for target in TARGETS:
        tag = str(target).replace(".", "").rstrip("0") or "0"
        key = f"r{tag}_h{horizon}"
        out[f"wr_{target:g}R"] = sum(
            1 for r in rows if r[key] >= target - 0.01
        ) / n
    return out


def fmt(m: dict) -> str:
    if m["n"] == 0:
        return "n=0"
    return (
        f"n={m['n']:5d}  1R {m['wr_1R']:5.1%}  2R {m['wr_2R']:5.1%}  "
        f"2.5R {m['wr_2.5R']:5.1%}  3R {m['wr_3R']:5.1%}  "
        f"medio {m['avg_R']:+.3f}  mediana {m['med_R']:+.2f}  "
        f"MFE {m['mfe']:4.2f}  MAE {m['mae']:4.2f}  PF {m['pf']:4.2f}  "
        f"total {m['total_R']:+7.1f}R"
    )


def cuts(rows: Sequence[dict]) -> dict[str, list[dict]]:
    """Os quatro recortes independentes: dois por simbolo, dois por tempo."""
    stamps = sorted(datetime.fromisoformat(r["timestamp"]) for r in rows)
    split = stamps[int(0.6 * len(stamps))] if stamps else None
    return {
        "search": [r for r in rows if r["sample"] == "search"],
        "holdout": [r for r in rows if r["sample"] == "holdout"],
        "early": [r for r in rows if datetime.fromisoformat(r["timestamp"]) < split],
        "late": [r for r in rows if datetime.fromisoformat(r["timestamp"]) >= split],
    }


Rule = tuple[str, Callable[[dict], bool]]


def show(rows: Sequence[dict], rules: Sequence[Rule], horizon: int, base: dict) -> list[dict]:
    """Uma linha por regra, com os quatro recortes e o lift sobre a base."""
    results = []
    for name, keep in rules:
        sel = [r for r in rows if keep(r)]
        m = metrics(sel, horizon)
        if m["n"] == 0:
            print(f"  {name:34s} n=0")
            continue
        c = cuts(sel)
        lift = m["avg_R"] - base["avg_R"]
        flag = "" if m["n"] >= MIN_N else "  <- amostra fina"
        print(f"  {name:34s} {fmt(m)}  lift {lift:+.3f}{flag}")
        sub = {}
        for tag, part in c.items():
            mm = metrics(part, horizon)
            sub[tag] = mm
            if mm["n"]:
                print(f"    {tag:10s} n={mm['n']:5d}  2R {mm['wr_2R']:5.1%}  "
                      f"medio {mm['avg_R']:+.3f}  total {mm['total_R']:+7.1f}R"
                      f"{'' if mm['n'] >= MIN_N // 2 else '  (fino)'}")
        results.append({"name": name, "all": m, "cuts": sub, "lift": lift,
                        "keep": keep})
    return results


def feature_rules() -> dict[str, list[Rule]]:
    """Os limiares que o leitor pediu, fixos antes de olhar o resultado."""
    nn: Callable[[Any], bool] = lambda v: v is not None  # noqa: E731
    return {
        "F1 VWAP -> OB": [
            *[
                (f"vwap_ob_dist <= {t:.2f} ATR",
                 (lambda t: lambda r: nn(r["vwap_ob_dist_atr"]) and r["vwap_ob_dist_atr"] <= t)(t))
                for t in (0.25, 0.50, 0.75, 1.00)
            ],
            ("vwap_ob_dist > 1.00 ATR (complemento)",
             lambda r: nn(r["vwap_ob_dist_atr"]) and r["vwap_ob_dist_atr"] > 1.0),
            ("vwap_inside_ob", lambda r: r["vwap_inside_ob"] is True),
            ("vwap fora do OB (complemento)", lambda r: r["vwap_inside_ob"] is False),
        ],
        "F2 displacement do OB": [
            *[
                (f"disp > {t:g} ATR",
                 (lambda t: lambda r: nn(r["disp_atr"]) and r["disp_atr"] > t)(t))
                for t in (1, 2, 3, 4)
            ],
            *[
                (f"disp {lo:g}-{hi:g} ATR (bin)",
                 (lambda lo, hi: lambda r: nn(r["disp_atr"]) and lo < r["disp_atr"] <= hi)(lo, hi))
                for lo, hi in ((0, 1), (1, 2), (2, 3), (3, 4), (4, 1e9))
            ],
            ("disp_eff > 0.5 ATR/vela",
             lambda r: nn(r["disp_eff"]) and r["disp_eff"] > 0.5),
        ],
        "F3 sweep + rejeicao": [
            ("sweep_detected", lambda r: r["sweep_detected"] is True),
            ("sem sweep (complemento)", lambda r: r["sweep_detected"] is False),
            ("sweep + close_back_inside",
             lambda r: r["sweep_detected"] and r["close_back_inside"] is True),
            ("sweep sem close back",
             lambda r: r["sweep_detected"] and r["close_back_inside"] is False),
            ("sweep dentro do OB",
             lambda r: r["sweep_detected"] and r["sweep_into_ob"] is True),
            ("sweep + close back + dentro do OB",
             lambda r: r["sweep_detected"] and r["close_back_inside"] is True
             and r["sweep_into_ob"] is True),
            ("sweep <= 3 velas da entrada",
             lambda r: nn(r["sweep_to_entry_candles"]) and r["sweep_to_entry_candles"] <= 3),
        ],
        "F4 EMA9 <-> VWAP": [
            *[
                (f"ema_vwap_dist <= {t:.2f} ATR",
                 (lambda t: lambda r: nn(r["ema_vwap_dist_atr"])
                  and r["ema_vwap_dist_atr"] <= t)(t))
                for t in (0.25, 0.50, 0.75, 1.00)
            ],
            ("ema_vwap_dist > 1.00 ATR (complemento)",
             lambda r: nn(r["ema_vwap_dist_atr"]) and r["ema_vwap_dist_atr"] > 1.0),
            ("ema_vwap_aligned", lambda r: r["ema_vwap_aligned"] is True),
            ("nao alinhado (complemento)", lambda r: r["ema_vwap_aligned"] is False),
            ("proximas (<=0.5) E alinhadas",
             lambda r: nn(r["ema_vwap_dist_atr"]) and r["ema_vwap_dist_atr"] <= 0.5
             and r["ema_vwap_aligned"] is True),
            ("distantes (>1.0) sem alinhamento",
             lambda r: nn(r["ema_vwap_dist_atr"]) and r["ema_vwap_dist_atr"] > 1.0
             and r["ema_vwap_aligned"] is False),
        ],
        "F5 penetracao no OB": [
            *[
                (f"pen {lo:.0%}-{hi:.0%}",
                 (lambda lo, hi: lambda r: nn(r["pen_pct"]) and lo <= r["pen_pct"] < hi)(lo, hi))
                for lo, hi in ((0, .10), (.10, .25), (.25, .50), (.50, .75), (.75, 1.0))
            ],
            ("pen >= 100% (atravessou)",
             lambda r: nn(r["pen_pct"]) and r["pen_pct"] >= 1.0),
            ("wick alem do OB", lambda r: r["wick_beyond_ob"] is True),
            ("wick alem SEM fechar alem",
             lambda r: r["wick_only_beyond"] is True),
            ("ob_invalidated (fechou alem)",
             lambda r: r["ob_invalidated"] is True),
            ("ob NAO invalidado", lambda r: r["ob_invalidated"] is False),
        ],
    }


def combo_rules() -> list[Rule]:
    nn: Callable[[Any], bool] = lambda v: v is not None  # noqa: E731
    near_vwap = lambda r: nn(r["vwap_ob_dist_atr"]) and r["vwap_ob_dist_atr"] <= 0.5  # noqa: E731
    near_ema = lambda r: nn(r["ema_vwap_dist_atr"]) and r["ema_vwap_dist_atr"] <= 0.5  # noqa: E731
    return [
        ("C1 vwap<=.5 + ema<=.5", lambda r: near_vwap(r) and near_ema(r)),
        ("C2 vwap<=.5 + vwap dentro do OB",
         lambda r: near_vwap(r) and r["vwap_inside_ob"] is True),
        ("C3 sweep + close back + OB vivo",
         lambda r: r["sweep_detected"] and r["close_back_inside"] is True
         and r["ob_invalidated"] is False),
        ("C4 C1 + alinhadas",
         lambda r: near_vwap(r) and near_ema(r) and r["ema_vwap_aligned"] is True),
        ("C5 A++ (tudo junto)",
         lambda r: near_vwap(r) and near_ema(r) and nn(r["disp_atr"]) and r["disp_atr"] > 2
         and r["sweep_detected"] and r["close_back_inside"] is True
         and r["ob_invalidated"] is False),
    ]


def _r2(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def examples(rows: Sequence[dict], horizon: int, k: int = 3) -> None:
    key = f"r2_h{horizon}"
    ranked = sorted(rows, key=lambda r: net(r, key))
    def line(r):
        return (f"    {r['symbol']:10s} {r['timestamp'][:16]} {r['direction'][:4]:4s} "
                f"R={net(r, key):+.2f}  vwap_ob={r['vwap_ob_dist_atr']:.2f} "
                f"disp={_r2(r['disp_atr'])} "
                f"sweep={int(bool(r['sweep_detected']))}/"
                f"{'-' if r['close_back_inside'] is None else int(r['close_back_inside'])} "
                f"ema_gap={_r2(r['ema_vwap_dist_atr'])} "
                f"pen={_r2(r['pen_pct'])}")
    print("  melhores:")
    for r in ranked[-k:][::-1]:
        print(line(r))
    print("  piores:")
    for r in ranked[:k]:
        print(line(r))


def report(rows: Sequence[dict], horizon: int = MAIN_HORIZON) -> None:
    print(f"\n{'=' * 78}\nRELATORIO — alvo principal {MAIN_TARGET:g}R, horizonte h{horizon}")
    print(f"{len(rows)} entradas · custo {COST_PCT:.2%} ida e volta · R liquido\n{'=' * 78}")

    base = metrics(rows, horizon)
    print("\n### 1. BASELINE (setup atual, sem filtro adicional)")
    print(f"  {'baseline':34s} {fmt(base)}")
    for tag, part in cuts(rows).items():
        print(f"    {tag:10s} {fmt(metrics(part, horizon))}")
    print(f"\n  (h40, para comparacao) {fmt(metrics(rows, 40))}")

    print("\n### 2-4. FEATURES, UMA A UMA")
    ranking: list[tuple[str, dict]] = []
    for family, rules in feature_rules().items():
        print(f"\n-- {family}")
        res = show(rows, rules, horizon, base)
        best = [
            x for x in res
            if x["all"]["n"] >= MIN_N and "complemento" not in x["name"]
        ]
        if best:
            top = max(best, key=lambda x: x["lift"])
            ranking.append((family, top))

    print("\n### 5. COMBINACOES")
    combos = show(rows, combo_rules(), horizon, base)

    print("\n### RANKING das features (melhor limiar de cada, por lift no R medio)")
    for i, (family, top) in enumerate(
        sorted(ranking, key=lambda x: -x[1]["lift"]), 1
    ):
        m, c = top["all"], top["cuts"]
        agree = sum(
            1 for t in ("search", "holdout", "early", "late")
            if c.get(t, {}).get("n", 0) >= MIN_N // 2
            and c[t]["avg_R"] > base["avg_R"]
        )
        print(f"  {i}. {family:24s} {top['name']:34s} lift {top['lift']:+.3f}  "
              f"n={m['n']:5d}  recortes a favor: {agree}/4")

    print("\n### 6. EXEMPLOS (melhores e piores do baseline)")
    examples(rows, horizon)

    print("\n### combos: quanto custam em numero de operacoes")
    for x in combos:
        share = x["all"]["n"] / base["n"]
        print(f"  {x['name']:34s} {share:5.1%} das operacoes  "
              f"lift {x['lift']:+.3f}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+", default=list(UNIVERSE))
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--limit", type=int, default=60_000)
    p.add_argument("--horizon", type=int, default=MAIN_HORIZON, choices=HORIZONS)
    p.add_argument("--out", default="/tmp/qf.json")
    p.add_argument("--report-only", default=None)
    a = p.parse_args()
    if a.report_only:
        report(json.loads(Path(a.report_only).read_text()), a.horizon)
        return
    rows = scan(a.symbols, TimeFrame(a.timeframe), a.limit, a.out)
    if rows:
        report(rows, a.horizon)


if __name__ == "__main__":
    main()
