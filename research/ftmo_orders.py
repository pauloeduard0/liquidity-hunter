"""A fila de ordens: transforma decisao do diario em intencao de ordem.

Este e o lado WSL do robo, e ele **nao manda nada**. Ele le o diario que
`ftmo_live` acabou de escrever, converte cada decisao nova de R para lotes
(`ftmo_sizing`) e escreve uma linha numa fila que o lado Windows consome.

A divisao nao e burocracia. A deteccao e o codigo que foi MEDIDO -- os gates,
o reclaim, o stop no extremo testado -- e ela vive aqui, no mesmo Python que
produziu `docs/block_reclaim.md`. Quem manda a ordem e
`research/mt5_trader.py`, que roda no Windows, nao importa nada do projeto e
nao decide nada: le a fila, confere os limites e chama `order_send`. Se a
regra mudar, muda de um lado so; se o executor tiver um bug, ele nao consegue
inventar uma entrada que a regra nao pediu.

**A fila e o registro.** Nao ha estado separado de "o que ja foi enfileirado":
a propria `orders.jsonl` responde isso, o que torna a passada idempotente sem
um segundo arquivo para dessincronizar. A `fills.jsonl` que volta do Windows
fecha o circuito -- e dela que sai a derrapagem REAL, medida contra preco de
execucao em vez de contra o proximo close.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from liquidity_hunter.app.paper_journal import read_journal
from liquidity_hunter.core.domain import MarketDirection, PaperOutcome
from research._mt5 import EXPORT_DIR
from research.ftmo_live import JOURNAL_PATH, MAX_SIGNAL_AGE, server_offset
from research.ftmo_sizing import ACCOUNT_BALANCE, load_info, position_size
from research.ftmo_universe import RISK_PER_TRADE

#: A fila fica no diretorio compartilhado do exportador, que os dois lados ja
#: enxergam -- o WSL por `/mnt/c`, o Windows por `C:\`. Uma pasta a mais so
#: acrescentaria um caminho para configurar errado.
ORDERS_NAME = "orders.jsonl"
FILLS_NAME = "fills.jsonl"


def queued_keys(orders: Path) -> set[str]:
    """As decisoes que ja viraram intencao. A fila e a propria memoria."""
    if not orders.exists():
        return set()
    keys = set()
    for line in orders.read_text(encoding="utf-8").splitlines():
        if line.strip():
            keys.add(json.loads(line)["key"])
    return keys


def build_intent(decision, info: dict, balance: float, risk: float) -> dict | None:
    """Uma decisao do diario vira uma ordem pronta para mandar, ou nada.

    `None` quando o simbolo nao esta na ficha do exportador ou quando o lote
    ideal fica abaixo do minimo da corretora: nesse caso a unica ordem que
    existe arrisca MAIS do que o plano pediu, e pegar uma operacao fora do
    tamanho e pior do que nao pegar.
    """
    spec = info.get(decision.symbol)
    if spec is None:
        return None
    sizing = position_size(
        entry=decision.observed_price, stop=decision.stop_price,
        balance=balance, info=spec, risk=risk,
    )
    if sizing is None or not sizing.takeable:
        return None
    return {
        "key": decision.key,
        "symbol": decision.symbol,
        "side": "buy" if decision.direction is MarketDirection.BULLISH else "sell",
        "lots": sizing.lots,
        "stop": decision.stop_price,
        "target": decision.target_price,
        # Levados junto para o executor poder recusar sozinho o que envelheceu
        # entre a decisao e a proxima volta dele.
        "signal_timestamp": decision.signal_timestamp.isoformat(),
        "timeframe": decision.timeframe.value,
        "reference_price": decision.observed_price,
        "risk_pct": sizing.risk_pct,
        "queued_at": datetime.now(UTC).isoformat(),
    }


def enqueue(*, journal: Path, export: Path, balance: float, risk: float,
            provider=None) -> list[dict]:
    """Enfileira toda decisao ABERTA do diario que ainda nao foi enfileirada.

    So as abertas: uma decisao ja liquidada pelo diario e historia, e mandar
    ordem para ela seria entrar num trade que ja acabou. E so as recentes --
    a mesma idade maxima que o diario usa, porque uma intencao velha mede
    deriva de preco e nao derrapagem.
    """
    orders = export / ORDERS_NAME
    known = queued_keys(orders)
    info = load_info(export)
    offset = server_offset(provider) if provider is not None else None
    now = datetime.now(UTC)
    fresh: list[dict] = []
    for decision in read_journal(journal):
        if decision.outcome is not PaperOutcome.OPEN or decision.key in known:
            continue
        if offset is not None:
            closed_at = decision.signal_timestamp - offset
            if now - closed_at > MAX_SIGNAL_AGE:
                continue
        intent = build_intent(decision, info, balance, risk)
        if intent is not None:
            fresh.append(intent)
    if fresh:
        with orders.open("a", encoding="utf-8") as fh:
            for intent in fresh:
                fh.write(json.dumps(intent) + "\n")
    return fresh


def read_fills(export: Path) -> list[dict]:
    path = export / FILLS_NAME
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def report_fills(export: Path) -> str:
    """A derrapagem REAL: preco de execucao contra o preco que a regra viu.

    E este o numero que o diario em papel so podia estimar. Ele sai em R
    porque e assim que o custo entra na conta do plano: os mesmos pontos-base
    doem o dobro num stop com metade da largura.
    """
    fills = read_fills(export)
    done = [f for f in fills if f.get("status") == "filled"]
    if not done:
        return "sem execucoes ainda"
    lines = [f"{len(done)} execucoes"]
    slips = []
    for fill in done:
        reference = fill["reference_price"]
        distance = abs(reference - fill["stop"])
        signed = fill["price"] - reference
        if fill["side"] == "sell":
            signed = -signed
        slips.append(signed / distance if distance > 0 else 0.0)
    slips.sort()
    mid = slips[len(slips) // 2]
    lines.append(f"  derrapagem mediana {mid:+.3f}R"
                 f"   (pior {slips[-1]:+.3f}R, melhor {slips[0]:+.3f}R)")
    rejected = [f for f in fills if f.get("status") != "filled"]
    if rejected:
        lines.append(f"  {len(rejected)} recusadas pelo executor")
        for fill in rejected[-5:]:
            lines.append(f"    {fill['symbol']:<12} {fill.get('status')}"
                         f"  {fill.get('detail', '')}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", default=str(EXPORT_DIR))
    parser.add_argument("--journal", default=str(JOURNAL_PATH))
    parser.add_argument("--balance", type=float, default=ACCOUNT_BALANCE)
    parser.add_argument("--risk", type=float, default=RISK_PER_TRADE)
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()

    export = Path(args.export)
    if not args.report_only:
        from research._mt5 import MT5CsvProvider
        fresh = enqueue(
            journal=Path(args.journal), export=export,
            balance=args.balance, risk=args.risk,
            provider=MT5CsvProvider(export),
        )
        print(f"enfileiradas {len(fresh)}"
              f"   (risco {args.risk:.2%} de {args.balance:,.0f})")
        for intent in fresh:
            print(f"  {intent['symbol']:<12} {intent['side']:<4}"
                  f" {intent['lots']:>8.2f} lotes"
                  f"   stop {intent['stop']:.5f}  alvo {intent['target']:.5f}")
    print()
    print(report_fills(export))


if __name__ == "__main__":
    main()
