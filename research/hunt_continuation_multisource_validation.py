"""H2.2: a regra `continuation >= 2 fontes` sobrevive fora da amostra?

A regra esta **congelada** desde a H2.1 e nada aqui a ajusta:

    aceita  <=>  score >= limiar de producao (4)  E  n_unique_sources >= 2

`delta` conta como fonte, como na H2.1. Se o resultado piorar, piorou.

O recorte que o enunciado pede, e o que existe
----------------------------------------------
O pedido e "dados MAIS RECENTES do que os usados na H2.1". Esses dados **nao
existem**: o painel da H2.1 rodou hoje e sua janela w0 termina no ultimo candle
fechado de cada serie, entao ele ja alcanca a borda viva. Nao ha fita mais nova
para medir ate que o tempo passe -- e inventar um recorte "novo" a partir dos
mesmos candles seria re-medir a amostra e chamar de confirmacao.

O que existe, e e genuinamente **nao visto** pela H2.0 nem pela H2.1, sao dois
eixos, e esta etapa roda os dois:

    PAINEL T (temporal, anterior)   os mesmos 72 simbolos, em janelas
                                    inteiramente ANTERIORES ao span da H2.1.
                                    A H2.1 enxergou os ultimos ~800 candles de
                                    cada serie; aqui as janelas vivem entre
                                    -2000 e -800. Nenhum episodio se repete, e
                                    sao outros regimes de mercado.

    PAINEL S (secao cruzada)        40 simbolos que nunca entraram em nenhum
                                    painel deste projeto, sorteados por hash do
                                    proprio nome (dos 651 perps USDT fora do
                                    `UNIVERSE`), no span recente.

Os dois sao out-of-sample de verdade; nenhum deles e "mais recente", e o
relatorio diz isso em vez de deixar o rotulo sugerir o contrario. A confirmacao
temporal para frente so pode ser feita daqui a algumas semanas, re-rodando este
mesmo arquivo.

O que a regra alcanca em producao (verificado, nao suposto)
-----------------------------------------------------------
`build()` -- o estado vivo, o card de KPI e a escada do `app/overview.py` -- nao
chama a continuation em ponto nenhum: ele produz `LiquidityHuntState` e so trata
da caca contra-tendencia. Os episodios de continuation visiveis no grafico vem
de `dashboard_data.py:3161` (`build_continuation_history`) ->
`DashboardData.liquidity_continuation_history` -> `api/schemas.py:76` ->
`MainChart.tsx`. Um gate na continuation, portanto, muda um unico caminho e nao
toca o estado vivo.

Run:
    poetry run python -m research.hunt_continuation_multisource_validation \\
        --out research/hunt_continuation_multisource_validation_baseline.json
    poetry run python -m research.hunt_continuation_multisource_validation \\
        --report-only research/hunt_continuation_multisource_validation_baseline.json
    poetry run python -m research.hunt_continuation_multisource_validation --case BTCUSDT:M15
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean, median
from typing import Any

from liquidity_hunter.app.dashboard_data import _HIGHER_TIMEFRAME_MAP, load_dashboard_data
from liquidity_hunter.core.domain import Candle, RetailPositioning, TimeFrame
from research._paginated import PaginatedFuturesProvider
from research._symbols import UNIVERSE
from research.hunt_htf_causality import TFS, NoFuturesProvider, WindowProvider
from research.hunt_score_redundancy import HORIZONS, VISIBLE_LIMIT
from research.hunt_score_variants import V0, V2, engine_episodes, outcome_of

#: Quantos candles baixar por (simbolo, timeframe). O painel T precisa chegar a
#: 2000 candles atras; 2400 da margem para um simbolo com buracos na serie.
DEEP_LIMIT = 2400

#: Onde o painel T comeca a olhar: a H2.1 enxergou os ultimos
#: `VISIBLE_LIMIT + 2 * 150 = 800` candles de cada serie, entao a primeira
#: janela daqui termina exatamente onde aquela comecou. Nao ha sobreposicao de
#: um unico candle -- que e o que faz este painel ser outra amostra, e nao a
#: mesma amostra recortada de outro jeito.
H21_SPAN_CANDLES = 800

#: Janelas do painel T: cortes recuando a partir de `H21_SPAN_CANDLES`.
T_WINDOWS = 3
T_WINDOW_STEP = 350

#: Janelas do painel S, na geometria da H2.1 (span recente).
S_WINDOWS = 2
S_WINDOW_STEP = 150

#: Quantos simbolos ineditos sortear.
N_NEW_SYMBOLS = 40


def new_symbols(pool: list[str], n: int = N_NEW_SYMBOLS) -> tuple[str, ...]:
    """Amostra deterministica de simbolos nunca medidos.

    Ordenada pelo hash do proprio nome, como em `research._symbols`: a escolha e
    uma propriedade do simbolo e nao da ordem em que a exchange respondeu, entao
    ela reproduz em qualquer maquina e nao pode ser empurrada depois de ver um
    resultado.
    """
    ranked = sorted(
        pool, key=lambda s: int.from_bytes(hashlib.sha256(s.encode()).digest()[:8], "big")
    )
    return tuple(ranked[:n])


def discover_pool() -> list[str]:
    """Perps USDT ativos que nunca entraram em painel nenhum deste projeto."""
    import ccxt

    markets = ccxt.binanceusdm().load_markets()
    perps = [
        m["id"]
        for m in markets.values()
        if m.get("swap") and m.get("quote") == "USDT" and m.get("active")
    ]
    return sorted(set(perps) - set(UNIVERSE))


# ---------------------------------------------------------------------------
# 1. Coleta
# ---------------------------------------------------------------------------


def fetch_deep(
    provider: PaginatedFuturesProvider, symbol: str, tf: TimeFrame, htf: TimeFrame
) -> dict[TimeFrame, list[Candle]]:
    return {
        tf: provider.get_ohlcv(symbol, tf, DEEP_LIMIT),
        htf: provider.get_ohlcv(symbol, htf, DEEP_LIMIT),
    }


def collect(
    symbols: tuple[str, ...],
    tf_names: tuple[str, ...],
    panel: str,
    windows: int,
    step: int,
    offset: int,
    rng: random.Random,
    episodes: dict[str, dict[str, dict[str, Any]]],
    outcomes: dict[str, dict[str, Any]],
    spans: dict[str, list[str]],
    errors: list[str],
) -> None:
    """Roda os dois bracos sobre um painel e acumula episodios e desfechos.

    ``offset`` e quantos candles do fim ficam **de fora**: zero para o span
    recente, `H21_SPAN_CANDLES` para o painel temporal anterior. E o unico
    parametro que separa os dois paineis, o que mantem a geometria (janelas,
    passo, limite visivel) identica entre eles.
    """
    prov = PaginatedFuturesProvider()
    for symbol in symbols:
        for tf_name in tf_names:
            tf = TFS[tf_name]
            htf = _HIGHER_TIMEFRAME_MAP[tf]
            try:
                series = fetch_deep(prov, symbol, tf, htf)
            except Exception as exc:  # noqa: BLE001 - um simbolo morto nao para o painel
                errors.append(f"{panel} {symbol} {tf_name}: {type(exc).__name__}: {exc}")
                continue
            needed = offset + VISIBLE_LIMIT + (windows - 1) * step
            if not series[htf] or len(series[tf]) < needed:
                errors.append(
                    f"{panel} {symbol} {tf_name}: serie insuficiente "
                    f"({len(series[tf])} < {needed})"
                )
                continue
            lo = series[tf][-(needed)].timestamp
            hi = series[tf][-(offset + 1)].timestamp
            spans[f"{panel}|{symbol}:{tf_name}"] = [lo.isoformat(), hi.isoformat()]
            for w in range(windows):
                back = offset + w * step
                cut = series[tf][-1 - back].timestamp
                try:
                    data = load_dashboard_data(
                        provider=WindowProvider(series, cut=cut),
                        symbol=symbol,
                        timeframe=tf,
                        limit=VISIBLE_LIMIT,
                        compute_narrative=False,
                        futures_provider=NoFuturesProvider(),
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(
                        f"{panel} {symbol} {tf_name} w{w}: {type(exc).__name__}: {exc}"
                    )
                    continue
                for arm, cls in (("V0", V0), ("V2", V2)):
                    for key, ep in engine_episodes(cls(), data, symbol, tf_name).items():
                        if key.stream != "continuation":
                            continue
                        ident = f"{panel}|{symbol}|{tf_name}|{key.anchor}"
                        episodes[arm].setdefault(ident, {**ep, "window": f"w{w}"})
                        if ident not in outcomes:
                            up = ep["hunted_side"] == RetailPositioning.SHORT.value
                            res = outcome_of(
                                data.candles, datetime.fromisoformat(key.anchor), up, rng
                            )
                            if res is not None:
                                outcomes[ident] = {
                                    "panel": panel, "symbol": symbol, "tf": tf_name,
                                    "anchor": key.anchor, "up": up,
                                    "sources": ep["sources"], "score": ep["score"],
                                    **res,
                                }
            print(
                f"  [{panel}] {symbol} {tf_name}: V0={len(episodes['V0'])} "
                f"V2={len(episodes['V2'])}",
                flush=True,
            )


def live_impact(symbols: tuple[str, ...], tf_names: tuple[str, ...]) -> dict[str, Any]:
    """Quantos grabs de continuation VISIVEIS hoje o gate removeria.

    Roda na borda viva, com a serie inteira que o provider entrega, que e o que
    o dashboard veria agora. Nao mede desfecho: metade desses episodios ainda
    nao tem horizonte, e o ponto aqui e o custo visual, nao a qualidade.
    """
    prov = PaginatedFuturesProvider()
    total = removed = 0
    por_tf: Counter[str] = Counter()
    exemplos: list[dict[str, Any]] = []
    for symbol in symbols:
        for tf_name in tf_names:
            tf = TFS[tf_name]
            htf = _HIGHER_TIMEFRAME_MAP[tf]
            try:
                series = fetch_deep(prov, symbol, tf, htf)
                data = load_dashboard_data(
                    provider=WindowProvider(series),
                    symbol=symbol, timeframe=tf, limit=VISIBLE_LIMIT,
                    compute_narrative=False, futures_provider=NoFuturesProvider(),
                )
            except Exception:  # noqa: BLE001
                continue
            base = engine_episodes(V0(), data, symbol, tf_name)
            var = engine_episodes(V2(), data, symbol, tf_name)
            for key, ep in base.items():
                if key.stream != "continuation":
                    continue
                total += 1
                if key not in var:
                    removed += 1
                    por_tf[tf_name] += 1
                    exemplos.append({"symbol": symbol, "tf": tf_name,
                                     "anchor": key.anchor, "sources": ep["sources"],
                                     "score": ep["score"]})
    return {
        "visiveis": total, "removidos": removed,
        "por_tf": dict(por_tf), "exemplos": exemplos[:40],
    }


# ---------------------------------------------------------------------------
# 2. Estatistica
# ---------------------------------------------------------------------------


def _agg(rows: list[dict[str, Any]], h: int) -> dict[str, float] | None:
    rows = [r for r in rows if f"mfe_{h}" in r]
    if not rows:
        return None
    mfe = fmean(float(r[f"mfe_{h}"]) for r in rows)
    mae = fmean(float(r[f"mae_{h}"]) for r in rows)
    return {
        "n": float(len(rows)),
        "mfe": mfe,
        "mae": mae,
        "ratio": mfe / mae if mae else 0.0,
        "net": fmean(float(r[f"net_{h}"]) for r in rows),
        "win": fmean(1.0 if r[f"win_{h}"] else 0.0 for r in rows),
        "ctl_win": fmean(float(r[f"ctl_win_{h}"]) for r in rows),
    }


def _fmt(st: dict[str, float] | None) -> str:
    if st is None:
        return "n=    0"
    return (
        f"n={int(st['n']):5d} MFE {st['mfe']:5.2f} MAE {st['mae']:5.2f} "
        f"R {st['ratio']:5.2f} net {st['net']:+5.2f} acerto {st['win']:6.1%} "
        f"(ctl {st['ctl_win']:.1%})"
    )


def split_rows(payload: dict[str, Any], panel: str) -> tuple[list, list, list]:
    """(baseline, sobreviventes, removidos) do painel pedido."""
    base_ids = [i for i in payload["episodes"]["V0"] if i.startswith(f"{panel}|")]
    var_ids = set(payload["episodes"]["V2"])
    out = payload["outcomes"]
    base = [out[i] for i in base_ids if i in out]
    keep = [out[i] for i in base_ids if i in out and i in var_ids]
    gone = [out[i] for i in base_ids if i in out and i not in var_ids]
    return base, keep, gone


# ---------------------------------------------------------------------------
# 3. Relatorio
# ---------------------------------------------------------------------------


def report_panel(payload: dict[str, Any], panel: str, titulo: str) -> dict[str, Any]:
    base, keep, gone = split_rows(payload, panel)
    n_base = sum(1 for i in payload["episodes"]["V0"] if i.startswith(f"{panel}|"))
    n_var = sum(
        1 for i in payload["episodes"]["V2"] if i.startswith(f"{panel}|")
    )
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")
    if not n_base:
        print("  sem episodios")
        return {}
    print(f"  episodios: baseline {n_base} -> V2 {n_var}  "
          f"(coverage {n_var / n_base:.1%}, removidos {n_base - n_var})")

    print("\n  --- 3/4. BASELINE vs V2 vs REMOVIDOS, por horizonte ---")
    for h in HORIZONS:
        print(f"    h={h:2d}")
        for label, rows in (("baseline ", base), ("V2 (>=2) ", keep), ("REMOVIDOS", gone)):
            print(f"      {label} {_fmt(_agg(rows, h))}")

    st_keep, st_base, st_gone = _agg(keep, 20), _agg(base, 20), _agg(gone, 20)

    print("\n  --- 5. MECANISMO: qual era a fonte unica removida ---")
    fontes = Counter(
        r["sources"][0] if len(r["sources"]) == 1 else "MULTI" for r in gone
    )
    for src, k in fontes.most_common():
        sub = _agg([r for r in gone if r["sources"] == [src]], 20)
        print(f"    {src:12s} {k:5d} ({k / len(gone):5.1%})  {_fmt(sub)}" if gone
              else f"    {src}")

    print("\n  --- 6. POR TIMEFRAME ---")
    for tf in ("M15", "H1", "H4"):
        b = _agg([r for r in base if r["tf"] == tf], 20)
        v = _agg([r for r in keep if r["tf"] == tf], 20)
        g = _agg([r for r in gone if r["tf"] == tf], 20)
        if b and v:
            print(f"    {tf:4s} base {b['win']:6.1%} -> V2 {v['win']:6.1%} "
                  f"({int(v['n']):4d}) delta {v['win'] - b['win']:+.1%} | "
                  f"removidos {g['win']:6.1%} (ctl {g['ctl_win']:.1%})" if g
                  else f"    {tf:4s} base {b['win']:6.1%} -> V2 {v['win']:6.1%}")

    print("\n  --- 7. POR DIRECAO ---")
    for label, up in (("bullish (shorts cacados)", True), ("bearish (longs cacados)", False)):
        b = _agg([r for r in base if r["up"] is up], 20)
        v = _agg([r for r in keep if r["up"] is up], 20)
        if b and v:
            print(f"    {label:26s} base {b['win']:6.1%} -> V2 {v['win']:6.1%} "
                  f"({int(v['n']):4d}) delta {v['win'] - b['win']:+.1%}")

    print("\n  --- 8. POR SIMBOLO ---")
    deltas = []
    for symbol in {r["symbol"] for r in base}:
        b = _agg([r for r in base if r["symbol"] == symbol], 20)
        v = _agg([r for r in keep if r["symbol"] == symbol], 20)
        if b and v and b["n"] >= 3:
            deltas.append((v["win"] - b["win"], symbol, int(b["n"]), int(v["n"])))
    if deltas:
        up_n = sum(1 for d, *_ in deltas if d > 0)
        down_n = sum(1 for d, *_ in deltas if d < 0)
        print(f"    {len(deltas)} simbolos (n>=3) | melhoram {up_n} ({up_n/len(deltas):.0%})"
              f" | pioram {down_n} ({down_n/len(deltas):.0%}) | "
              f"mediana {median(d for d, *_ in deltas):+.1%}")
        deltas.sort(reverse=True)
        print("      top 5   : " + ", ".join(
            f"{s} {d:+.0%}({a}->{b})" for d, s, a, b in deltas[:5]))
        print("      bottom 5: " + ", ".join(
            f"{s} {d:+.0%}({a}->{b})" for d, s, a, b in deltas[-5:]))

    criterios = {
        "1 multi >= baseline": bool(st_keep and st_base and st_keep["win"] >= st_base["win"]),
        "2 multi > controle": bool(st_keep and st_keep["win"] > st_keep["ctl_win"]),
        "3 single <= baseline": bool(st_gone and st_base and st_gone["win"] <= st_base["win"]),
        "6 coverage razoavel": n_var / n_base >= 0.6,
    }
    print("\n  --- 13. CRITERIOS ---")
    for nome, ok in criterios.items():
        print(f"    [{'OK ' if ok else 'NAO'}] {nome}")
    return {"panel": panel, "criterios": criterios, "coverage": n_var / n_base,
            "keep": st_keep, "base": st_base, "gone": st_gone,
            "symbol_up": up_n / len(deltas) if deltas else None}


def report(payload: dict[str, Any]) -> None:
    meta = payload["meta"]
    print(f"\n{'=' * 78}\nH2.2 - CONFIRMACAO OUT-OF-SAMPLE DE `continuation >= 2 fontes`"
          f"\n{'=' * 78}")
    print("A regra esta congelada: score >= 4 E n_unique_sources >= 2 (delta conta).")
    print("\nNAO existe fita mais recente que a da H2.1: aquele painel alcanca a borda")
    print("viva. Os dois paineis abaixo sao out-of-sample por outros eixos.")
    print(f"\n  PAINEL T: {len(meta['t_symbols'])} simbolos do UNIVERSE, janelas "
          f"ANTERIORES aos ultimos {H21_SPAN_CANDLES} candles (a amostra da H2.1)")
    print(f"  PAINEL S: {len(meta['s_symbols'])} simbolos INEDITOS, span recente")
    print(f"  timeframes {meta['timeframes']} | visivel {VISIBLE_LIMIT} candles/janela")
    print(f"  erros: {len(payload['errors'])}")
    for panel in ("T", "S"):
        datas = [v for k, v in payload["spans"].items() if k.startswith(f"{panel}|")]
        if datas:
            print(f"  datas painel {panel}: {min(d[0] for d in datas)[:10]} .. "
                  f"{max(d[1] for d in datas)[:10]}")

    v_t = report_panel(payload, "T", "PAINEL T - MESMOS SIMBOLOS, JANELAS ANTERIORES")
    v_s = report_panel(payload, "S", "PAINEL S - SIMBOLOS INEDITOS, SPAN RECENTE")

    live = payload["live"]
    print(f"\n{'=' * 78}\n9/14. IMPACTO NO GRAFICO ATUAL\n{'=' * 78}")
    print("  build() vivo NAO usa continuation (produz LiquidityHuntState; "
          "app/overview.py idem).")
    print("  Os episodios visiveis vem de dashboard_data.py:3161 -> "
          "liquidity_continuation_history -> MainChart.tsx.")
    if live.get("visiveis"):
        print(f"  grabs de continuation visiveis agora: {live['visiveis']}  |  "
              f"removidos por >=2: {live['removidos']} "
              f"({live['removidos'] / live['visiveis']:.1%})")
        print(f"  por timeframe: {live['por_tf']}")

    print(f"\n{'=' * 78}\nVEREDITO\n{'=' * 78}")
    for v in (v_t, v_s):
        if v:
            ok = all(v["criterios"].values())
            print(f"  painel {v['panel']}: {'PASSA' if ok else 'NAO PASSA'} "
                  f"({sum(v['criterios'].values())}/{len(v['criterios'])} criterios, "
                  f"coverage {v['coverage']:.1%})")


def case_report(symbol: str, tf_name: str) -> None:
    """Continuations recentes de fonte unica e de 2+, com o que veio depois."""
    tf = TFS[tf_name]
    prov = PaginatedFuturesProvider()
    series = fetch_deep(prov, symbol, tf, _HIGHER_TIMEFRAME_MAP[tf])
    data = load_dashboard_data(
        provider=WindowProvider(series), symbol=symbol, timeframe=tf,
        limit=VISIBLE_LIMIT, compute_narrative=False, futures_provider=NoFuturesProvider(),
    )
    rng = random.Random(1)
    base = engine_episodes(V0(), data, symbol, tf_name)
    var = engine_episodes(V2(), data, symbol, tf_name)
    print(f"\n=== {symbol} {tf_name} ===")
    for titulo, pred in (
        ("A) fonte unica -- V2 REMOVERIA", lambda k, e: k not in var),
        ("B) >=2 fontes -- V2 PRESERVA", lambda k, e: k in var),
    ):
        print(f"\n  -- {titulo} --")
        sel = [(k, e) for k, e in base.items()
               if k.stream == "continuation" and pred(k, e)]
        if not sel:
            print("    (nenhum)")
        for key, ep in sel[-4:]:
            up = ep["hunted_side"] == RetailPositioning.SHORT.value
            res = outcome_of(data.candles, datetime.fromisoformat(key.anchor), up, rng)
            desfecho = (
                f"MFE {res['mfe_20']:.2f} MAE {res['mae_20']:.2f} "
                f"{'ACERTOU' if res['win_20'] else 'errou'}"
                if res else "sem horizonte ainda"
            )
            print(f"    {key.anchor} score={ep['score']:4.1f} "
                  f"[{' '.join(ep['sources'])}] -> {desfecho}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report-only", type=Path)
    parser.add_argument("--tfs", default="M15,H1,H4")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--case", help="SIMBOLO:TF")
    parser.add_argument("--skip-live", action="store_true")
    args = parser.parse_args()

    if args.report_only:
        report(json.loads(args.report_only.read_text()))
        return
    if args.case:
        symbol, tf_name = args.case.split(":")
        case_report(symbol, tf_name)
        return

    tf_names = tuple(args.tfs.split(","))
    rng = random.Random(args.seed)
    episodes: dict[str, dict[str, dict[str, Any]]] = {"V0": {}, "V2": {}}
    outcomes: dict[str, dict[str, Any]] = {}
    spans: dict[str, list[str]] = {}
    errors: list[str] = []

    print("painel T (janelas anteriores as da H2.1)...")
    collect(UNIVERSE, tf_names, "T", T_WINDOWS, T_WINDOW_STEP, H21_SPAN_CANDLES,
            rng, episodes, outcomes, spans, errors)

    inedito = new_symbols(discover_pool())
    print(f"painel S ({len(inedito)} simbolos ineditos)...")
    collect(inedito, tf_names, "S", S_WINDOWS, S_WINDOW_STEP, 0,
            rng, episodes, outcomes, spans, errors)

    live = {} if args.skip_live else live_impact(UNIVERSE, tf_names)

    payload = {
        "meta": {
            "generated": datetime.now(tz=UTC).isoformat(),
            "t_symbols": list(UNIVERSE),
            "s_symbols": list(inedito),
            "timeframes": list(tf_names),
            "t_windows": T_WINDOWS, "t_step": T_WINDOW_STEP,
            "s_windows": S_WINDOWS, "s_step": S_WINDOW_STEP,
            "h21_span_candles": H21_SPAN_CANDLES,
            "visible_limit": VISIBLE_LIMIT,
            "deep_limit": DEEP_LIMIT,
            "seed": args.seed,
            "futures": "disabled (NoFuturesProvider)",
            "regra": "score >= threshold E n_unique_sources >= 2 (congelada)",
        },
        "episodes": episodes, "outcomes": outcomes, "spans": spans,
        "live": live, "errors": errors,
    }
    if args.out:
        args.out.write_text(json.dumps(payload, indent=1, default=str))
        print(f"\nescrito: {args.out}")
    report(payload)


if __name__ == "__main__":
    main()
