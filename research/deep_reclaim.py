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

6. **A sequencia nas duas linhas**, acrescentada em 2026-09-04 depois que o
   BTCUSD M15 ao vivo entrou vendido 0,76 ATR ACIMA da EMA9 com o fechamento
   0,10 ATR abaixo da VWAP -- nominalmente um reclaim, na tela nada. O gatilho
   de producao pede o lado bom de UMA linha (`on_vwap or on_ema`); a leitura do
   setup pede tres coisas, e todas ficam aqui, sem tocar em `block_reclaim.py`:

   - **perna**: depois do ultimo toque no bloco, alguma vela ANTERIOR ao
     gatilho fecha do lado bom das DUAS linhas (`departure`). Se a primeira a
     passar e o proprio pinbar, nao houve perna.
   - **fechamento acima das duas** (abaixo, na venda) na vela do gatilho.
   - **toque de pavio** (`wick_touch`): a minima fura a VWAP ou a EMA9 -- o que
     vier primeiro, e `ambas` no caso forte -- e o fechamento volta do lado bom.
     Um candle que so fecha acima nao testou nada.

   `--no-lines` desliga as tres para o custo delas ser medido.

   Quantas velas podem separar a perna do gatilho **nao** vira parametro: "o
   trade ja correu sem entrada" e "o stop ficaria gigantesco" sao a mesma
   frase, e o `r_atr` ja mede isso na unidade que importa. Oito velas de lado
   coladas nas linhas sao o setup e dao `r_atr` baixo; tres velas de foguete
   sao o descarte e dao `r_atr` alto -- um relogio erraria as duas.
   `candles_since_departure` sai como campo para a correlacao ser conferida.

7. **A geometria da visita, emitida sem filtro** (2026-09-04). O unico corte
   que sobreviveu a primeira grade M15 foi `r_atr<=2`, e ele entrou de
   carona: nao mede o stop, mede que o fundo da visita ficou perto da
   entrada -- a **visita rasa**. Se e isso que separa, entao os eixos da
   propria visita deveriam separar melhor, e sao seis: `penetration` (quanto
   do bloco a visita comeu), `visit_candles` e `test_candles` (quanto durou),
   `gap_to_trigger` (o quanto o preco ja tinha se afastado), `prior_visits`
   (a regra do leitor -- "o OB tem que continuar vivo"), `block_age` e
   `sweeps_in_block` (a zona ja defendeu preco antes?). Nenhum filtra: os
   seis saem como campo e o relatorio corta, pelo mesmo motivo que o
   `lines_ok` -- uma passada do universo responde as seis perguntas, e a
   alternativa custava seis.

   `sweeps_in_block` e a unica confluencia ja medida positiva neste projeto
   (sweep dentro de OB casado, `held@5` 76% contra 60%; 3+ sweeps na mesma
   zona, +14pp com dose-resposta) e nunca tinha sido ligada ao reclaim.

8. **O momento, tambem emitido sem filtro** (2026-09-06). O pedido do leitor
   e um filtro de momento que **nao corte o fluxo** -- com ~20 entradas por
   mes, um gate caro nao tem espaco. Entra RSI(14)
   (`liquidity_hunter/indicators/rsi.py`, novo), em quatro leituras:
   `rsi`/`rsi_lag1` (nivel na vela do gatilho e na anterior),
   `rsi_slope_lag1` (a inclinacao do momento antes do gatilho),
   `rsi_recovery` (quanto o RSI subiu desde o FUNDO da visita ate o gatilho)
   e `rsi_div` (divergencia contra a visita anterior ao mesmo bloco).

   O eixo da hipotese e o `rsi_recovery`, nao o nivel: "sobrevendido" e uma
   leitura de reversao, e este setup ja tem a reversao no gatilho. A pergunta
   util e se a perna que fez a visita **ja tinha perdido forca** quando o
   pinbar apareceu. Como sempre aqui, nenhum dos cinco filtra.

O que **nao** muda: o gatilho (`detect_block_reclaims`, uniao dos tres graus de
pinbar, rotas VWAP e EMA), o piso de acumulacao da VWAP -- `vwap_candles>=4`,
que existe porque a VWAP de sessao reancora a meia-noite UTC e cruza o preco
sozinha --, o descarte do teste que **atravessou o bloco** e saiu do outro lado
(o bloco nao segurou nada; ver `docs/block_reclaim.md`), o custo de 0,10% por
ida e volta, a divisao busca/holdout congelada em `_symbols.py`, e o controle aleatorio
**casado na direcao e no simbolo** (um controle sem direcao faz qualquer
periodo que tendeu parecer preditivo).

