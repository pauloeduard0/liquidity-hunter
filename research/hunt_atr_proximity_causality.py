"""H4.0: a proximidade ATR do HUNT olha para candles futuros?

A cadeia, lida antes de medir
-----------------------------
`_effective_proximity` tem **um unico caller** em todo o pacote:

    LiquidityHuntEngine.build()            liquidity_hunt.py:269
      -> _effective_proximity(data.candles)          :222
      -> _zone_targets(..., proximity, ...)          :281 -> :1173
      -> _band_targets(..., proximity, ...)          :284 -> :1234
      -> LiquidityHuntState.targets / phase

`build_history` e `build_continuation_history` **nao aparecem nessa cadeia**:
elas nunca calculam proximidade e nunca montam targets. O caminho de pool delas
e outro -- `_collect_capture_signals` monta `pool_levels` com todas as zonas do
tipo cacado, sem nenhum filtro de distancia, e `_raid_signals` consome isso.

Disso decorre uma conclusao que muda o enunciado desta etapa: **nao existem
"pools historicas consideradas proximas"**, porque a reconstrucao historica nao
considera proximidade. O modulo nao afirma isso -- ele **demonstra**
(`history_invariance`), rodando a producao com `proximity_atr` em None, 0.5, 2.0
e 100.0 e conferindo que os dois streams historicos saem identicos byte a byte.

O que sobra, e que e mediivel
-----------------------------
A formula media o true range percentual sobre a **janela visivel inteira**:

    mean_tr_pct = mean( max(h-l, |h-c_prev|, |l-c_prev|) / c  para cada par )
    proximity   = proximity_atr * mean_tr_pct

No instante vivo, "janela inteira" e "tudo ate agora": todo candle e passado, e
a leitura e **causal**. O que ela nao e e **estavel** -- o valor depende de
quantos candles o caller pediu e muda a cada vela nova, entao o mesmo instante
pode ser reclassificado depois. Isso e repaint, nao lookahead, e as duas coisas
tem consequencias diferentes.

Ainda assim o experimento do enunciado tem sentido, e e este: **se** alguem
reconstruisse um instante passado T usando a proximidade do snapshot atual --
que e exatamente o que uma pesquisa ou um replay ingenuo fariam -- quanto erro
isso introduziria? Duas variantes sobre os mesmos candles:

    LEGACY   mean_tr_pct sobre a janela visivel inteira (o valor da producao)
    CAUSAL   mean_tr_pct sobre o prefixo ate T, mesma formula, so o corte muda

A formula nao e trocada. Nada de Wilder, nada de EMA, nada de outro periodo: a
unica variavel do experimento e janela completa contra prefixo.

Run:
    poetry run python -m research.hunt_atr_proximity_causality \\
        --out research/hunt_atr_proximity_causality_baseline.json
    poetry run python -m research.hunt_atr_proximity_causality \\
        --report-only research/hunt_atr_proximity_causality_baseline.json
    poetry run python -m research.hunt_atr_proximity_causality --case BTCUSDT:M15
    poetry run python -m research.hunt_atr_proximity_causality --chain
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import fmean
from typing import Any

from liquidity_hunter.app.dashboard_data import (
    _HIGHER_TIMEFRAME_MAP,
    _HUNT_PROXIMITY_ATR,
    load_dashboard_data,
)
from liquidity_hunter.app.liquidity_hunt import LiquidityHuntEngine
from liquidity_hunter.core.domain import Candle, LiquidityZoneType
from research.hunt_htf_causality import TFS, NoFuturesProvider, WindowProvider, fetch_series
from research.hunt_score_redundancy import VISIBLE_LIMIT, WINDOW_STEP, WINDOWS

#: Quantos instantes T avaliar por janela, e de quantos em quantos candles. Um T
#: a cada 10 candles cobre a janela sem transformar o painel numa varredura
#: O(N^2) que nao acrescentaria resolucao nenhuma: o `mean_tr_pct` e uma media
#: sobre centenas de candles e nao muda de forma perceptivel entre vizinhos.
EVAL_STEP = 10
EVAL_TAIL = 300

#: Os limiares de diferenca relativa pedidos no item 9.
DIFF_BANDS = (0.01, 0.05, 0.10)


# ---------------------------------------------------------------------------
# 1. As duas leituras da mesma formula
# ---------------------------------------------------------------------------


def legacy_proximity(candles: list[Candle], proximity_atr: float) -> float:
    """A proximidade da producao, sobre a janela visivel inteira.

    Delegada ao proprio motor em vez de reescrita: se a formula mudar em
    producao, esta pesquisa muda junto, e uma reimplementacao teria comecado a
    medir outra coisa em silencio.
    """
    return LiquidityHuntEngine(proximity_atr=proximity_atr)._effective_proximity(candles)


def causal_proximity(
    candles: list[Candle], at: datetime, proximity_atr: float
) -> float:
    """A mesma formula, restrita ao prefixo que existia em ``at``.

    O corte e inclusivo: o candle de ``at`` ja fechou quando a leitura e feita
    (a producao le sobre candles fechados), entao ele pertence ao passado de
    quem observa. Excluir esse candle seria uma segunda mudanca de regra, e o
    item 15 pede exatamente uma.
    """
    prefix = [c for c in candles if c.timestamp <= at]
    return LiquidityHuntEngine(proximity_atr=proximity_atr)._effective_proximity(prefix)


def eligible_targets(
    zones: list[Any], price: float, proximity: float, hunted_short: bool, at: datetime
) -> list[tuple[float, str]]:
    """Os pools que passariam o filtro de distancia de `_zone_targets`.

    Reproduz so o teste de elegibilidade (`abs(mid - price) / price > proximity`)
    sobre as zonas que **ja existiam** em ``at``. Nao reproduz o resto de
    `_zone_targets` (captura, confirmacao, ordenacao por captura) porque essas
    partes nao dependem de proximidade, e inclui-las adicionaria variaveis a um
    experimento que tem de ter uma so.
    """
    zone_type = (
        LiquidityZoneType.EQUAL_HIGHS if hunted_short else LiquidityZoneType.EQUAL_LOWS
    )
    out = []
    for zone in zones:
        if zone.zone_type is not zone_type or zone.formed_at > at:
            continue
        mid = (zone.price_low + zone.price_high) / 2
        if mid <= 0 or price <= 0:
            continue
        if abs(mid - price) / price > proximity:
            continue
        out.append((mid, zone.formed_at.isoformat()))
    return sorted(out)


# ---------------------------------------------------------------------------
# 2. A demonstracao de que o historico nao usa proximidade
# ---------------------------------------------------------------------------


PROXIMITY_PROBES = (None, 0.5, _HUNT_PROXIMITY_ATR, 100.0)


def history_invariance(data: Any) -> dict[str, Any]:
    """O stream historico muda quando a proximidade muda radicalmente?

    Se `build_history` dependesse de proximidade em qualquer grau, um
    `proximity_atr` de 100 (que tornaria todo pool do universo "proximo") teria
    de produzir um stream diferente de `None` (que desliga a normalizacao).
    Comparado por igualdade de episodio, nao por contagem.
    """
    reference: dict[str, list[Any]] | None = None
    resultados = {}
    for probe in PROXIMITY_PROBES:
        engine = LiquidityHuntEngine(proximity_atr=probe)
        streams = {
            "hunt": engine.build_history(data),
            "continuation": engine.build_continuation_history(data),
        }
        if reference is None:
            reference = streams
            resultados[str(probe)] = {"n_hunt": len(streams["hunt"]),
                                      "n_cont": len(streams["continuation"]),
                                      "identico": True}
            continue
        identico = all(streams[k] == reference[k] for k in streams)
        resultados[str(probe)] = {
            "n_hunt": len(streams["hunt"]),
            "n_cont": len(streams["continuation"]),
            "identico": identico,
        }
        # O estado VIVO, ao contrario, tem de reagir -- se ele nao reagisse, a
        # sonda nao estaria testando nada.
        resultados[str(probe)]["live_targets"] = len(
            engine.build(data).targets
        )
    return resultados


# ---------------------------------------------------------------------------
# 3. Painel
# ---------------------------------------------------------------------------


def evaluate_window(
    data: Any, symbol: str, tf: str, window: str, proximity_atr: float
) -> list[dict[str, Any]]:
    """Legacy x causal em cada instante amostrado da janela."""
    candles = data.candles
    if len(candles) < 30:
        return []
    legacy = legacy_proximity(candles, proximity_atr)
    rows: list[dict[str, Any]] = []
    inicio = max(2, len(candles) - EVAL_TAIL)
    for i in range(inicio, len(candles), EVAL_STEP):
        at = candles[i].timestamp
        causal = causal_proximity(candles, at, proximity_atr)
        price = candles[i].close
        rec: dict[str, Any] = {
            "symbol": symbol, "tf": tf, "window": window,
            "at": at.isoformat(), "i_from_end": len(candles) - 1 - i,
            "legacy": legacy, "causal": causal,
            "diff": legacy - causal,
            "rel": (legacy - causal) / causal if causal else 0.0,
        }
        for hunted_short in (True, False):
            a = eligible_targets(data.liquidity_zones, price, legacy, hunted_short, at)
            b = eligible_targets(data.liquidity_zones, price, causal, hunted_short, at)
            lado = "short" if hunted_short else "long"
            sa, sb = set(a), set(b)
            rec[f"n_legacy_{lado}"] = len(a)
            rec[f"n_causal_{lado}"] = len(b)
            rec[f"so_legacy_{lado}"] = len(sa - sb)
            rec[f"so_causal_{lado}"] = len(sb - sa)
            rec[f"muda_{lado}"] = sa != sb
            rec[f"muda_mais_proximo_{lado}"] = (
                (a[0] if a else None) != (b[0] if b else None)
            )
        rows.append(rec)
    return rows


def run_panel(
    symbols: tuple[str, ...], tf_names: tuple[str, ...], seed: int = 7
) -> dict[str, Any]:
    rng = random.Random(seed)
    assert rng
    rows: list[dict[str, Any]] = []
    invariance: dict[str, int] = Counter()
    invariance_detail: list[dict[str, Any]] = []
    errors: list[str] = []

    for symbol in symbols:
        for tf_name in tf_names:
            tf = TFS[tf_name]
            htf = _HIGHER_TIMEFRAME_MAP[tf]
            try:
                series = fetch_series(symbol, tf, htf)
            except Exception as exc:  # noqa: BLE001 - um simbolo morto nao para o painel
                errors.append(f"{symbol} {tf_name}: {type(exc).__name__}: {exc}")
                continue
            if not series[htf] or len(series[tf]) < VISIBLE_LIMIT:
                errors.append(f"{symbol} {tf_name}: serie insuficiente")
                continue
            for w in range(WINDOWS):
                back = w * WINDOW_STEP
                if back >= len(series[tf]):
                    continue
                try:
                    data = load_dashboard_data(
                        provider=WindowProvider(series, cut=series[tf][-1 - back].timestamp),
                        symbol=symbol, timeframe=tf, limit=VISIBLE_LIMIT,
                        compute_narrative=False, futures_provider=NoFuturesProvider(),
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{symbol} {tf_name} w{w}: {type(exc).__name__}: {exc}")
                    continue
                rows.extend(
                    evaluate_window(data, symbol, tf_name, f"w{w}", _HUNT_PROXIMITY_ATR)
                )
                inv = history_invariance(data)
                for probe, res in inv.items():
                    invariance["janelas"] += 1 if probe == str(PROXIMITY_PROBES[0]) else 0
                    if not res["identico"]:
                        invariance["historico_mudou"] += 1
                        invariance_detail.append(
                            {"symbol": symbol, "tf": tf_name, "window": f"w{w}",
                             "probe": probe, **res}
                        )
                    if res.get("live_targets") is not None:
                        invariance[f"live_targets_{probe}"] += res["live_targets"]
            print(f"  {symbol} {tf_name}: avaliacoes={len(rows)}", flush=True)

    return {
        "meta": {
            "symbols": list(symbols), "timeframes": list(tf_names),
            "windows": WINDOWS, "visible_limit": VISIBLE_LIMIT,
            "proximity_atr": _HUNT_PROXIMITY_ATR,
            "eval_step": EVAL_STEP, "eval_tail": EVAL_TAIL,
            "probes": [str(p) for p in PROXIMITY_PROBES],
        },
        "rows": rows,
        "invariance": dict(invariance),
        "invariance_detail": invariance_detail[:20],
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# 4. Relatorio
# ---------------------------------------------------------------------------


def _quantis(vals: list[float]) -> dict[str, float]:
    if not vals:
        return {}
    s = sorted(vals)
    def q(p: float) -> float:
        return s[min(len(s) - 1, int(p * len(s)))]
    return {"p50": q(0.50), "p75": q(0.75), "p90": q(0.90),
            "p95": q(0.95), "max": s[-1]}


def _pct(n: float, d: float) -> str:
    return f"{n/d:.1%}" if d else "-"


def report(payload: dict[str, Any]) -> None:  # noqa: PLR0915 - um relatorio por item
    rows = payload["rows"]
    meta = payload["meta"]
    print("\n" + "=" * 78)
    print("H4.0 - CAUSALIDADE DA PROXIMIDADE ATR DO HUNT")
    print("=" * 78)
    print(f"painel: {len(meta['symbols'])} simbolos x {meta['timeframes']} x "
          f"{meta['windows']} janelas | proximity_atr = {meta['proximity_atr']}")
    print(f"avaliacoes: {len(rows)} | erros: {len(payload['errors'])}")

    print("\n--- 1. A CADEIA, E ONDE ELA NAO CHEGA ---")
    inv = payload["invariance"]
    mudou = inv.get("historico_mudou", 0)
    print("  `_effective_proximity` tem um unico caller: `build()` (linha 269).")
    print("  `build_history` / `build_continuation_history` nao a chamam.")
    print(f"  SONDA: rodando a producao com proximity_atr em {meta['probes']},")
    print(f"  o stream historico mudou em {mudou} de {inv.get('janelas', 0)} janelas.")
    for probe in meta["probes"]:
        alvos = inv.get(f"live_targets_{probe}")
        if alvos is not None:
            print(f"    targets do estado VIVO com proximity_atr={probe:>5s}: {alvos}")
    print("  (o vivo reage a sonda; o historico nao. Se o historico tambem nao")
    print("   reagisse a uma sonda que o vivo ignora, a sonda e que estaria quebrada.)")

    print("\n--- 5. QUANTO O VALOR DE PROXIMITY MUDA (legacy - causal) ---")
    rel = [abs(r["rel"]) for r in rows]
    print("  |diferenca relativa|: " + "  ".join(
        f"{k} {v:.2%}" for k, v in _quantis(rel).items()))
    for banda in DIFF_BANDS:
        n = sum(1 for r in rows if abs(r["rel"]) > banda)
        print(f"    muda mais de {banda:.0%}: {n:6d}  ({_pct(n, len(rows))})")
    print("\n  por timeframe:")
    for tf in ("M15", "H1", "H4"):
        sel = [abs(r["rel"]) for r in rows if r["tf"] == tf]
        if sel:
            qs = _quantis(sel)
            print(f"    {tf:4s} n={len(sel):6d} p50 {qs['p50']:6.2%} "
                  f"p90 {qs['p90']:6.2%} p95 {qs['p95']:6.2%} max {qs['max']:7.2%}")

    print("\n--- 10. DIRECAO DO VIES ---")
    maior = sum(1 for r in rows if r["rel"] > 0)
    menor = sum(1 for r in rows if r["rel"] < 0)
    print(f"  legacy > causal (proximity AMPLIADA): {maior:6d} ({_pct(maior, len(rows))})")
    print(f"  legacy < causal (proximity REDUZIDA): {menor:6d} ({_pct(menor, len(rows))})")
    print(f"  media da diferenca relativa: {fmean(r['rel'] for r in rows):+.2%}"
          if rows else "")
    n_leg = sum(r["n_legacy_short"] + r["n_legacy_long"] for r in rows)
    n_cau = sum(r["n_causal_short"] + r["n_causal_long"] for r in rows)
    print(f"  targets elegiveis no total: legacy {n_leg} | causal {n_cau} "
          f"({(n_leg - n_cau) / n_cau:+.1%})" if n_cau else "")

    print("\n--- 6. IMPACTO NA SELECAO DE TARGETS ---")
    for lado in ("short", "long"):
        muda = sum(1 for r in rows if r[f"muda_{lado}"])
        prox = sum(1 for r in rows if r[f"muda_mais_proximo_{lado}"])
        so_l = sum(r[f"so_legacy_{lado}"] for r in rows)
        so_c = sum(r[f"so_causal_{lado}"] for r in rows)
        print(f"  lado {lado:6s}: conjunto muda {muda:6d} ({_pct(muda, len(rows))}) | "
              f"alvo mais proximo muda {prox:5d} ({_pct(prox, len(rows))})")
        print(f"                 pools so no legacy {so_l:6d} | so no causal {so_c:6d}")
    print("\n  por timeframe (conjunto de targets muda):")
    for tf in ("M15", "H1", "H4"):
        sel = [r for r in rows if r["tf"] == tf]
        if sel:
            m = sum(1 for r in sel if r["muda_short"] or r["muda_long"])
            print(f"    {tf:4s} {m:6d} de {len(sel):6d}  ({_pct(m, len(sel))})")

    print("\n--- 7. IMPACTO NO STREAM DE EPISODIOS ---")
    print("  A/idenficos: 100% -- por construcao, demonstrado acima: nenhum")
    print("  episodio historico depende de proximidade, entao B/C/D/E/F/G/H")
    print("  sao todos ZERO e nao ha o que diffar. O objeto que a proximidade")
    print("  altera e `LiquidityHuntState.targets` / `phase`, que existe apenas")
    print("  no estado vivo e nao e um episodio.")

    print("\n--- 13. LIVE vs HISTORY ---")
    print("  live `build()`   : usa a janela visivel ATE AGORA -- todo candle e")
    print("                     passado, logo CAUSAL. Mas depende do tamanho da")
    print("                     janela e muda a cada vela nova: repinta.")
    print("  `build_history`  : nao usa proximidade. Nada a corrigir.")
    print("  `build_continuation_history`: idem.")
    print("  Nao ha cache nem metrica global compartilhada entre chamadas: o")
    print("  valor sai de `data.candles` a cada chamada, e `data` e um snapshot.")


def chain_audit() -> None:
    """Imprime a cadeia e os callers, a partir do codigo em execucao."""
    import inspect

    src = inspect.getsource(LiquidityHuntEngine)
    print("\n=== CADEIA DA PROXIMIDADE (do codigo em execucao) ===")
    for metodo in ("_effective_proximity", "_zone_targets", "_band_targets"):
        chamadas = src.count(f"self.{metodo}(")
        print(f"  {metodo:24s} chamado {chamadas}x dentro do motor")
    for metodo in ("build", "build_history", "build_continuation_history"):
        corpo = inspect.getsource(getattr(LiquidityHuntEngine, metodo))
        usa = "_effective_proximity" in corpo
        alvos = "_zone_targets" in corpo or "_band_targets" in corpo
        print(f"  {metodo:28s} usa proximity={usa!s:5s} monta targets={alvos!s:5s}")
    print("\n  formula (producao):")
    print(inspect.getsource(LiquidityHuntEngine._effective_proximity))


def case_report(symbol: str, tf_name: str) -> None:
    """Casos reais: onde a volatilidade muda e onde o alvo muda junto."""
    tf = TFS[tf_name]
    data = load_dashboard_data(
        symbol=symbol, timeframe=tf, limit=VISIBLE_LIMIT,
        compute_narrative=False, futures_provider=NoFuturesProvider(),
    )
    rows = evaluate_window(data, symbol, tf_name, "live", _HUNT_PROXIMITY_ATR)
    print(f"\n=== {symbol} {tf_name}: {len(rows)} instantes avaliados ===")
    print(f"  proximity legacy (janela inteira): {rows[0]['legacy']:.4%}"
          if rows else "  (sem dados)")
    casos = [
        ("A) volatilidade muda, alvos NAO mudam",
         [r for r in rows if abs(r["rel"]) > 0.05
          and not r["muda_short"] and not r["muda_long"]]),
        ("B) pool elegivel SO no legacy",
         [r for r in rows if r["so_legacy_short"] or r["so_legacy_long"]]),
        ("C) pool elegivel SO no causal",
         [r for r in rows if r["so_causal_short"] or r["so_causal_long"]]),
        ("E) totalmente estavel",
         [r for r in rows if abs(r["rel"]) < 0.005
          and not r["muda_short"] and not r["muda_long"]]),
    ]
    for titulo, sel in casos:
        print(f"\n  -- {titulo} ({len(sel)} instantes) --")
        for r in sel[:3]:
            print(f"    {r['at']}  legacy {r['legacy']:.4%} causal {r['causal']:.4%} "
                  f"({r['rel']:+.1%})")
            print(f"      targets short {r['n_legacy_short']}->{r['n_causal_short']}  "
                  f"long {r['n_legacy_long']}->{r['n_causal_long']}  "
                  f"(so_legacy {r['so_legacy_short'] + r['so_legacy_long']}, "
                  f"so_causal {r['so_causal_short'] + r['so_causal_long']})")
    print("\n  D) episodio muda por causa disso: nenhum, e nao pode haver --")
    print("     o historico nao consulta proximidade (ver --chain).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report-only", type=Path)
    parser.add_argument("--symbols", type=int)
    parser.add_argument("--tfs", default="M15,H1,H4")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--case", help="SIMBOLO:TF")
    parser.add_argument("--chain", action="store_true", help="so a auditoria da cadeia")
    args = parser.parse_args()

    from research._symbols import UNIVERSE

    if args.chain:
        chain_audit()
        return
    if args.report_only:
        report(json.loads(args.report_only.read_text()))
        return
    if args.case:
        symbol, tf_name = args.case.split(":")
        case_report(symbol, tf_name)
        return
    payload = run_panel(
        UNIVERSE[: args.symbols] if args.symbols else UNIVERSE,
        tuple(args.tfs.split(",")), seed=args.seed,
    )
    if args.out:
        args.out.write_text(json.dumps(payload, indent=1, default=str))
        print(f"\nescrito: {args.out}")
    report(payload)


if __name__ == "__main__":
    main()
