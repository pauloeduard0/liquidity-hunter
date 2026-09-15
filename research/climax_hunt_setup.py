"""K4: clímax VSA contra o hunt (A) e Block Reclaim de stop largo (B), H1.

De onde vem
-----------
Exemplo do usuário: BTCUSDT H1, 13/09 21:00 UTC-3. O gráfico mostra a caixa
CONT começando ali, mas no replay ao vivo o CONT só aparece no fechamento das
01:00 (5 candles e ~1.300 pontos depois) -- a caixa é redesenhada para trás
quando confirma (atraso mediano medido no K1: 3 candles, p90 21). O que
EXISTIA às 21:00: selling climax (VSA), H4 de alta com a perna H1 contra
(HUNT ativo) e um Block Reclaim de alta com r_atr 1,43, fora do gate.

Dois setups, fixados ANTES de rodar
------------------------------------
Contexto ao vivo reaproveitado do K2 (`.replay_cache/hunt_reclaim_setup`,
H1, 3000 candles por símbolo, cortes 500..2999): HTF com o candle H4 em
formação removido e a perna LTF pelo replay dos eventos confirmados. VSA por
`classify_candle` (janela só para trás, sem o dedup que olha vizinhos).

    A0   selling climax com HTF bullish (buying climax com HTF bearish).
         entrada close[i], stop no extremo do candle i, alvo 2R.
    A1   A0 + perna LTF CONTRA a HTF (HUNT ativo)            <- setup A
    A1c  A0 + perna LTF A FAVOR da HTF                       (referência)

    Bref Block Reclaim H1 com os gates de produção (r_atr <= 1,0,
         vwap_candles >= 1, não atravessou o bloco, penetração < 50%).
    B0   o mesmo com 1,0 < r_atr <= 2,0                       (stop largo)
    B1   B0 + clímax VSA na direção do trade em algum candle da visita
         (test_start .. gatilho)                             <- setup B
    B2   B0 + direção = HTF e perna LTF contra a HTF         <- setup B (hunt)

Régua: a do K2 -- horizonte 60 candles, custo 0,13% ida e volta em R,
stop e alvo no mesmo candle contam como stop. Controle casado: mesma série,
mesma direção, mesmo r_atr, entradas aleatórias.

Critério de aprovação
---------------------
Em DISCOVERY e HOLDOUT (70/30 temporal por série): net > 0, net > controle,
holdout n >= 30. A1 precisa bater A0; B1/B2 precisam bater B0, nas duas
amostras. Reportado também r_atr <= 1 / > 1 e trades/mês (informativo).

Ressalva estrutural: a janela é ~104 dias (jun-set 2026), um regime só.
Aprovado aqui => walk-forward em janela longa antes de qualquer uso.

Run:
    poetry run python -m research.climax_hunt_setup --out research/climax_hunt_setup_baseline.json
    poetry run python -m research.climax_hunt_setup \\
        --report-only research/climax_hunt_setup_baseline.json
"""

from __future__ import annotations

import argparse
import json
import random
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Any

from liquidity_hunter.app.block_reclaim import detect_block_reclaims
from liquidity_hunter.app.dashboard_data import _HIGHER_TIMEFRAME_MAP, load_dashboard_data
from liquidity_hunter.app.paper_journal import (
    MAX_BLOCK_PENETRATION,
    test_penetrated_block_deeply,
    test_pierced_the_block,
)
from liquidity_hunter.core.domain import MarketDirection, TimeFrame, VSAPattern
from liquidity_hunter.indicators import ema_series, volume_delta_series
from liquidity_hunter.psychology import VolumeSpreadAnalyzer
from research._symbols import UNIVERSE
from research.hunt_htf_causality import NoFuturesProvider, WindowProvider
from research.hunt_knowability import TRADE_HORIZON, causal_atr, trade_r
from research.hunt_reclaim_setup import (
    CACHE_DIR,
    CONTROL_DRAWS,
    DISCOVERY_SHARE,
    MIN_HOLDOUT,
    VISIBLE,
    _fmt,
    _stats,
    net_r,
)
from research.quality_features import CachedProvider

TF = TimeFrame.H1
FETCH = 3000
MAX_WIDE = 2.0
ARMS = ("A0", "A1", "A1c", "Bref", "B0", "B1", "B2")