Alvos 2R, 2,5R e 3R, horizontes 40 e 120 velas.

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
from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.data.exceptions import DataProviderError
from liquidity_hunter.indicators import ema_series, rsi_series
from pydantic import ValidationError
from research._paginated import NoFuturesProvider, PaginatedFuturesProvider
from research._symbols import UNIVERSE, sample_of

#: Ida e volta, taker nas duas pontas. O custo em R e `COST_PCT / r_pct`, e e
#: por isso que o stop importa duas vezes: ele decide o denominador.
COST_PCT = 0.0010
TARGETS = (2.0, 2.5, 3.0)
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


def visit_clusters(touches: Sequence[int], gap: int) -> list[tuple[int, int]]:
    """Os toques no bloco agrupados em VISITAS, com a mesma folga do stop.

    A mesma regra que `visit_start` usa para achar o comeco da visita do
    gatilho, aplicada ao historico inteiro -- e dela que sai "quantas vezes
    este bloco ja foi visitado antes desta". Reusar a folga nao e economia:
    duas definicoes de visita no mesmo arquivo produziriam um `prior_visits`
    que nao conversa com o stop.
    """
    out: list[list[int]] = []
    for t in touches:
        if out and t - out[-1][1] <= gap:
            out[-1][1] = t
        else:
            out.append([t, t])
    return [(a, b) for a, b in out]


def departure(
    candles: Sequence[Candle], vwap_at: Sequence[float | None],
    ema: Sequence[float | None], last_touch: int, i0: int, *, bull: bool,
) -> int | None:
    """A vela em que a perna DEIXOU o bloco e passou das duas linhas.

    A leitura do setup e uma sequencia, nao um instante: o preco visita o
    bloco, **sobe acima da VWAP e da EMA9**, e so entao o pinbar de gatilho
    acontece trabalhando em cima delas. Sem exigir essa perna, o gatilho passa
    a valer no meio da visita -- que e como o BTCUSD de 2026-09-04 entrou
    vendido 0,76 ATR ACIMA da EMA9, com o fechamento colado na VWAP.

    Retorna o primeiro fechamento do lado bom das DUAS linhas depois do ultimo
    toque no bloco, e `None` quando a perna nao existe. Precisa ser ANTERIOR ao
    gatilho: se a primeira vela a passar das linhas e o proprio pinbar, nao
    houve perna nenhuma, houve so o pinbar.
    """
    for j in range(last_touch + 1, i0):
        v, e = vwap_at[j], ema[j]
        if v is None or e is None:
            continue
        close = candles[j].close
        if (close > v and close > e) if bull else (close < v and close < e):
            return j
    return None


