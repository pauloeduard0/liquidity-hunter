"""Etapa 7.1 -- a profundidade do pullback e um sinal de SMC, ou geometria de stop?

A Etapa 7.0 rejeitou a VWAP como filtro de entrada e, ao fazer isso, tropecou
num achado que nao estava procurando: **`pullback_atr` foi a unica feature do
painel com separacao real** (AUC 0,68-0,73 contra "bateu 2R", nos quatro
timeframes, contra 0,46-0,55 de tudo que era VWAP), e o corte pela mediana foi
o unico subconjunto que ficou liquido positivo em H4 (+0,236R, PF 1,38) e D1
(+0,229R, PF 1,38).

Aquilo **nao e um resultado ainda**, e o motivo esta na propria 7.0: a selecao
foi congelada no eixo VWAP, entao o corte de profundidade foi lido no painel
inteiro, discovery e holdout juntos. Um numero escolhido depois de ver os dados
que ele explica nao tem holdout -- tem memoria. Esta etapa existe para dar a
ele o holdout que faltou, e para submete-lo ao controle que a 7.0 mostrou ser
decisivo.

--------------------------------------------------------------------------
1. A POPULACAO E A MESMA, IMPORTADA E NAO REDEFINIDA
--------------------------------------------------------------------------

Setup, entrada, stop, alvo, horizonte, custo e politica de empate vem de
`research/smc_vwap_entry_quality.py`, por import. Nada e recopiado: se as duas
etapas divergissem numa virgula, a 7.1 estaria medindo outra coisa e chamando
de "a mesma populacao". `test_a_populacao_e_a_da_7_0_e_nao_uma_copia` guarda
isso comparando as regras termo a termo.

As tres familias continuam separadas (`bos_retest`, `choch_retest`,
`sweep_retest`), porque o sinal pode existir so em algumas.

--------------------------------------------------------------------------
2. AS FEATURES DE PROFUNDIDADE, E UMA IDENTIDADE QUE PRECISA SER DITA
--------------------------------------------------------------------------

| feature | formula | o que mede |
|---|---|---|
| `pullback_atr` | `(extremo - entrada) / unidade` | profundidade ABSOLUTA, em ATR |
| `retrace_pct` | `100 x (extremo - entrada) / (extremo - nivel)` | profundidade / impulso |
| `pullback_over_leg` | `(extremo - entrada) / (extremo - origem)` | profundidade / perna |

onde `unidade = ATR% x preco de entrada`, `nivel` e a referencia rompida e
`origem` e o `origin_price_level` do evento.

`impulse_atr` = `(extremo - nivel) / unidade`, e dai sai a identidade que
importa nao esquecer:

> `retrace_pct = 100 x pullback_atr / impulse_atr`

Ou seja, "pullback dividido pelo impulso" **nao e uma feature nova** -- e o
`retrace_pct` com outro nome. Testar as duas como se fossem candidatos
independentes seria contar a mesma aposta duas vezes na correcao por multiplas
comparacoes. Fica registrado, e ha teste.

--------------------------------------------------------------------------
3. O CONTROLE QUE DECIDE: LARGURA DE STOP
--------------------------------------------------------------------------

A 7.0 terminou mostrando que ~80% do unico "ganho" de VWAP era o termo de
custo: o recorte selecionava stops mais largos, e um stop largo paga menos
taxa por unidade de risco. **A profundidade e suspeita da mesma coisa, e por
um mecanismo mais direto ainda**: um pullback profundo entra mais perto do
nivel, e onde o stop fica depende dessa geometria.

Entao todo numero desta etapa aparece em tres versoes:

- **bruto** (`gross_r`), sem custo nenhum;
- **liquido** (`net_r`), com o round trip de taxa convertido para R;
- **`cost_r`**, o termo de custo isolado, reportado por grupo.

E o teste principal e estratificado: **dentro de cada quintil de `r_atr`**
(largura do stop), a profundidade ainda separa? Se nao separar, o efeito e
geometria de stop e custo, e a resposta e nao.

--------------------------------------------------------------------------
4. O CRITERIO DE SELECAO, CORRIGIDO
--------------------------------------------------------------------------

A 7.0 escolheu por `delta_per_day` e isso nao discriminou: numa populacao de
expectativa negativa, **qualquer** filtro que corte trades sobe a conta por dia
por subtracao. "Perder menos por dia" nao e edge. A hierarquia aqui e outra, e
nesta ordem:

1. expectativa **liquida positiva** (nao "menos negativa");
2. profit factor > 1;
3. amostra minima;
4. estabilidade discovery -> holdout;
5. so entao cobertura e frequencia.

E a preferencia estrutural: **relacao monotonica ou plato**, e nao um balde
isolado bonito. Um balde solto no meio da distribuicao e o formato que o acaso
produz; uma rampa e o formato que um mecanismo produz.

Uso:

    poetry run python -m research.smc_pullback_depth --expanded
    poetry run python -m research.smc_pullback_depth --symbols BTCUSDT --cases
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from liquidity_hunter.core.domain import MarketDirection, TimeFrame
from research.choch_leg_opener import TIMEFRAMES, WINDOWS
from research.current_market_pressure import auc, quantile
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, load_series
from research.smc_vwap_entry_quality import (
    FAMILIES,
    ROUND_TRIP,
    TARGETS,
    Trade,
    collect_combo,
    cut_by_tf,
    net_r,
    span_days,
)

BULL = MarketDirection.BULLISH
BEAR = MarketDirection.BEARISH

#: Os quantis testados no discovery (secao 5). Cinco por feature, tres
#: features: quinze candidatos, e o numero e reportado.
QUANTILES = (0.30, 0.40, 0.50, 0.60, 0.70)

#: As tres leituras de profundidade. `pullback_over_leg` e derivada e so entra
#: onde o detector publicou origem da perna.
DEPTH_FEATURES = ("pullback_atr", "retrace_pct", "pullback_over_leg")

#: Quintis para a tabela de baldes (secao 22).
BUCKETS = 5

#: Decis, e nao quintis, para o controle de largura de stop. A diferenca nao
#: e cosmetica: estratificar uma variavel CONTINUA em faixas largas so absorve
#: parte de um confundidor que anda junto com ela -- dentro de uma faixa larga
#: sobra variacao suficiente para o efeito confundido reaparecer. Faixas mais
#: estreitas absorvem mais. Mesmo assim **a absorcao nunca e total**, e essa
#: limitacao fica declarada: um delta residual pequeno depois do controle nao
#: prova sinal proprio, so prova que o controle nao explicou tudo.
STOP_BUCKETS = 10

#: Piso de amostra para publicar um grupo.
MIN_N = 150
#: Piso mais baixo para recortes ja estratificados, onde a amostra se divide.
MIN_STRATUM = 40

#: Blocos temporais da secao 20.
TIME_BLOCKS = 4

CASES = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def depth_of(trade: Trade, name: str) -> float:
    """A leitura de profundidade pedida, ou `nan` onde ela nao existe."""
    if name == "pullback_over_leg":
        if math.isnan(trade.leg_atr) or trade.leg_atr <= 0:
            return float("nan")
        return trade.pullback_atr / trade.leg_atr
    return float(getattr(trade, name))


def cost_r(trade: Trade) -> float:
    """O termo de custo isolado, em R. `ROUND_TRIP / r_pct`."""
    return ROUND_TRIP / trade.r_pct


# --------------------------------------------------------------------------
# estatistica de grupo -- bruto, liquido e custo, sempre os tres
# --------------------------------------------------------------------------


def stats(trades: Sequence[Trade], pool: Sequence[Trade] | None = None) -> dict[str, Any]:
    """Tudo que a secao 14 pede, para um grupo.

    Os tres numeros de dinheiro andam juntos de proposito: a 7.0 mostrou que
    um liquido que melhora com um bruto que nao melhora e um resultado sobre
    corretagem, nao sobre mercado.
    """
    if not trades:
        return {"n": 0}
    gross = [t.r_grid.get(2.0, 0.0) for t in trades]
    net = [net_r(t, 2.0) for t in trades]
    costs = [cost_r(t) for t in trades]
    wins = sum(r for r in net if r > 0)
    losses = -sum(r for r in net if r < 0)
    gross_wins = sum(r for r in gross if r > 0)
    gross_losses = -sum(r for r in gross if r < 0)
    out: dict[str, Any] = {
        "n": len(trades),
        "coverage": len(trades) / len(pool) if pool else 1.0,
        "gross_r": statistics.fmean(gross),
        "net_r": statistics.fmean(net),
        "cost_r": statistics.fmean(costs),
        # A media do custo e sensivel a cauda: um unico stop microscopico
        # (r_pct minusculo) vale varios R de taxa e desloca o balde inteiro.
        # A mediana fica ao lado para que "o custo subiu" nunca seja lido de
        # um outlier.
        "cost_r_median": quantile(sorted(costs), 0.5),
        "net_r_median": quantile(sorted(net), 0.5),
        "pf": wins / losses if losses > 0 else None,
        "pf_gross": gross_wins / gross_losses if gross_losses > 0 else None,
        "hit1": statistics.fmean(t.hit.get(1.0, 0.0) for t in trades),
        "hit2": statistics.fmean(t.hit.get(2.0, 0.0) for t in trades),
        "hit3": statistics.fmean(t.hit.get(3.0, 0.0) for t in trades),
        "stop_rate": statistics.fmean(t.stopped_first for t in trades),
        "mfe": statistics.fmean(t.mfe_r for t in trades if not math.isnan(t.mfe_r)),
        "mae": statistics.fmean(t.mae_r for t in trades if not math.isnan(t.mae_r)),
        "r_atr": statistics.fmean(t.r_atr for t in trades),
        "depth": statistics.fmean(t.pullback_atr for t in trades),
    }
    # Expectativa continua nos tres alvos (secao 21): um filtro pode subir a
    # taxa de 2R e piorar o payoff, e so olhar hit2 nao veria isso.
    for k in TARGETS:
        out[f"gross_{k:g}r"] = statistics.fmean(t.r_grid.get(k, 0.0) for t in trades)
    days = span_days(pool) if pool else span_days(trades)
    out["net_per_day"] = sum(net) / days if days > 0 else None
    out["trades_per_day"] = len(trades) / days if days > 0 else None
    return out


def buckets_of(
    trades: Sequence[Trade], feature: str, count: int = BUCKETS
) -> list[tuple[float, float, list[Trade]]]:
    """Quantis da feature, com os trades de cada balde."""
    values = sorted(v for t in trades if not math.isnan(v := depth_of(t, feature)))
    if len(values) < count * 10:
        return []
    edges = [quantile(values, q / count) for q in range(1, count)]
    out: list[tuple[float, float, list[Trade]]] = []
    bounds = [-math.inf, *edges, math.inf]
    for index in range(count):
        low, high = bounds[index], bounds[index + 1]
        rows = [
            t
            for t in trades
            if not math.isnan(v := depth_of(t, feature)) and low < v <= high
        ]
        out.append((low, high, rows))
    return out


def monotonic_score(values: Sequence[float]) -> float:
    """Fracao dos passos que sobem. 1,0 = rampa perfeita, 0,5 = ruido.

    Nao e um teste de significancia e nao pretende ser: e a distincao entre "a
    profundidade tem uma direcao" e "um balde do meio ficou bonito", que e a
    diferenca entre um mecanismo e um sorteio.
    """
    steps = list(zip(values, values[1:], strict=False))
    if not steps:
        return float("nan")
    return sum(1 for a, b in steps if b > a) / len(steps)


# --------------------------------------------------------------------------
# o controle que decide: profundidade DENTRO de cada largura de stop
# --------------------------------------------------------------------------


def stop_matched(
    pool: Sequence[Trade], feature: str, threshold: float
) -> dict[str, Any]:
    """Profundo x raso, comparado so DENTRO de cada quintil de `r_atr`.

    A pergunta da secao 9: entre trades cujo stop tem a mesma largura, a
    profundidade ainda separa? Cada quintil devolve o seu proprio delta, e o
    delta agregado e a media ponderada pelo numero de trades profundos do
    quintil -- que e o peso com que aquele estrato aparece na regra.

    O bruto entra junto porque e ele que responde se sobrou mecanismo: um
    delta liquido positivo com delta bruto zero e uma economia de corretagem.
    """
    widths = sorted(t.r_atr for t in pool if not math.isnan(t.r_atr))
    if len(widths) < STOP_BUCKETS * 10:
        return {}
    edges = [quantile(widths, q / STOP_BUCKETS) for q in range(1, STOP_BUCKETS)]
    bounds = [-math.inf, *edges, math.inf]

    strata: list[dict[str, Any]] = []
    weighted_net = 0.0
    weighted_gross = 0.0
    total = 0
    for index in range(STOP_BUCKETS):
        low, high = bounds[index], bounds[index + 1]
        rows = [t for t in pool if low < t.r_atr <= high]
        deep = [t for t in rows if depth_of(t, feature) >= threshold]
        shallow = [t for t in rows if depth_of(t, feature) < threshold]
        if len(deep) < MIN_STRATUM or len(shallow) < MIN_STRATUM:
            strata.append({"r_atr_lo": low, "r_atr_hi": high, "n_deep": len(deep)})
            continue
        a, b = stats(deep), stats(shallow)
        strata.append({
            "r_atr_lo": low,
            "r_atr_hi": high,
            "n_deep": len(deep),
            "n_shallow": len(shallow),
            "net_deep": a["net_r"],
            "net_shallow": b["net_r"],
            "delta_net": a["net_r"] - b["net_r"],
            "gross_deep": a["gross_r"],
            "gross_shallow": b["gross_r"],
            "delta_gross": a["gross_r"] - b["gross_r"],
        })
        weighted_net += len(deep) * (a["net_r"] - b["net_r"])
        weighted_gross += len(deep) * (a["gross_r"] - b["gross_r"])
        total += len(deep)
    return {
        "strata": strata,
        "n": total,
        "delta_net": weighted_net / total if total else None,
        "delta_gross": weighted_gross / total if total else None,
    }


# --------------------------------------------------------------------------
# recortes de tempo, simbolo e contexto
# --------------------------------------------------------------------------


def cuts_of(trades: Sequence[Trade]) -> dict[TimeFrame, int]:
    return {tf: cut_by_tf(trades, tf) for tf in TIMEFRAMES}


def sample_of(trade: Trade, cuts: dict[TimeFrame, int]) -> str:
    return "discovery" if trade.ts < cuts[trade.timeframe] else "holdout"


def by_symbol(trades: Sequence[Trade]) -> dict[str, Any]:
    """Secao 19: o edge esta espalhado ou mora em cinco simbolos?"""
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for trade in trades:
        totals[trade.symbol] = totals.get(trade.symbol, 0.0) + net_r(trade)
        counts[trade.symbol] = counts.get(trade.symbol, 0) + 1
    if not totals:
        return {}
    means = {s: totals[s] / counts[s] for s in totals}
    positive = sum(1 for v in means.values() if v > 0)
    ordered = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    total = sum(totals.values())
    top5 = sum(value for _s, value in ordered[:5])
    return {
        "symbols": len(totals),
        "positive": positive,
        "negative": len(totals) - positive,
        "median_per_trade": quantile(sorted(means.values()), 0.5),
        "total_net": total,
        "top5_share": top5 / total if total > 0 else None,
        "top5": [[s, round(v, 2)] for s, v in ordered[:5]],
    }


def by_time_block(trades: Sequence[Trade], blocks: int = TIME_BLOCKS) -> list[dict]:
    """Secao 20: um regime so, ou varios periodos?"""
    stamps = sorted(t.ts for t in trades)
    if len(stamps) < blocks * MIN_STRATUM:
        return []
    edges = [quantile([float(s) for s in stamps], q / blocks) for q in range(1, blocks)]
    bounds = [-math.inf, *edges, math.inf]
    out = []
    for index in range(blocks):
        low, high = bounds[index], bounds[index + 1]
        rows = [t for t in trades if low < t.ts <= high]
        got = stats(rows)
        got["block"] = index
        out.append(got)
    return out


# --------------------------------------------------------------------------
# construcao
# --------------------------------------------------------------------------


def collect(symbols: Sequence[str], windows: int) -> list[Trade]:
    trades: list[Trade] = []
    for position, symbol in enumerate(symbols, 1):
        print(f"[{position}/{len(symbols)}] {symbol}  trades={len(trades)}", flush=True)
        for timeframe in TIMEFRAMES:
            if not (CACHE_DIR / f"{symbol}_{timeframe.value}.json").exists():
                continue
            try:
                series = load_series(symbol, timeframe)
            except Exception as error:  # noqa: BLE001 - cache com vela corrompida
                print(f"! cache invalido: {symbol} {timeframe.value}: {error}")
                continue
            for window in range(windows):
                end = len(series) - window * LIMIT
                if end - LIMIT - BUFFER < 0:
                    break
                try:
                    trades += collect_combo(symbol, timeframe, series, end)
                except Exception as error:  # noqa: BLE001 - feed morto
                    print(f"! janela invalida: {symbol} {timeframe.value} w{window}: {error}")
                    continue
    return trades


def candidate_rules(pool: Sequence[Trade]) -> list[dict[str, Any]]:
    """Os quantis do discovery, e so eles (secao 4).

    O limiar sai de um quantil da distribuicao do DISCOVERY. Ler o quantil do
    painel inteiro seria escolher o numero depois de ver os dados que ele
    explica -- exatamente o que faltou na 7.0.
    """
    out = []
    for feature in DEPTH_FEATURES:
        values = sorted(v for t in pool if not math.isnan(v := depth_of(t, feature)))
        if len(values) < MIN_N * 2:
            continue
        for q in QUANTILES:
            threshold = quantile(values, q)
            fired = [t for t in pool if depth_of(t, feature) >= threshold]
            if len(fired) < MIN_N:
                continue
            got = stats(fired, pool)
            out.append({
                "feature": feature,
                "quantile": q,
                "threshold": threshold,
                **got,
            })
    return out


def choose(candidates: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """A hierarquia da secao 6, aplicada na ordem declarada.

    Positivo em LIQUIDO primeiro -- "menos negativo" nao entra na peneira. Se
    nada for positivo a etapa ja tem a sua resposta, e nenhum criterio
    secundario deve ser usado para salvar um candidato.
    """
    viable = [
        c
        for c in candidates
        if c.get("n", 0) >= MIN_N and c.get("net_r", -1) > 0 and (c.get("pf") or 0) > 1.0
    ]
    if not viable:
        return None
    return max(viable, key=lambda c: (c["net_r"], c["coverage"]))


def build(symbols: Sequence[str], windows: int, want_cases: bool) -> dict[str, Any]:
    trades = collect(symbols, windows)
    print(f"trades: {len(trades)}")
    cuts = cuts_of(trades)
    report: dict[str, Any] = {
        "trades": len(trades),
        "round_trip": ROUND_TRIP,
        "quantiles": list(QUANTILES),
        "families": {},
        "rules_tested": 0,
    }

    for family in FAMILIES:
        pool = [t for t in trades if t.family == family]
        if not pool:
            continue
        block: dict[str, Any] = {"baseline": stats(pool)}
        block["auc"] = {
            name: (
                got[0]
                if (
                    got := auc(
                        [
                            depth_of(t, name)
                            for t in pool
                            if not math.isnan(depth_of(t, name))
                        ],
                        [
                            int(t.hit.get(2.0, 0.0) > 0)
                            for t in pool
                            if not math.isnan(depth_of(t, name))
                        ],
                    )
                )
                else None
            )
            for name in DEPTH_FEATURES
        }
        block["buckets"] = {}
        for name in DEPTH_FEATURES:
            rows = []
            for low, high, group in buckets_of(pool, name):
                got = stats(group, pool)
                got["lo"] = None if low == -math.inf else low
                got["hi"] = None if high == math.inf else high
                rows.append(got)
            if rows:
                block["buckets"][name] = {
                    "rows": rows,
                    "monotonic_net": monotonic_score([r["net_r"] for r in rows]),
                    "monotonic_gross": monotonic_score([r["gross_r"] for r in rows]),
                    "monotonic_hit2": monotonic_score([r["hit2"] for r in rows]),
                }
        block["by_tf"] = {}
        for timeframe in TIMEFRAMES:
            sub = [t for t in pool if t.timeframe is timeframe]
            if len(sub) < MIN_N:
                continue
            rows = [
                {**stats(group, sub), "lo": None if low == -math.inf else low}
                for low, high, group in buckets_of(sub, "pullback_atr")
            ]
            block["by_tf"][timeframe.value] = {
                "baseline": stats(sub),
                "buckets": rows,
                "monotonic_net": monotonic_score([r["net_r"] for r in rows]) if rows else None,
            }
        report["families"][family] = block

    # ---- a regra, so no discovery, e so entao o holdout -------------------
    main = [t for t in trades if t.family == "bos_retest"]
    discovery = [t for t in main if sample_of(t, cuts) == "discovery"]
    holdout = [t for t in main if sample_of(t, cuts) == "holdout"]
    candidates = candidate_rules(discovery)
    report["rules_tested"] = len(candidates)
    report["discovery_grid"] = candidates
    report["discovery_baseline"] = stats(discovery)
    chosen = choose(candidates)
    report["chosen"] = chosen

    # O melhor candidato pelo criterio ANTIGO (menos negativo), so para deixar
    # explicito o que a hierarquia corrigida descarta.
    if candidates:
        report["best_if_less_negative"] = max(candidates, key=lambda c: c["net_r"])

    target = chosen or report.get("best_if_less_negative")
    if target:
        feature, threshold = target["feature"], target["threshold"]
        fired_h = [t for t in holdout if depth_of(t, feature) >= threshold]
        report["holdout"] = {
            "baseline": stats(holdout),
            "rule": stats(fired_h, holdout),
            "confirmed": bool(chosen) and stats(fired_h, holdout).get("net_r", -1) > 0,
        }
        # o controle que decide, no painel inteiro da familia
        report["stop_matched"] = stop_matched(main, feature, threshold)
        fired_all = [t for t in main if depth_of(t, feature) >= threshold]
        report["robustness"] = {
            "by_symbol": by_symbol(fired_all),
            "by_block": by_time_block(fired_all),
        }
        report["context"] = {}
        for label, rows in (
            ("1o teste", [t for t in fired_all if t.test_ordinal == 1]),
            ("revisita", [t for t in fired_all if t.test_ordinal > 1]),
            ("long", [t for t in fired_all if t.direction is BULL]),
            ("short", [t for t in fired_all if t.direction is BEAR]),
        ):
            report["context"][label] = stats(rows, fired_all)
        # Secao 16: so o CHoCH classifica a sua referencia. O `feature`
        # escolhido pode nem existir nessa familia (`pullback_over_leg` exige
        # `origin_price_level`, que o CHoCH nao publica), entao o recorte usa
        # a leitura que existe la -- `pullback_atr` no seu proprio p70 de
        # discovery. Herdar um limiar de outra familia seria transplantar um
        # numero, e devolver um bloco vazio seria esconder a pergunta.
        choch = [t for t in trades if t.family == "choch_retest"]
        choch_disc = [t for t in choch if sample_of(t, cuts) == "discovery"]
        values = sorted(
            v for t in choch_disc if not math.isnan(v := depth_of(t, "pullback_atr"))
        )
        if len(values) >= MIN_N * 2:
            edge = quantile(values, 0.70)
            fired_c = [t for t in choch if depth_of(t, "pullback_atr") >= edge]
            report["context"]["choch_ref_threshold"] = edge
            for label, flag in (("ref forte", 1.0), ("ref fraca", 0.0)):
                rows = [t for t in fired_c if t.reference_structural == flag]
                report["context"][label] = stats(rows, fired_c)

    if want_cases:
        report["cases"] = pick_cases(trades)
    return report


def pick_cases(trades: Sequence[Trade]) -> list[dict[str, Any]]:
    """Quatro trades para conferir a feature no olho (secao 23).

    Sanity check de calculo, nunca de decisao: os quatro sao escolhidos DEPOIS
    de tudo estar medido, e nenhum limiar desta etapa os viu.
    """
    pool = [t for t in trades if t.symbol in CASES and t.family == "bos_retest"]
    if not pool:
        return []
    depths = sorted(t.pullback_atr for t in pool if not math.isnan(t.pullback_atr))
    if len(depths) < 20:
        return []
    low = quantile(depths, 0.2)
    high = quantile(depths, 0.8)
    wanted = {
        "raso perdedor": lambda t: t.pullback_atr <= low and t.r_grid.get(2.0) == -1.0,
        "raso vencedor": lambda t: t.pullback_atr <= low and t.hit.get(2.0),
        "profundo perdedor": lambda t: t.pullback_atr >= high and t.r_grid.get(2.0) == -1.0,
        "profundo vencedor": lambda t: t.pullback_atr >= high and t.hit.get(2.0),
    }
    out = []
    for label, predicate in wanted.items():
        found = next((t for t in pool if predicate(t)), None)
        if found is None:
            continue
        out.append({
            "caso": label,
            "symbol": found.symbol,
            "timeframe": found.timeframe.value,
            "direction": found.direction.value,
            "entry": found.entry,
            "stop": found.stop,
            "pullback_atr": found.pullback_atr,
            "impulse_atr": found.impulse_atr,
            "retrace_pct": found.retrace_pct,
            "r_atr": found.r_atr,
            "cost_r": cost_r(found),
            "gross_2r": found.r_grid.get(2.0),
            "net_2r": net_r(found),
        })
    return out


# --------------------------------------------------------------------------
# saida
# --------------------------------------------------------------------------


def num(value: float | None, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "  --  "
    return f"{value:.{digits}f}"


def pct(value: float | None) -> str:
    return "  --  " if value is None else f"{100 * value:5.1f}%"


def row(label: str, got: dict[str, Any]) -> str:
    if got.get("n", 0) == 0 or "net_r" not in got:
        return f"  {label:<26} n={got.get('n', 0):>5}  (abaixo do piso)"
    return (
        f"  {label:<26} n={got['n']:>5} cov {pct(got.get('coverage'))} "
        f"hit2 {pct(got['hit2'])} bruto {num(got['gross_r']):>7} "
        f"liq {num(got['net_r']):>7} (med {num(got.get('net_r_median')):>7}) "
        f"custo {num(got['cost_r']):>6}/{num(got.get('cost_r_median'), 2):>5} "
        f"PF {num(got['pf'], 2):>6}/{num(got['pf_gross'], 2):>6} "
        f"MFE {num(got['mfe'], 2)} MAE {num(got['mae'], 2)} "
        f"r_atr {num(got['r_atr'], 2)} prof {num(got['depth'], 2)}"
    )


def print_report(report: dict[str, Any]) -> None:
    print()
    print("=" * 140)
    print("ETAPA 7.1 -- SMC PULLBACK DEPTH")
    print("=" * 140)
    print(f"trades {report['trades']}  round trip {report['round_trip']:.4f}")
    print("  (PF liquido/bruto)")

    for family, block in report["families"].items():
        print()
        print(f"--- familia {family} ---")
        print(row("baseline", block["baseline"]))
        print("  AUC contra 'bateu 2R': " + "  ".join(
            f"{name} {num(value)}" for name, value in block["auc"].items()
        ))
        for name, got in block["buckets"].items():
            print(f"  -- quintis de {name} "
                  f"(monotonia liq {num(got['monotonic_net'], 2)} / "
                  f"bruto {num(got['monotonic_gross'], 2)} / "
                  f"hit2 {num(got['monotonic_hit2'], 2)}) --")
            for index, entry in enumerate(got["rows"]):
                edge = "  -inf" if entry["lo"] is None else num(entry["lo"], 2)
                print(row(f"Q{index + 1} (>{edge})", entry))
        for tf, entry in block.get("by_tf", {}).items():
            print(f"  -- {tf}: monotonia liq {num(entry['monotonic_net'], 2)} --")
            for index, got in enumerate(entry["buckets"]):
                print(row(f"    {tf} Q{index + 1}", got))

    print()
    print(f"--- regra ({report['rules_tested']} candidatos, quantis SO do discovery) ---")
    print(row("discovery baseline", report.get("discovery_baseline", {})))
    for got in report.get("discovery_grid", []):
        print(row(f"{got['feature']} p{int(got['quantile'] * 100)} "
                  f">={num(got['threshold'], 2)}", got))

    chosen = report.get("chosen")
    if chosen is None:
        print("\n  NENHUM candidato tem expectativa LIQUIDA positiva com PF > 1.")
        fallback = report.get("best_if_less_negative")
        if fallback:
            print(f"  (o melhor pelo criterio antigo -- 'menos negativo' -- seria "
                  f"{fallback['feature']} p{int(fallback['quantile'] * 100)}, "
                  f"liq {num(fallback['net_r'])}, PF {num(fallback['pf'], 2)}; "
                  f"a hierarquia da secao 6 o descarta.)")
    else:
        print(f"\n  ESCOLHIDA: {chosen['feature']} "
              f"p{int(chosen['quantile'] * 100)} >= {num(chosen['threshold'], 2)}")

    hold = report.get("holdout")
    if hold:
        print()
        print("--- holdout ---")
        print(row("baseline", hold["baseline"]))
        print(row("regra", hold["rule"]))
        print(f"  confirma: {hold['confirmed']}")

    matched = report.get("stop_matched")
    if matched and matched.get("strata"):
        print()
        print("--- controle: profundidade DENTRO de cada quintil de r_atr ---")
        for entry in matched["strata"]:
            if "delta_net" not in entry:
                print(f"  r_atr ({num(entry['r_atr_lo'], 2)}, {num(entry['r_atr_hi'], 2)}]"
                      f"  amostra insuficiente (n_deep={entry['n_deep']})")
                continue
            print(f"  r_atr ({num(entry['r_atr_lo'], 2)}, {num(entry['r_atr_hi'], 2)}]"
                  f"  n {entry['n_deep']}/{entry['n_shallow']}  "
                  f"delta bruto {num(entry['delta_gross']):>7}  "
                  f"delta liq {num(entry['delta_net']):>7}")
        print(f"  AGREGADO (peso = trades profundos): "
              f"delta bruto {num(matched['delta_gross'])}  "
              f"delta liquido {num(matched['delta_net'])}")

    robust = report.get("robustness", {})
    sym = robust.get("by_symbol")
    if sym:
        print()
        print("--- robustez por simbolo ---")
        print(f"  {sym['positive']} positivos / {sym['negative']} negativos de "
              f"{sym['symbols']}  mediana {num(sym['median_per_trade'])}  "
              f"top5 = {pct(sym['top5_share'])} do PnL  {sym['top5']}")
    for entry in robust.get("by_block", []):
        print(row(f"bloco temporal {entry['block'] + 1}", entry))

    context = report.get("context")
    if context:
        print()
        print("--- contexto ---")
        for label, got in context.items():
            if not isinstance(got, dict):
                print(f"  {label}: {num(got, 2)}")
                continue
            print(row(label, got))

    cases = report.get("cases")
    if cases:
        print()
        print("--- casos (conferencia de calculo, nao de decisao) ---")
        for case in cases:
            print(f"  {case['caso']:<20} {case['symbol']} {case['timeframe']} "
                  f"{case['direction']:<7} prof {case['pullback_atr']:.2f} ATR "
                  f"(impulso {case['impulse_atr']:.2f}, retrace {case['retrace_pct']:.0f}%) "
                  f"r_atr {case['r_atr']:.2f} custo {case['cost_r']:.2f}R "
                  f"-> bruto {case['gross_2r']:+.2f} liq {case['net_2r']:+.2f}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--expanded", action="store_true")
    parser.add_argument("--windows", type=int, default=WINDOWS)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--cases", action="store_true")
    args = parser.parse_args(argv)

    if args.symbols:
        symbols = list(args.symbols)
    elif args.expanded:
        from research.choch_leg_opener import expanded_symbols

        symbols = expanded_symbols()
    else:
        symbols = list(CASES)

    report = build(symbols, args.windows, args.cases)
    print_report(report)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, default=str))
        print(f"\njson: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