def load_window(provider: CachedProvider, symbol: str) -> tuple[dict, dict] | None:
    """A série exata do K2: termina no candle carimbado no nome do cache."""
    files = sorted(CACHE_DIR.glob(f"{symbol}_H1_{FETCH}_*.json"))
    if not files:
        return None
    stamp = datetime.strptime(files[-1].stem.rsplit("_", 1)[1], "%Y%m%d%H%M").replace(tzinfo=UTC)
    ctx = json.loads(files[-1].read_text())
    htf = _HIGHER_TIMEFRAME_MAP[TF]
    full = provider.get_ohlcv(symbol, TF, 60_000)
    end = next((k for k, c in enumerate(full) if c.timestamp == stamp), None)
    if end is None or end + 1 < FETCH:
        return None
    candles = full[end + 1 - FETCH : end + 1]
    gap = any(
        (b.timestamp - a.timestamp).total_seconds() != 3600
        for a, b in zip(candles, candles[1:], strict=False)
    )
    if gap:
        return None
    htf_full = [c for c in provider.get_ohlcv(symbol, htf, 60_000) if c.timestamp <= stamp]
    return {TF: candles, htf: htf_full[-FETCH:]}, ctx


def _climax_for(up: bool) -> VSAPattern:
    return VSAPattern.SELLING_CLIMAX if up else VSAPattern.BUYING_CLIMAX


def analyse(symbol: str, seed: int) -> list[dict[str, Any]]:
    loaded = load_window(CachedProvider(), symbol)
    if loaded is None:
        return []
    series, ctx = loaded
    candles = series[TF]
    n = len(candles)
    last = n - TRADE_HORIZON - 1
    span = last - VISIBLE
    days = (candles[last].timestamp - candles[VISIBLE].timestamp).total_seconds() / 86400
    atrs = causal_atr(candles)
    vd = volume_delta_series(candles)
    vsa = VolumeSpreadAnalyzer()
    patterns = [None] * n
    for i in range(n):
        sig = vsa.classify_candle(candles, vd, i)
        patterns[i] = sig.pattern if sig else None
    rng = random.Random(f"{seed}|{symbol}")
    idx = {c.timestamp: i for i, c in enumerate(candles)}

    def control(up: bool, r_atr: float) -> float | None:
        vals = []
        for _ in range(CONTROL_DRAWS):
            j = rng.randrange(VISIBLE, last)
            if atrs[j]:
                v = net_r(candles, j, up, r_atr * atrs[j])
                if v is not None:
                    vals.append(v)
        return fmean(vals) if vals else None

    def row(i: int, up: bool, risk: float, arms: list[str], kind: str) -> dict[str, Any] | None:
        net = net_r(candles, i, up, risk)
        if net is None or not atrs[i]:
            return None
        r_atr = risk / atrs[i]
        return {
            "symbol": symbol, "i": i, "kind": kind, "up": up, "arms": arms,
            "ts": candles[i].timestamp.isoformat(), "r_atr": r_atr, "net": net,
            "gross": trade_r(candles, i, up, risk, 2.0),
            "ctl_net": control(up, r_atr),
            "split": "discovery" if (i - VISIBLE) / span < DISCOVERY_SHARE else "holdout",
            "days": days,
        }

    rows: list[dict[str, Any]] = []
    # --- A: clímax a favor da HTF
    for i in range(VISIBLE, last):
        side = ctx["htf"][i]
        if side not in ("bullish", "bearish"):
            continue
        up = side == "bullish"
        if patterns[i] is not _climax_for(up):
            continue
        c = candles[i]
        risk = (c.close - c.low) if up else (c.high - c.close)
        if risk <= 0:
            continue
        arms = ["A0"]
        ltf = ctx["ltf"][i]
        if ltf in ("bullish", "bearish"):
            arms.append("A1" if ltf != side else "A1c")
        r = row(i, up, risk, arms, "climax")
        if r:
            rows.append(r)

    # --- B: Block Reclaim, uma chamada com a série inteira (como quality_features)
    data = load_dashboard_data(
        provider=WindowProvider(series), symbol=symbol, timeframe=TF, limit=FETCH,
        futures_provider=NoFuturesProvider(),
    )
    if data.vwap is None or len(data.candles) != n:
        return rows
    reclaims = detect_block_reclaims(
        candles, data.poi_zones, data.vwap, symbol=symbol, timeframe=TF,
        ema=ema_series(candles, 9),
    )
    for rec in reclaims:
        i = idx.get(rec.timestamp)
        if rec.provisional or i is None or not VISIBLE <= i < last or rec.r_atr is None:
            continue
        if rec.vwap_candles < 1 or test_pierced_the_block(rec):
            continue
        if test_penetrated_block_deeply(rec, max_fraction=MAX_BLOCK_PENETRATION[TF]):
            continue
        up = rec.direction is MarketDirection.BULLISH
        c = candles[i]
        risk = (c.close - rec.test_extreme) if up else (rec.test_extreme - c.close)
        if risk <= 0:
            continue
        if rec.r_atr <= 1.0:
            arms = ["Bref"]
        elif rec.r_atr <= MAX_WIDE:
            arms = ["B0"]
            start = idx.get(rec.test_start_timestamp, i)
            if any(patterns[k] is _climax_for(up) for k in range(start, i + 1)):
                arms.append("B1")
            side, ltf = ctx["htf"][i], ctx["ltf"][i]
            if side == ("bullish" if up else "bearish") and ltf in ("bullish", "bearish") \
                    and ltf != side:
                arms.append("B2")
        else:
            continue
        r = row(i, up, risk, arms, "reclaim")
        if r:
            rows.append(r)
    return rows