def wick_touch(
    candle: Candle, vwap_value: float, ema_value: float, *, bull: bool,
) -> str | None:
    """Qual linha o PAVIO tocou, com o corpo terminando do lado bom.

    O toque tem que ser sombra: numa compra a minima fura a linha e o
    fechamento volta acima dela. Um candle que simplesmente fecha acima nao
    testou nada -- foi o pavio que foi la embaixo procurar oferta e nao achou.

    `"ambas"` quando as duas foram furadas no mesmo candle, que pela leitura do
    grafico e o caso forte; emitido separado para poder ser medido, nao
    filtrado.
    """
    if bull:
        hit_v = candle.low <= vwap_value <= candle.close
        hit_e = candle.low <= ema_value <= candle.close
    else:
        hit_v = candle.high >= vwap_value >= candle.close
        hit_e = candle.high >= ema_value >= candle.close
    if hit_v and hit_e:
        return "ambas"
    return "vwap" if hit_v else ("ema" if hit_e else None)


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
    # O extremo do PROPRIO candido de gatilho: a minima do pinbar numa compra,
    # a maxima numa venda. E o stop mais apertado que a leitura permite -- se o
    # pavio que testou a linha for perdido, o teste falhou e nao ha mais tese.
    # Entra porque o `r_atr` mandou: em toda faixa medida ate aqui, apertado
    # ganha, e o stop na visita produz 3,6 ATR justamente onde ganha menos.
    out["pinbar"] = candles[i0].low if bull else candles[i0].high
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
        drop_pierced=True, pinbar_color="l2", require_lines=False):
    provider, futures = PaginatedFuturesProvider(), NoFuturesProvider()
    rng = random.Random(7)
    rows: list[dict] = []
    dropped = {"ema9": 0, "vwap": 0, "atravessou": 0,
               "sem perna": 0, "fechou entre as linhas": 0, "toque de corpo": 0}
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
        r14 = rsi_series(candles, 14)
        vwap_by_ts = {point.timestamp: point.value for point in data.vwap.points}
        vwap_at = [vwap_by_ts.get(c.timestamp) for c in candles]
        # Os sweeps ja detectados nesta serie, como (indice, preco). Servem
        # para perguntar se o bloco visitado e uma zona que ja DEFENDEU preco
        # antes -- 3+ sweeps na mesma faixa mediram `held` +14pp com
        # dose-resposta (`research/sweep_cluster.py`), e essa leitura nunca
        # foi cruzada com o reclaim.
        sweeps = [
            (idx[e.timestamp], e.price_level)
            for e in (*data.market_structure_events, *data.internal_structure_events)
            if e.event is StructureEvent.LIQUIDITY_SWEEP and e.timestamp in idx
        ]
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
            # As tres regras que faltavam, na ordem em que o leitor as le no
            # grafico. Todas dependem das duas linhas na vela do gatilho, e
            # nenhuma delas existe no gatilho de producao -- por isso ficam
            # aqui, sem tocar em `block_reclaim.py`.
            v0, e0 = vwap_at[i0], e9[i0]
            if v0 is None or e0 is None:
                continue
            close0 = candles[i0].close
            clears_both = ((close0 > v0 and close0 > e0) if bull
                           else (close0 < v0 and close0 < e0))
            touched = wick_touch(candles[i0], v0, e0, bull=bull)
            legs = [
                j for j, c in enumerate(candles[: i0 + 1])
                if c.low <= rec.block_price_high and c.high >= rec.block_price_low
            ]
            start = departure(candles, vwap_at, e9, legs[-1] if legs else 0, i0,
                              bull=bull) if legs else None
            # EMITIDAS, nao filtradas: uma rodada so responde "com as regras"
            # e "sem as regras", que era o que `--no-lines` custava numa
            # segunda passada do universo inteiro.
            lines_ok = clears_both and touched is not None and start is not None
            if not clears_both:
                dropped["fechou entre as linhas"] += 1
            elif touched is None:
                dropped["toque de corpo"] += 1
            elif start is None:
                dropped["sem perna"] += 1
            if require_lines and not lines_ok:
                continue
            entry = rec.reclaim_price
            touches = legs
            # --- a geometria da visita, emitida e nunca filtrada ---------
            # O `r_atr<=2` da primeira grade sobreviveu por acidente, e o que
            # ele mede nao e o stop: e que o fundo da visita ficou perto da
            # entrada. Isso e uma propriedade da VISITA, e estes campos a
            # medem direto, para que o proxy possa ser aposentado (ou
            # confirmado como o melhor resumo dela).
            height = rec.block_price_high - rec.block_price_low
            # Quanto do bloco a visita comeu, contado a partir da borda por
            # onde o preco entrou. >1 significa que atravessou (o `pierced`).
            penetration = (
                (rec.block_price_high - rec.test_extreme) if bull
                else (rec.test_extreme - rec.block_price_low)
            ) / height
            clusters = visit_clusters(touches, gap)
            block_i = idx.get(rec.block_timestamp)
            test_i = idx.get(rec.test_start_timestamp)
            in_block = [
                (j, lvl) for j, lvl in sweeps
                if j < i0 and rec.block_price_low <= lvl <= rec.block_price_high
            ]
            # --- o momento, tambem emitido e nunca filtrado --------------
            # O RSI(14) entra pelo mesmo motivo que a EMA9 e a VWAP: e um
            # ponto de Schelling, olhado igual por todo mundo. A pergunta
            # deste setup NAO e "sobrevendido": e se a queda que fez a visita
            # ja tinha perdido forca quando o gatilho apareceu. Por isso o
            # eixo principal e `rsi_recovery` (o RSI subiu desde o fundo da
            # visita?) e nao o nivel solto -- e por isso nada aqui corta.
            vs = visit_start(touches, i0, gap)
            ext_i = (
                min(range(vs, i0 + 1), key=lambda j: candles[j].low) if bull
                else max(range(vs, i0 + 1), key=lambda j: candles[j].high)
            )

            def _sig(a, b, sign=sign):
                return None if a is None or b is None else sign * (a - b)

            # A divergencia classica, contra a visita ANTERIOR ao mesmo bloco:
            # preco fez extremo pior, momento nao acompanhou. Sem visita
            # anterior nao ha divergencia a medir -- fica `None`, nao `False`,
            # porque "nao houve" e "nao deu" nao sao a mesma linha.
            rsi_div = None
            if len(clusters) >= 2:
                pa, pb = clusters[-2]
                prev_i = (
                    min(range(pa, pb + 1), key=lambda j: candles[j].low) if bull
                    else max(range(pa, pb + 1), key=lambda j: candles[j].high)
                )
                worse = (
                    candles[ext_i].low < candles[prev_i].low if bull
                    else candles[ext_i].high > candles[prev_i].high
                )
                if r14[ext_i] is not None and r14[prev_i] is not None:
                    rsi_div = bool(
                        worse and (
                            r14[ext_i] > r14[prev_i] if bull
                            else r14[ext_i] < r14[prev_i]
                        )
                    )
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
                # Quanto o fechamento LIMPOU cada linha, com sinal a favor da
                # operacao. E a variavel que o BTCUSD expos: 0,10 na VWAP
                # (ruido lido como reclaim) e -0,76 na EMA9.
                "clear_vwap_atr": sign * (close0 - v0) / atr,
                "clear_ema_atr": sign * (close0 - e0) / atr,
                # Qual linha o pavio furou: `vwap`, `ema` ou `ambas`.
                "wick_line": touched,
                # As tres regras de linha satisfeitas de uma vez. `False` NAO
                # e descarte: a linha fica no arquivo para o corte ser medido.
                "lines_ok": lines_ok,
                # Velas entre a perna e o gatilho. Emitido para conferir se
                # anda junto com o `r_atr` -- se andar, contar velas seria um
                # segundo botao para a mesma coisa, e o `r_atr` ja e o teto.
                "candles_since_departure": (i0 - start) if start is not None else None,
                "visit_candles": i0 - visit_start(touches, i0, gap) + 1,
                # Fracao do bloco consumida pela visita. O gate de qualidade
                # `penetracao<50%` ja sobreviveu no M15/M30/H1 do setup
                # original; aqui ele e candidato a SUBSTITUIR o `r_atr`, nao
                # a acompanha-lo -- se os dois medirem a mesma coisa, um sai.
                "penetration": penetration,
                # A visita segundo o proprio detector (do primeiro toque ao
                # gatilho), ao lado da versao agrupada por folga acima.
                "test_candles": (i0 - test_i + 1) if test_i is not None else None,
                # Velas entre o ULTIMO toque no bloco e o gatilho: o quanto o
                # preco ja tinha se afastado quando a entrada apareceu.
                "gap_to_trigger": i0 - touches[-1] if touches else None,
                # Quantas visitas ANTERIORES este bloco ja tinha tido. A
                # regra do leitor ("o OB tem que continuar vivo, nao basta
                # tocar uma vez") vira numero aqui pela primeira vez.
                "prior_visits": max(0, len(clusters) - 1),
                # Idade do bloco em velas, do candle que o ancorou ate o
                # gatilho -- freshness medida em tempo, nao em toques.
                "block_age": (i0 - block_i) if block_i is not None else None,
                # Sweeps ja detectados DENTRO da faixa do bloco antes do
                # gatilho: a zona defendeu preco antes de voce entrar nela?
                "sweeps_in_block": len(in_block),
                # --- RSI(14) ------------------------------------------
                # Na vela do gatilho e na ANTERIOR a ela, pela mesma razao
                # que a EMA9 e lida com defasagem: um pinbar de reclaim
                # move o RSI sozinho, e sem a defasagem o eixo vira o
                # proprio gatilho dito com outro nome.
                "rsi": r14[i0],
                "rsi_lag1": r14[i0 - 1] if i0 else None,
                # Inclinacao do momento nas tres velas anteriores ao gatilho,
                # com sinal a favor da operacao.
                "rsi_slope_lag1": _sig(r14[i0 - 1], r14[i0 - 4]) if i0 >= 4 else None,
                # O momento no FUNDO da visita, e o quanto ele recuperou de
                # la ate o gatilho. Este e o eixo da hipotese.
                "rsi_at_extreme": r14[ext_i],
                "rsi_recovery": _sig(r14[i0], r14[ext_i]),
                # Divergencia contra a visita anterior ao mesmo bloco.
                "rsi_div": rsi_div,
            }
            row.update(_row_outcomes(candles, i0, entry, stop, r, bull=bull))
            # Cada definicao de stop MEDIDA por inteiro, na mesma passada.
            # Antes so o R saia daqui, e escolher entre elas exigia uma rodada
            # por variante -- cinco rodadas do universo inteiro para responder
            # uma pergunta que cabe numa. O resultado depende do stop duas
            # vezes (ele decide se a operacao morre E o tamanho do R que o
            # alvo persegue), entao guardar so o R nao permitia comparar nada.
            row["alts"] = {}
            for name, alt in all_stops.items():
                ra = (entry - alt) if bull else (alt - entry)
                row[f"r_atr_{name}"] = (ra / atr) if ra > 0 else None
                if ra <= 0:
                    continue
                row["alts"][name] = {
                    "r_atr": ra / atr, "r_pct": ra / entry,
                    **_row_outcomes(candles, i0, entry, alt, ra, bull=bull),
                }
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
    print("descartados: " + ", ".join(f"{k} {v}" for k, v in dropped.items()))
    report(rows)


