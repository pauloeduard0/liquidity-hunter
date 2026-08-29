"""Por que o anchor estrutural pula, e qual regra o segura.

`_structural_anchor_index` decide onde o `InternalStructureDetector` comeca a
detectar, e a docstring promete estabilidade entre refreshes. Ela nao se
sustenta: o *extremo* e um ponto de preco fixo, mas a **regiao** onde ele e
procurado -- `[visible_start - 300, visible_start)` -- desliza com a janela.
Cada mudanca de anchor re-semeia o bootstrap e reescreve o stream; e a causa
dominante do churn retroativo medido em `research/atr_window_stability.py`
(36,8% dos refreshes reescrevem estrutura ja resolvida).

Metrica barata: taxa de salto do anchor (nao roda o detector). Offline.

Tres perguntas, nesta ordem:

1. **Por que ele pula** (`--decompose`). Um salto e o extremo saindo pela
   esquerda da regiao, uma alternancia low<->high, ou uma vela mais recente
   entrando pela direita e sendo um extremo novo?
2. **Alguma regra pura resolve?** Regiao maior, extremo dominante em vez do
   mais recente, borda quantizada.
3. **E com estado?** Manter o anchor anterior enquanto ele continuar dentro
   da regiao (histerese), e a tentativa de obter o mesmo efeito sem estado
   rolando a histerese dentro da propria janela.

Resultado (12 simbolos x 15m/1h/4h, 60 refreshes de 24 velas), em
`docs/structure_decisions.md`: nenhuma regra pura melhora, a histerese com
estado corta de ~30% para ~9,5%, e a histerese rolada da 100% -- ela herda o
ponto de partida, que e a borda esquerda da janela, que e justamente o que
desliza. A conclusao e que estabilizar isso exige estado persistido por
(simbolo, timeframe); nao ha regra pura da janela que o faca.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable

from liquidity_hunter.app.dashboard_data import _STRUCTURAL_ANCHOR_REGION, _structural_anchor_index
from liquidity_hunter.core.domain import Candle, TimeFrame
from research.atr_window_stability import BUFFER, CACHE_DIR, STEP, VISIBLE, load_series

REGION = _STRUCTURAL_ANCHOR_REGION
Rule = Callable[[list[Candle], int], int]


def _region(candles: list[Candle], vsi: int, size: int) -> tuple[list[Candle], int]:
    off = max(0, vsi - size)
    return candles[off:vsi], off


def _pick_recent(region: list[Candle]) -> int:
    lo = min(range(len(region)), key=lambda j: region[j].low)
    hi = max(range(len(region)), key=lambda j: region[j].high)
    return lo if lo > hi else hi


def current(candles: list[Candle], vsi: int) -> int:
    return _structural_anchor_index(candles, candles[vsi].timestamp)


def wider(size: int) -> Rule:
    """A regra atual olhando mais para tras.

    Nota: com o wiring de producao (`limit=1200`, buffer 300, teto de 1500 do
    provider) a regiao JA e todo o buffer, entao 600 e 900 nao podem olhar
    mais longe -- os numeros saem identicos ao baseline por construcao, e
    ampliar de verdade exigiria encolher a janela visivel.
    """

    def rule(candles: list[Candle], vsi: int) -> int:
        region, off = _region(candles, vsi, size)
        return off + _pick_recent(region) if region else 0

    return rule


def dominant(size: int = REGION) -> Rule:
    """Escolhe o extremo mais proeminente em vez do mais recente."""

    def rule(candles: list[Candle], vsi: int) -> int:
        region, off = _region(candles, vsi, size)
        if not region:
            return 0
        lo = min(range(len(region)), key=lambda j: region[j].low)
        hi = max(range(len(region)), key=lambda j: region[j].high)
        ref = candles[vsi].open
        return off + (lo if (ref - region[lo].low) >= (region[hi].high - ref) else hi)

    return rule


def quantized(step: int) -> Rule:
    """A regra atual sobre uma borda quantizada -- dilui, nao corrige."""

    def rule(candles: list[Candle], vsi: int) -> int:
        region, off = _region(candles, (vsi // step) * step, REGION)
        return off + _pick_recent(region) if region else 0

    return rule


def rolled(candles: list[Candle], vsi: int) -> int:
    """Histerese rolada dentro da janela: a tentativa de histerese SEM estado.

    Mantem o anchor enquanto ele continuar dentro da regiao movel, rolando da
    primeira vela da janela ate `vsi`. Falha por construcao: o valor inicial e
    fixado na borda esquerda da janela, que desliza -- e a histerese entao
    propaga esse ponto instavel ate o fim em vez de amortece-lo.
    """
    held: int | None = None
    for i in range(1, vsi + 1):
        off = max(0, i - REGION)
        if held is not None and held >= off:
            continue
        region = candles[off:i]
        if region:
            held = off + _pick_recent(region)
    return held or 0


PURE_RULES: dict[str, Rule] = {
    "atual (300, mais recente)": current,
    "regiao 600": wider(600),
    "regiao 900": wider(900),
    "dominante (300)": dominant(300),
    "quantizado 24": quantized(24),
    "histerese rolada (pura)": rolled,
}


def windows(series: list[Candle], refreshes: int):
    span = VISIBLE + BUFFER
    if len(series) < span + STEP * refreshes:
        return
    for k in range(refreshes):
        end = len(series) - STEP * (refreshes - k)
        window = series[end - span : end]
        yield window, len(window) - VISIBLE


def measure_pure(names: list[str], tf: TimeFrame, refreshes: int) -> dict[str, list[int]]:
    per = {k: [0, 0] for k in PURE_RULES}
    for symbol in names:
        try:
            series = load_series(symbol, tf)
        except FileNotFoundError:
            continue
        prev: dict[str, object] = {}
        for window, vsi in windows(series, refreshes):
            for name, rule in PURE_RULES.items():
                ts = window[rule(window, vsi)].timestamp
                if name in prev:
                    per[name][1] += 1
                    per[name][0] += ts != prev[name]
                prev[name] = ts
    return per


def measure_hysteresis(names: list[str], tf: TimeFrame, refreshes: int) -> list[int]:
    """Histerese COM estado: guarda o anchor do refresh anterior."""
    jumps = total = 0
    for symbol in names:
        try:
            series = load_series(symbol, tf)
        except FileNotFoundError:
            continue
        prev = None
        held = None
        for window, vsi in windows(series, refreshes):
            region, off = _region(window, vsi, REGION)
            keep = held is not None and any(c.timestamp == held for c in region)
            ts = held if keep else window[off + _pick_recent(region)].timestamp
            if prev is not None:
                total += 1
                jumps += ts != prev
            prev = held = ts
    return [jumps, total]


def decompose(names: list[str], tf: TimeFrame, refreshes: int) -> tuple[int, ...]:
    left = alt = fresh = jump = total = 0
    for symbol in names:
        try:
            series = load_series(symbol, tf)
        except FileNotFoundError:
            continue
        prev = None
        for window, vsi in windows(series, refreshes):
            region, off = _region(window, vsi, REGION)
            idx = _pick_recent(region)
            lo = min(range(len(region)), key=lambda j: region[j].low)
            anchor, side = region[idx].timestamp, ("low" if idx == lo else "high")
            if prev is not None:
                total += 1
                if anchor != prev[0]:
                    jump += 1
                    if not any(c.timestamp == prev[0] for c in region):
                        left += 1
                    elif side != prev[1]:
                        alt += 1
                    else:
                        fresh += 1
            prev = (anchor, side)
    return jump, total, left, alt, fresh


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframes", default="15m,1h,4h")
    ap.add_argument("--symbols", type=int, default=12)
    ap.add_argument("--refreshes", type=int, default=60)
    ap.add_argument("--decompose", action="store_true")
    args = ap.parse_args()

    names = sorted({p.name.rsplit("_", 1)[0] for p in CACHE_DIR.glob("*_1h.json")})[: args.symbols]

    for tf_name in args.timeframes.split(","):
        tf = TimeFrame(tf_name)
        print(f"\n=== {tf_name}")
        if args.decompose:
            jump, total, left, alt, fresh = decompose(names, tf, args.refreshes)


            def pct(n: int, total: int = jump) -> str:
                return f"{100 * n / total:.0f}%" if total else "-"

            print(f"  saltos {jump}/{total} = {100*jump/total:.1f}%")
            print(f"    saiu pela esquerda        {left:>4} ({pct(left)})")
            print(f"    alternou low<->high       {alt:>4} ({pct(alt)})")
            print(f"    extremo novo, mesmo lado  {fresh:>4} ({pct(fresh)})")
            continue
        per = measure_pure(names, tf, args.refreshes)
        per["histerese (com estado)"] = measure_hysteresis(names, tf, args.refreshes)
        print(f"{'regra':<28}{'saltos':>8}{'refresh':>9}{'% salto':>10}")
        for name, (jumps, total) in per.items():
            if not total:
                print(f"{name:<28}{'sem dados':>8}")
                continue
            print(f"{name:<28}{jumps:>8}{total:>9}{100*jumps/total:>9.1f}%")


if __name__ == "__main__":
    main()
