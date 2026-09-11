"""H3.0: o `raid` do HUNT pontua liquidez que ja tinha sido consumida?

O que o codigo faz hoje, lido antes de medir
--------------------------------------------
`LiquidityZone` distingue **tres** estados de consumo (ver o docstring da
entidade): `formed_at`, `invalidated_at` (primeiro **pavio** atravessando -- o
grab) e `breached_at` (primeiro **fechamento** alem -- o nivel gasto), mais
`sweep_rejected` e `is_mitigated`.

`_collect_capture_signals` monta `pool_levels` com **todas** as zonas do tipo
cacado e carrega apenas `(nivel, formed_at)`: `invalidated_at` e `breached_at`
sao descartados antes de chegar ao `_raid_signals`. La dentro, a unica condicao
de elegibilidade e `formed_at <= candle.timestamp`. Logo, pelo codigo, **um pool
ja levado continua elegivel para novos raids indefinidamente**, e cada novo pavio
sobre ele vale `_WEIGHT_RAID = 4`.

Bandas de liquidacao **nao entram nisso**: `_raid_signals` so recebe niveis de
EQH/EQL. `LiquidationBand` aparece unicamente em `_band_targets`, no `build()`
vivo, e la o consumo ja e consultado (`end_time`). Por isso este arquivo audita
as bandas em separado e nao lhes aplica a mesma regra -- elas nem participam da
pergunta.

Por que a classificacao nao pode usar a zona
--------------------------------------------
`zone.invalidated_at` e calculado pelo detector sobre a janela visivel inteira,
**incluindo candles posteriores ao raid**. Classificar um raid em T com esse
campo seria ler o futuro -- o mesmo erro que a H1 corrigiu no join HTF. Aqui o
estado do pool e reconstruido candle a candle a partir de `data.candles`, usando
somente candles **anteriores** a T (`pool_state_at`), e um teste de truncamento
exige que a classificacao nao mude quando a serie e cortada logo depois do raid.

Re-raid nao e bug
-----------------
Um pool tocado nao esta necessariamente morto, e chamar tudo de zumbi
esconderia a pergunta. Os estados sao separados:

    INTACT    nenhum pavio anterior atravessou o nivel desde que ele se formou
    PARTIAL   ja houve pavio, mas nenhum fechamento alem -- o nivel foi pago e
              devolvido, e continua sendo um nivel que o mercado defende
    SPENT     ja houve fechamento alem -- deixou de ser pool e virou outra coisa

A identidade do pool e `(tipo, formed_at, nivel)`: um nivel praticamente no
mesmo preco formado depois e **outro** pool, e conta como INTACT porque e.

Um candle de raid pode atravessar varios pools de uma vez. Ele so e classificado
como zumbi quando **todos** os pools que ele tocou ja estavam consumidos -- a
leitura conservadora, que nunca inventa um zumbi onde havia liquidez nova.

Run:
    poetry run python -m research.hunt_pool_lifecycle \\
        --out research/hunt_pool_lifecycle_baseline.json
    poetry run python -m research.hunt_pool_lifecycle \\
        --report-only research/hunt_pool_lifecycle_baseline.json
    poetry run python -m research.hunt_pool_lifecycle --case BTCUSDT:M15
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import fmean, median
from typing import Any

from liquidity_hunter.app.dashboard_data import _HIGHER_TIMEFRAME_MAP, load_dashboard_data
from liquidity_hunter.app.liquidity_hunt import (
    _WEIGHT_RAID,
    LiquidityHuntEngine,
)
from liquidity_hunter.core.domain import (
    Candle,
    LiquidityZoneType,
    RetailPositioning,
)
from research.hunt_htf_causality import TFS, NoFuturesProvider, WindowProvider, fetch_series
from research.hunt_score_redundancy import (
    VISIBLE_LIMIT,
    WINDOW_STEP,
    WINDOWS,
    collect_clusters,
)
from research.hunt_score_variants import engine_episodes, outcome_of, series_span

#: Os tres estados de um pool no instante anterior a um raid.
INTACT = "INTACT"
PARTIAL = "PARTIAL"
SPENT = "SPENT"
STATES = (INTACT, PARTIAL, SPENT)

DISCOVERY_SHARE = 0.7


# ---------------------------------------------------------------------------
# 1. O lifecycle, reconstruido causalmente
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PoolId:
    """A identidade de um pool: tipo, nascimento e nivel.

    Dois niveis quase no mesmo preco nascidos em momentos diferentes sao pools
    diferentes -- o caso (D) do enunciado. Sem isso, um pool novo formado sobre
    as cinzas de um antigo seria contado como re-raid do antigo, e a prevalencia
    de zumbis sairia inflada.
    """

    zone_type: str
    formed_at: str
    level: float


@dataclass(frozen=True)
class PoolState:
    """O estado do pool imediatamente antes de um instante, sem olhar adiante."""

    state: str
    prior_wicks: int
    first_touch: str | None
    breached_at: str | None


def pool_state_at(
    candles: list[Candle],
    level: float,
    formed_at: datetime,
    at: datetime,
    hunted_short: bool,
) -> PoolState:
    """O estado do pool em ``at``, usando somente candles **anteriores** a ``at``.

    O corte e estrito (`c.timestamp < at`): o proprio candle do raid nao pode
    contar como consumo anterior dele mesmo. E o `formed_at` limita por baixo --
    um pavio que atravessou o preco antes do nivel existir nao consumiu nada,
    porque nao havia ordens ali.
    """
    wicks = 0
    first: datetime | None = None
    breached: datetime | None = None
    for candle in candles:
        if candle.timestamp < formed_at or candle.timestamp >= at:
            continue
        through = candle.high > level if hunted_short else candle.low < level
        if not through:
            continue
        wicks += 1
        if first is None:
            first = candle.timestamp
        beyond = candle.close > level if hunted_short else candle.close < level
        if beyond and breached is None:
            breached = candle.timestamp
    if wicks == 0:
        state = INTACT
    elif breached is not None:
        state = SPENT
    else:
        state = PARTIAL
    return PoolState(
        state=state,
        prior_wicks=wicks,
        first_touch=first.isoformat() if first else None,
        breached_at=breached.isoformat() if breached else None,
    )


def pools_of(data: Any, hunted_short: bool) -> list[tuple[PoolId, float, datetime]]:
    """Os niveis que `_collect_capture_signals` entregaria ao `_raid_signals`.

    Reproduz a selecao da producao -- todas as zonas do tipo cacado, com o
    `price_high`/`price_low` conforme o lado -- e acrescenta **so** a identidade,
    que a producao descarta.
    """
    zone_type = (
        LiquidityZoneType.EQUAL_HIGHS if hunted_short else LiquidityZoneType.EQUAL_LOWS
    )
    out = []
    for zone in data.liquidity_zones:
        if zone.zone_type is not zone_type:
            continue
        level = zone.price_high if hunted_short else zone.price_low
        out.append(
            (
                PoolId(zone.zone_type.value, zone.formed_at.isoformat(), level),
                level,
                zone.formed_at,
            )
        )
    return out


def classify_raid(
    candles: list[Candle],
    pools: list[tuple[PoolId, float, datetime]],
    at: datetime,
    hunted_short: bool,
) -> dict[str, Any] | None:
    """Classifica o candle de raid pelos pools que ele de fato atravessou.

    Devolve ``None`` quando o candle nao atravessa pool nenhum -- ou seja,
    quando ele nao seria um raid para a producao tambem.
    """
    candle = next((c for c in candles if c.timestamp == at), None)
    if candle is None:
        return None
    hits = []
    for pid, level, formed_at in pools:
        if formed_at > at:
            continue
        through = (
            candle.high > level and candle.close < level
            if hunted_short
            else candle.low < level and candle.close > level
        )
        if not through:
            continue
        hits.append((pid, pool_state_at(candles, level, formed_at, at, hunted_short)))
    if not hits:
        return None
    estados = [st.state for _pid, st in hits]
    # Conservador: so e zumbi quando NENHUM pool atingido estava intacto.
    raid_state = INTACT if INTACT in estados else (PARTIAL if PARTIAL in estados else SPENT)
    return {
        "at": at.isoformat(),
        "state": raid_state,
        "n_pools": len(hits),
        "estados": dict(Counter(estados)),
        "pools": [
            {"id": [p.zone_type, p.formed_at, p.level], **st.__dict__} for p, st in hits[:4]
        ],
    }


# ---------------------------------------------------------------------------
# 2. A variante
# ---------------------------------------------------------------------------


class ZombieFilterEngine(LiquidityHuntEngine):
    """Producao com **uma** mudanca: raid sobre pool consumido nao pontua.

    Sobrescreve so `_raid_signals`. As zonas continuam no snapshot e no grafico,
    o sinal `zone` continua sendo emitido como sempre, e nenhuma outra fonte e
    tocada -- o que desaparece e a contribuicao de 4 pontos do raid quando o
    pool que ele atravessou ja tinha sido pago.

    O estado e o causal de `pool_state_at`, nao o `invalidated_at` da zona: a
    variante tem de ser implementavel ao vivo para ser uma proposta, e ao vivo
    nao existe o campo calculado sobre a janela inteira.
    """

    #: Estados que ainda pontuam. `PARTIAL` conta como vivo: um nivel pago e
    #: devolvido continua sendo um nivel que o mercado defende, e trata-lo como
    #: morto seria o filtro binario ingenuo que o item 4 manda evitar.
    ALIVE = frozenset({INTACT, PARTIAL})

    @staticmethod
    def _raid_signals(  # type: ignore[override]
        data: Any,
        hunted_short: bool,
        pool_levels: list[tuple[float, datetime]],
        start: datetime,
        end: datetime,
    ) -> list[tuple[datetime, float, str]]:
        if not pool_levels:
            return []
        raids: list[tuple[datetime, float, str]] = []
        for candle in data.candles:
            if not start <= candle.timestamp <= end:
                continue
            for level, formed_at in pool_levels:
                if formed_at > candle.timestamp:
                    continue
                through = (
                    candle.high > level and candle.close < level
                    if hunted_short
                    else candle.low < level and candle.close > level
                )
                if not through:
                    continue
                st = pool_state_at(
                    data.candles, level, formed_at, candle.timestamp, hunted_short
                )
                if st.state in ZombieFilterEngine.ALIVE:
                    raids.append((candle.timestamp, _WEIGHT_RAID, "raid"))
                    break
        return raids


# ---------------------------------------------------------------------------
# 3. Painel
# ---------------------------------------------------------------------------


def _direction_pools(data: Any) -> dict[bool, list[tuple[PoolId, float, datetime]]]:
    return {True: pools_of(data, True), False: pools_of(data, False)}


def analyse_window(
    data: Any, symbol: str, tf: str, window: str, rng: random.Random
) -> dict[str, Any]:
    """Raids, clusters e episodios de uma janela, nos dois bracos."""
    pools = _direction_pools(data)
    raids: list[dict[str, Any]] = []
    clusters = collect_clusters(data, symbol, tf, window)
    for row in clusters:
        hunted_short = row.hunted_side == RetailPositioning.SHORT.value
        for ts, _w, source in row.raw:
            if source != "raid":
                continue
            info = classify_raid(
                data.candles, pools[hunted_short], datetime.fromisoformat(ts), hunted_short
            )
            if info is None:
                continue
            info.update(
                {
                    "symbol": symbol, "tf": tf, "window": window,
                    "stream": row.stream, "up": hunted_short,
                    "cluster_anchor": row.anchor,
                    "cluster_score": row.score,
                    "cluster_sources": sorted(row.by_source),
                    "cluster_passed": row.passed,
                    "threshold": row.threshold,
                    # O raid decide o cluster? (o peso 4 e o que o levou ao limiar)
                    "decisivo": row.passed and (row.score - _WEIGHT_RAID) < row.threshold,
                }
            )
            raids.append(info)

    arms: dict[str, dict[str, dict[str, Any]]] = {}
    outcomes: dict[str, dict[str, Any]] = {}
    for arm, engine in (("prod", LiquidityHuntEngine()), ("filtro", ZombieFilterEngine())):
        eps = engine_episodes(engine, data, symbol, tf)
        arms[arm] = {}
        for key, ep in eps.items():
            ident = f"{symbol}|{tf}|{key.stream}|{key.anchor}"
            arms[arm][ident] = {**ep, "stream": key.stream}
            if ident not in outcomes:
                up = ep["hunted_side"] == RetailPositioning.SHORT.value
                res = outcome_of(
                    data.candles, datetime.fromisoformat(key.anchor), up, rng
                )
                if res is not None:
                    outcomes[ident] = {
                        "symbol": symbol, "tf": tf, "stream": key.stream,
                        "anchor": key.anchor, "up": up, "score": ep["score"],
                        "sources": ep["sources"], **res,
                    }
    return {"raids": raids, "arms": arms, "outcomes": outcomes}


def run_panel(
    symbols: tuple[str, ...], tf_names: tuple[str, ...], seed: int = 7
) -> dict[str, Any]:
    rng = random.Random(seed)
    raids: list[dict[str, Any]] = []
    arms: dict[str, dict[str, dict[str, Any]]] = {"prod": {}, "filtro": {}}
    outcomes: dict[str, dict[str, Any]] = {}
    spans: dict[str, list[str]] = {}
    bands: Counter[str] = Counter()
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
            lo, hi = series_span(series[tf])
            spans[f"{symbol}:{tf_name}"] = [lo.isoformat(), hi.isoformat()]
            for w in range(WINDOWS):
                back = w * WINDOW_STEP
                if back >= len(series[tf]):
                    continue
                try:
                    data = load_dashboard_data(
                        provider=WindowProvider(series, cut=series[tf][-1 - back].timestamp),
                        symbol=symbol, timeframe=tf, limit=VISIBLE_LIMIT,
                        futures_provider=NoFuturesProvider(),
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{symbol} {tf_name} w{w}: {type(exc).__name__}: {exc}")
                    continue
                out = analyse_window(data, symbol, tf_name, f"w{w}", rng)
                raids.extend(out["raids"])
                for arm in ("prod", "filtro"):
                    for ident, ep in out["arms"][arm].items():
                        arms[arm].setdefault(ident, ep)
                for ident, row in out["outcomes"].items():
                    outcomes.setdefault(ident, row)
                # Bandas: auditadas em separado, e a auditoria e de participacao.
                if data.liquidation_map is not None:
                    for band in data.liquidation_map.bands:
                        bands["total"] += 1
                        bands["consumidas" if band.end_time else "vivas"] += 1
            print(f"  {symbol} {tf_name}: raids={len(raids)} eps={len(arms['prod'])}",
                  flush=True)

    return {
        "meta": {
            "symbols": list(symbols), "timeframes": list(tf_names),
            "windows": WINDOWS, "visible_limit": VISIBLE_LIMIT, "seed": seed,
            "futures": "disabled (NoFuturesProvider)",
            "alive_states": sorted(ZombieFilterEngine.ALIVE),
        },
        "raids": raids, "arms": arms, "outcomes": outcomes, "spans": spans,
        "bands": dict(bands), "errors": errors,
    }


# ---------------------------------------------------------------------------
# 4. Relatorio
# ---------------------------------------------------------------------------


def _agg(rows: list[dict[str, Any]], h: int = 20) -> dict[str, float] | None:
    rows = [r for r in rows if f"mfe_{h}" in r]
    if not rows:
        return None
    mfe = fmean(float(r[f"mfe_{h}"]) for r in rows)
    mae = fmean(float(r[f"mae_{h}"]) for r in rows)
    return {
        "n": float(len(rows)), "mfe": mfe, "mae": mae,
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


def _pct(n: float, d: float) -> str:
    return f"{n/d:.1%}" if d else "-"


def _split(payload: dict[str, Any], anchor: str, symbol: str, tf: str) -> str | None:
    span = payload["spans"].get(f"{symbol}:{tf}")
    if not span:
        return None
    lo, hi = datetime.fromisoformat(span[0]), datetime.fromisoformat(span[1])
    total = (hi - lo).total_seconds()
    if total <= 0:
        return None
    pos = (datetime.fromisoformat(anchor) - lo).total_seconds() / total
    return "discovery" if pos < DISCOVERY_SHARE else "holdout"


def report(payload: dict[str, Any]) -> None:  # noqa: PLR0915 - um relatorio por item
    raids = payload["raids"]
    arms, outcomes = payload["arms"], payload["outcomes"]
    print("\n" + "=" * 78)
    print("H3.0 - LIFECYCLE DE POOL: o raid pontua liquidez ja consumida?")
    print("=" * 78)
    meta = payload["meta"]
    print(f"painel: {len(meta['symbols'])} simbolos x {meta['timeframes']} x "
          f"{meta['windows']} janelas | erros: {len(payload['errors'])}")
    print(f"estados que ainda pontuam no filtro: {meta['alive_states']}")

    print(f"\n--- 6. PREVALENCIA (n={len(raids)} sinais de raid) ---")
    por_estado = Counter(r["state"] for r in raids)
    for st in STATES:
        print(f"  {st:8s} {por_estado[st]:6d}  ({_pct(por_estado[st], len(raids))})")
    zumbis = por_estado[SPENT]
    print(f"  ZUMBI (SPENT)  = {_pct(zumbis, len(raids))} dos raids")
    print(f"  pools por candle de raid: mediana "
          f"{median([r['n_pools'] for r in raids]) if raids else 0:.0f}")

    print("\n  por timeframe:")
    for tf in ("M15", "H1", "H4"):
        sel = [r for r in raids if r["tf"] == tf]
        c = Counter(r["state"] for r in sel)
        print(f"    {tf:4s} n={len(sel):5d} | " + " ".join(
            f"{s} {_pct(c[s], len(sel)):>6s}" for s in STATES))
    print("  por direcao:")
    for label, up in (("alta (shorts)", True), ("baixa (longs)", False)):
        sel = [r for r in raids if r["up"] is up]
        c = Counter(r["state"] for r in sel)
        print(f"    {label:14s} n={len(sel):5d} | " + " ".join(
            f"{s} {_pct(c[s], len(sel)):>6s}" for s in STATES))
    print("  por stream:")
    for stream in ("hunt", "continuation"):
        sel = [r for r in raids if r["stream"] == stream]
        c = Counter(r["state"] for r in sel)
        print(f"    {stream:14s} n={len(sel):5d} | " + " ".join(
            f"{s} {_pct(c[s], len(sel)):>6s}" for s in STATES))

    print("\n--- 7. IMPORTANCIA NO SCORE ---")
    aprovados = [r for r in raids if r["cluster_passed"]]
    print(f"  raids em cluster APROVADO: {len(aprovados)} de {len(raids)}")
    for st in STATES:
        sel = [r for r in aprovados if r["state"] == st]
        dec = [r for r in sel if r["decisivo"]]
        print(f"    {st:8s} aprovados {len(sel):5d} | DECISIVOS (cluster cai sem o "
              f"raid) {len(dec):5d} ({_pct(len(dec), len(sel))})")
    dec_zumbi = [r for r in aprovados if r["state"] == SPENT and r["decisivo"]]
    print(f"  clusters aprovados que dependem de raid ZUMBI: {len(dec_zumbi)}")

    print("\n--- 8. OUTCOME POR ESTADO DO POOL (h=20) ---")
    by_anchor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in raids:
        by_anchor[f"{r['symbol']}|{r['tf']}|{r['stream']}|{r['cluster_anchor']}"].append(r)
    for st in STATES:
        rows = [
            outcomes[i] for i, rs in by_anchor.items()
            if i in outcomes and any(r["state"] == st for r in rs)
        ]
        print(f"  {st:8s} {_fmt(_agg(rows))}")

    print("\n--- 10. DIFF DE STREAM: producao vs ZOMBIE_FILTER ---")
    for stream in ("hunt", "continuation"):
        base = {i for i in arms["prod"] if f"|{stream}|" in i}
        var = {i for i in arms["filtro"] if f"|{stream}|" in i}
        gone, novos = base - var, var - base
        score_muda = [
            i for i in base & var
            if arms["prod"][i]["score"] != arms["filtro"][i]["score"]
        ]
        src_muda = [
            i for i in base & var
            if arms["prod"][i]["sources"] != arms["filtro"][i]["sources"]
        ]
        ts_muda = [
            i for i in base & var
            if arms["prod"][i]["start"] != arms["filtro"][i]["start"]
        ]
        print(f"  {stream}: {len(base)} -> {len(var)} "
              f"(coverage {_pct(len(var), len(base))})")
        print(f"    identicos {len(base & var) - len(score_muda) - len(src_muda):5d} | "
              f"removidos {len(gone):4d} | surgem {len(novos):4d} | "
              f"score muda {len(score_muda):4d} | sources muda {len(src_muda):4d} | "
              f"inicio desloca {len(ts_muda):4d}")
        b = _agg([outcomes[i] for i in base if i in outcomes])
        v = _agg([outcomes[i] for i in var if i in outcomes])
        g = _agg([outcomes[i] for i in gone if i in outcomes])
        print(f"    producao  {_fmt(b)}")
        print(f"    filtro    {_fmt(v)}")
        print(f"    REMOVIDOS {_fmt(g)}")
        if b and v and gone:
            print("\n    --- 11. DISCOVERY / HOLDOUT (temporal) ---")
            for split in ("discovery", "holdout"):
                bb = _agg([
                    outcomes[i] for i in base
                    if i in outcomes
                    and _split(payload, outcomes[i]["anchor"], outcomes[i]["symbol"],
                               outcomes[i]["tf"]) == split
                ])
                vv = _agg([
                    outcomes[i] for i in var
                    if i in outcomes
                    and _split(payload, outcomes[i]["anchor"], outcomes[i]["symbol"],
                               outcomes[i]["tf"]) == split
                ])
                delta = f"{vv['win'] - bb['win']:+.1%}" if bb and vv else "-"
                print(f"      [{split:9s}] prod {_fmt(bb)}")
                print(f"      [{split:9s}] filt {_fmt(vv)}  delta {delta}")

    print("\n--- 13. BANDAS DE LIQUIDACAO ---")
    print("  `_raid_signals` recebe SO niveis de EQH/EQL: nenhuma banda participa")
    print("  do raid. `LiquidationBand` aparece apenas em `_band_targets`, no")
    print("  `build()` vivo, e la o consumo JA e consultado (`end_time`), inclusive")
    print("  contra o flip da perna. Lifecycle diferente, pergunta diferente.")
    b = payload["bands"]
    if b:
        print(f"  observadas no painel: {b.get('total', 0)} bandas "
              f"({b.get('vivas', 0)} vivas, {b.get('consumidas', 0)} consumidas)")

    print("\n--- 15/17. RE-RAID LEGITIMO ---")
    partial = [r for r in raids if r["state"] == PARTIAL]
    rows_p = [
        outcomes[i] for i, rs in by_anchor.items()
        if i in outcomes and any(r["state"] == PARTIAL for r in rs)
    ]
    print(f"  raids sobre pool pago-e-devolvido (PARTIAL): {len(partial)} "
          f"({_pct(len(partial), len(raids))})")
    print(f"    {_fmt(_agg(rows_p))}")
    print("  Se PARTIAL mede como INTACT, o lifecycle precisa dos tres estados e")
    print("  um filtro binario 'tocado = morto' estaria errado.")


def case_report(symbol: str, tf_name: str) -> None:
    """Os casos do item 12, com identidade de pool e primeiro consumo."""
    tf = TFS[tf_name]
    data = load_dashboard_data(
        symbol=symbol, timeframe=tf, limit=VISIBLE_LIMIT,
        futures_provider=NoFuturesProvider(),
    )
    rng = random.Random(1)
    out = analyse_window(data, symbol, tf_name, "live", rng)
    raids, outcomes = out["raids"], out["outcomes"]
    print(f"\n=== {symbol} {tf_name}: {len(raids)} sinais de raid ===")
    for titulo, sel in (
        ("raid INTACT", [r for r in raids if r["state"] == INTACT]),
        ("re-raid PARTIAL (pago e devolvido)", [r for r in raids if r["state"] == PARTIAL]),
        ("raid ZUMBI (SPENT) que NAO decide",
         [r for r in raids if r["state"] == SPENT and not r["decisivo"]]),
        ("raid ZUMBI (SPENT) que DECIDE o limiar",
         [r for r in raids if r["state"] == SPENT and r["decisivo"]]),
    ):
        print(f"\n  -- {titulo} --")
        if not sel:
            print("    (nenhum)")
        for r in sel[:3]:
            ident = f"{symbol}|{tf_name}|{r['stream']}|{r['cluster_anchor']}"
            res = outcomes.get(ident)
            desfecho = (
                f"MFE {res['mfe_20']:.2f} MAE {res['mae_20']:.2f} "
                f"{'ACERTOU' if res['win_20'] else 'errou'}"
                if res and "mfe_20" in res else "sem horizonte"
            )
            pool = r["pools"][0]
            veredito = "APROVADO" if r["cluster_passed"] else "reprovado"
            print(f"    raid {r['at']} {r['stream']:12s} "
                  f"score={r['cluster_score']:5.1f} "
                  f"[{' '.join(r['cluster_sources'])}] {veredito}")
            print(f"      pool nivel={pool['id'][2]:.6g} formado={pool['id'][1]} "
                  f"| pavios anteriores={pool['prior_wicks']} "
                  f"1o toque={pool['first_touch']} breach={pool['breached_at']}")
            print(f"      -> {desfecho}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report-only", type=Path)
    parser.add_argument("--symbols", type=int)
    parser.add_argument("--tfs", default="M15,H1,H4")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--case", help="SIMBOLO:TF")
    args = parser.parse_args()

    from research._symbols import UNIVERSE

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