def _net(rs, key, target) -> str:
    if len(rs) < 40:
        return f"n={len(rs):5d} (poucos)"
    hit = sum(1 for r in rs if r[key] >= target - 0.01) / len(rs)
    net = fmean(r[key] - COST_PCT / r["r_pct"] for r in rs)
    tot = sum(r[key] - COST_PCT / r["r_pct"] for r in rs)
    return f"n={len(rs):5d}  acerto {hit:5.1%}  liq {net:+.3f}  total {tot:+8.1f}R"


def report(rows: Sequence[dict], horizon: int = HORIZONS[0]) -> None:
    """Por faixa de `r_atr`, para o teto ser escolhido com o numero na frente.

    Faixas **disjuntas** de proposito: faixas cumulativas escondem que o lucro
    de um teto largo pode vir inteiro do nucleo apertado dentro dele -- foi
    exatamente o que quase passou batido na grade do setup original.
    """
    # Faixas recalibradas em 2026-09-04: com a perna exigida, o gatilho so
    # acontece DEPOIS que o preco deixou o bloco, entao o stop na visita fica
    # de 2 a 6 ATR e a grade antiga (feita para o gate `r_atr<=1` de producao)
    # jogava 100% da amostra num balde so.
    BANDS = ((0.0, 2.0), (2.0, 3.0), (3.0, 4.0), (4.0, 5.0), (5.0, float("inf")))
    INF = float("inf")
    #: Os eixos da visita e as faixas em que saem. Contagens (`prior_visits`,
    #: `sweeps_in_block`) usam faixas de largura 1 para o zero ficar sozinho:
    #: "bloco virgem" contra "bloco ja trabalhado" e a pergunta, e uma faixa
    #: 0-2 responderia as duas juntas.
    GEOMETRY = (
        ("penetration", ((0.0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.0),
                         (1.0, INF))),
        ("visit_candles", ((1, 3), (3, 6), (6, 12), (12, 30), (30, INF))),
        ("gap_to_trigger", ((0, 1), (1, 3), (3, 6), (6, 12), (12, INF))),
        ("prior_visits", ((0, 1), (1, 3), (3, 6), (6, 12), (12, INF))),
        ("sweeps_in_block", ((0, 1), (1, 2), (2, 4), (4, 8), (8, INF))),
        ("block_age", ((0, 30), (30, 80), (80, 200), (200, INF))),
        ("rsi_lag1", ((0, 30), (30, 45), (45, 55), (55, 70), (70, 101))),
        ("rsi_recovery", ((-INF, 0), (0, 5), (5, 10), (10, 20), (20, INF))),
        ("rsi_slope_lag1", ((-INF, -5), (-5, 0), (0, 5), (5, INF))),
    )
    for sample in ("search", "holdout"):
        S = [r for r in rows if r["sample"] == sample]
        real = [r for r in S if r["arm"] != "aleatorio"]
        ctrl = [r for r in S if r["arm"] == "aleatorio"]
        if len(real) < 40:
            continue
        for target in TARGETS:
            tag = str(target).replace(".", "").rstrip("0") or "0"
            key = f"r{tag}_h{horizon}"
            print(f"\n=== {sample} · alvo {target:g}R · h{horizon}")
            print(f"  {'tudo':22s} {_net(real, key, target)}")
            print(f"  {'  controle aleatorio':22s} {_net(ctrl, key, target)}")
            for lo, hi in BANDS:
                lbl = f"r_atr {lo:g}-{hi:g}".replace("-inf", "+")
                print(f"  {lbl:22s} "
                      f"{_net([r for r in real if lo < r['r_atr'] <= hi], key, target)}")
                print(f"  {'  (aleatorio)':22s} "
                      f"{_net([r for r in ctrl if lo < r['r_atr'] <= hi], key, target)}")
            # Os dois cortes que o leitor ainda nao escolheu, com o `n` na
            # frente: qual linha o pavio furou, e qual grau de pinbar.
            for line in ("vwap", "ema", "ambas"):
                sel = [r for r in real if r.get("wick_line") == line]
                print(f"  {'pavio ' + line:22s} {_net(sel, key, target)}")
            grades = sorted({r.get("pinbar_grade") or "" for r in real})
            for grade in grades:
                sel = [r for r in real if (r.get("pinbar_grade") or "") == grade]
                print(f"  {'grau ' + grade:22s} {_net(sel, key, target)}")
            # Cada stop candidato sobre AS MESMAS entradas: a unica comparacao
            # honesta, porque trocar o stop muda o R e o R esta no denominador
            # do custo. `r_atr` medio vai junto -- um stop que ganha por ter o
            # denominador maior nao ganhou nada, so mudou de escala.
            for label, sel in (("com as linhas", [r for r in real if r.get("lines_ok")]),
                               ("sem as linhas", [r for r in real if not r.get("lines_ok")])):
                print(f"  {label:22s} {_net(sel, key, target)}")
            # A geometria da visita, cada eixo em faixas disjuntas. Sao os
            # candidatos a explicar o `r_atr<=2` -- se um deles separar tao
            # bem quanto ele, o setup ganha um criterio que se le no grafico
            # em vez de um numero derivado do stop.
            for field, bands in GEOMETRY:
                for lo, hi in bands:
                    sel = [r for r in real
                           if r.get(field) is not None and lo <= r[field] < hi]
                    lbl = f"{field} {lo:g}-{hi:g}".replace("-inf", "+")
                    print(f"  {lbl:22s} {_net(sel, key, target)}")
            names = sorted({n for r in real for n in (r.get("alts") or {})})
            for name in names:
                sel = [r["alts"][name] for r in real if name in (r.get("alts") or {})]
                if len(sel) < 40:
                    print(f"  {'stop ' + name:22s} n={len(sel):5d} (poucos)")
                    continue
                mean_r = fmean(a["r_atr"] for a in sel)
                print(f"  {'stop ' + name:22s} {_net(sel, key, target)}"
                      f"   r_atr {mean_r:.2f}")


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
    p.add_argument("--only-lines", action="store_true",
                   help="gravar SO as entradas que passam nas regras de linha; "
                        "por padrao elas saem como campo `lines_ok` e o corte "
                        "e feito no relatorio, numa rodada so")
    p.add_argument("--out", default="/tmp/deep_reclaim.json")
    p.add_argument("--report-only", default=None)
    p.add_argument("--horizon", type=int, default=HORIZONS[0], choices=HORIZONS,
                   help="horizonte do relatorio; o JSON grava todos")
    a = p.parse_args()
    if a.report_only:
        report(json.loads(Path(a.report_only).read_text()), a.horizon)
        return
    run(a.symbols, TimeFrame(a.timeframe), a.limit, a.out,
        gap=a.gap, require_ema9=not a.no_ema9, min_vwap=a.min_vwap,
        require_lines=a.only_lines,
        pinbar_color=None if a.no_color else "l2",
        drop_pierced=not a.keep_pierced)


if __name__ == "__main__":
    main()