def run(symbols: tuple[str, ...], seed: int, workers: int) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    spans: dict[str, float] = {}
    errors: list[str] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(analyse, s, seed): s for s in symbols}
        for done, fut in enumerate(as_completed(futs), 1):
            s = futs[fut]
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{s}: {type(exc).__name__}: {exc}")
                continue
            rows.extend(res)
            if res:
                spans[s] = res[0]["days"]
            else:
                errors.append(f"{s}: sem janela alinhada")
            print(f"  [{done}/{len(symbols)}] {s}: {len(res)} linhas", flush=True)
    return {
        "meta": {"generated": datetime.now(tz=UTC).isoformat(), "seed": seed,
                 "symbols": list(symbols), "max_wide": MAX_WIDE},
        "rows": rows, "spans": spans, "errors": errors,
    }


def report(payload: dict[str, Any]) -> None:
    rows, spans = payload["rows"], payload["spans"]
    total_days = sum(spans.values()) / max(len(spans), 1)
    print(f"\n{'=' * 78}\nK4 - CLIMAX CONTRA O HUNT (A) e BLOCK RECLAIM LARGO (B), H1\n{'=' * 78}")
    print(f"simbolos com janela: {len(spans)} | ~{total_days:.0f} dias cada | "
          f"erros: {len(payload['errors'])}")
    base: dict[tuple[str, str], dict | None] = {}
    verdict = {}
    for arm in ARMS:
        sel = [r for r in rows if arm in r["arms"]]
        month = len(sel) / max(total_days, 1) * 30
        print(f"\n  {arm:4s} todos      {_fmt(_stats(sel))}  ~{month:.0f}/mes (universo)")
        if arm.startswith("A"):
            print(f"       r_atr<=1   {_fmt(_stats([r for r in sel if r['r_atr'] <= 1]))}")
            print(f"       r_atr>1    {_fmt(_stats([r for r in sel if r['r_atr'] > 1]))}")
        ok = True
        for split in ("discovery", "holdout"):
            st = _stats([r for r in sel if r["split"] == split])
            print(f"       [{split:9s}] {_fmt(st)}")
            base[(arm, split)] = st
            if st is None or st["net"] <= 0 or st["net"] <= st["ctl"]:
                ok = False
            if split == "holdout" and (st is None or st["n"] < MIN_HOLDOUT):
                ok = False
            ref = {"A1": "A0", "B1": "B0", "B2": "B0"}.get(arm)
            if ref:
                b = base.get((ref, split))
                if st is None or b is None or st["net"] <= b["net"]:
                    ok = False
        verdict[arm] = ok
        print(f"       => {'APROVADO' if ok else 'reprovado'}")
    approved = ", ".join(a for a, v in verdict.items() if v)
    print("\n  RESUMO: " + (approved or "nenhum braco aprovado"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report-only", type=Path)
    parser.add_argument("--symbols", type=int, default=len(UNIVERSE))
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--workers", type=int, default=7)
    args = parser.parse_args()
    if args.report_only:
        report(json.loads(args.report_only.read_text()))
        return
    payload = run(UNIVERSE[: args.symbols], args.seed, args.workers)
    if args.out:
        args.out.write_text(json.dumps(payload, default=str))
        print(f"\nescrito: {args.out}")
    report(payload)


if __name__ == "__main__":
    main()
