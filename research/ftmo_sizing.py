"""De R para lotes: o tamanho da ordem que o plano implica.

O estudo inteiro vive em **R** -- a distancia ate o stop -- e isso e de
proposito: o percentual arriscado cancela na conta de custo, entao medir em R
deixa a conclusao independente do tamanho da conta. Mas na hora de mandar a
ordem alguem tem que converter, e a conversao tem duas armadilhas que o R
esconde:

* **O lote e discreto.** `volume_step` costuma ser 0,01, entao o risco real e
  o alvo arredondado para baixo -- nunca exatamente 0,25%.
* **`volume_min` e um piso.** Abaixo dele nao existe ordem, e quem insiste
  arrisca MAIS do que pretendia. Numa conta pequena isso deixa de ser
  arredondamento e vira um limite sobre quais operacoes sao pegaveis.

Este modulo calcula o lote e, mais util, **mede quanto dessas duas coisas
morde** nas operacoes que o plano de fato produziu.

O insumo e o `meta.json` do exportador. `trade_tick_value` ja vem na moeda da
CONTA (verificado: GBPJPY tem 100 JPY por tick virando 0,6277 USD, EURGBP tem
1 GBP virando 1,3590), entao nao ha conversao de moeda a fazer aqui -- mas o
valor foi lido no dia da exportacao e, para um par cruzado, ele anda com o
cambio. Um erro de 2% no `tick_value` e um erro de 2% no lote, o que e menor
que o proprio arredondamento e maior que zero.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
from dataclasses import dataclass
from pathlib import Path

from research._mt5 import EXPORT_DIR
from research.ftmo_universe import RISK_PER_TRADE

#: O risco por operacao vem de `ftmo_universe`, que carrega a medicao de
#: probabilidade de estouro que escolheu o numero.
#: A conta em uso. So muda os exemplos e o padrao do `--balance`; nenhuma
#: conclusao depende dela, porque tudo o mais e medido em R.
ACCOUNT_BALANCE = 100_000.0

#: Tamanhos de conta da corretora, para a pergunta "isso e pegavel na minha?".
ACCOUNT_SIZES: tuple[float, ...] = (10_000, 25_000, 50_000, 100_000, 200_000)


@dataclass(frozen=True)
class Sizing:
    """O que mandar para a corretora, e o que isso custa de verdade."""

    lots: float
    #: Risco em dinheiro do lote que sera REALMENTE mandado.
    risk_money: float
    #: E o mesmo risco como fracao da conta -- o numero que o plano promete.
    risk_pct: float
    #: O lote ideal antes de arredondar. A distancia entre os dois e o erro.
    exact_lots: float
    #: True quando o ideal fica abaixo de `volume_min`: a ordem so existe
    #: arriscando MAIS do que se pretendia.
    below_minimum: bool

    @property
    def takeable(self) -> bool:
        """Da para pegar sem estourar o risco pretendido?"""
        return not self.below_minimum and self.lots > 0


def position_size(
    *,
    entry: float,
    stop: float,
    balance: float,
    info: dict,
    risk: float = RISK_PER_TRADE,
) -> Sizing | None:
    """Lotes para arriscar `risk` da conta entre `entry` e `stop`.

    `None` quando a distancia do stop e zero -- nao ha ordem a mandar, e
    dividir por ela produziria um lote infinito em vez de um erro.
    """
    distance = abs(entry - stop)
    tick_size = info["trade_tick_size"]
    tick_value = info["trade_tick_value"]
    if distance <= 0 or tick_size <= 0 or tick_value <= 0:
        return None
    # Quanto uma perda de um lote cheio custaria, se o stop for tocado.
    loss_per_lot = distance / tick_size * tick_value
    if loss_per_lot <= 0:
        return None
    exact = balance * risk / loss_per_lot
    step = info["volume_step"] or 0.01
    minimum = info["volume_min"] or step
    # Para BAIXO, sempre: arredondar para cima estoura o risco pretendido, e o
    # limite diario da corretora e sobre a perda, nao sobre a intencao.
    lots = math.floor(exact / step) * step
    below = exact < minimum
    if below:
        # Reporta o piso, e o risco que ELE implica -- que e maior que o alvo.
        lots = minimum
    lots = round(lots, 8)
    return Sizing(
        lots=lots,
        risk_money=lots * loss_per_lot,
        risk_pct=lots * loss_per_lot / balance,
        exact_lots=exact,
        below_minimum=below,
    )


def load_info(export: Path = EXPORT_DIR) -> dict[str, dict]:
    meta = json.loads((export / "meta.json").read_text(encoding="utf-8"))
    return {s["symbol"]: s for s in meta["symbols"]}


#: Como o nome do par na Binance vira o CFD do terminal, para o fluxo de
#: cripto ser dimensionado no instrumento que se opera de fato.
_CRYPTO_CFD = {
    "AAVEUSDT": "AAVUSD", "BNBUSDT": "BNBUSD", "BTCUSDT": "BTCUSD",
    "DOGEUSDT": "DOGEUSD", "ETCUSDT": "ETCUSD", "ETHUSDT": "ETHUSD",
    "GRTUSDT": "GRTUSD", "HBARUSDT": "BARUSD", "ICPUSDT": "ICPUSD",
    "IMXUSDT": "IMXUSD", "LINKUSDT": "LNKUSD", "MANAUSDT": "MANUSD",
    "SOLUSDT": "SOLUSD", "UNIUSDT": "UNIUSD", "VETUSDT": "VECUSD",
    "XLMUSDT": "XLMUSD", "GALAUSDT": "GALUSD",
}

DATASETS = Path("research/.datasets")

#: Os seis fluxos do plano, e onde as operacoes medidas de cada um moram. O
#: cripto aponta para a varredura de ORIGEM (`qf_*`) porque o arquivo de custo
#: (`ftmo_crypto_*`) guarda so `r_pct`, sem os precos -- e sem preco nao ha
#: distancia em ticks, que e o que vira lote.
STREAM_FILES: tuple[tuple[str, str], ...] = (
    ("indice M5", "ftmo_5m.json"),
    ("indice M15", "ftmo_15m.json"),
    ("indice M30", "ftmo_30m.json"),
    ("cripto M15", "qf_m15.json"),
    ("cripto H4", "qf_h4.json"),
    ("cambio H4", "ftmo_fx_4h.json"),
)


def stream_rows(filename: str, info: dict[str, dict]) -> list[dict]:
    """As operacoes de um fluxo, ja com o simbolo do terminal resolvido.

    Um simbolo sem CFD no terminal cai fora em silencio -- a varredura de
    cripto cobre 72 pares da Binance e a corretora lista 16, entao o descarte
    e a regra e nao a excecao aqui.
    """
    path = DATASETS / filename
    if not path.exists():
        return []
    out = []
    for row in json.loads(path.read_text()):
        symbol = _CRYPTO_CFD.get(row["symbol"], row["symbol"])
        if symbol in info and row.get("entry") and row.get("stop"):
            out.append({**row, "cfd": symbol})
    return out


def report(info: dict[str, dict], risk: float) -> None:
    print(f"\n{'=' * 88}\nTAMANHO DE POSICAO   risco alvo {risk:.2%} por operacao"
          f"\n{'=' * 88}")
    print("\nQuanto do risco pretendido sobrevive ao lote discreto:\n")
    head = f"  {'fluxo':<12}{'ops':>6}" + "".join(
        f"{f'${int(b / 1000)}k':>13}" for b in ACCOUNT_SIZES
    )
    print(head)
    print(f"  {'':<12}{'':>6}" + "".join(f"{'risco real':>13}" for _ in ACCOUNT_SIZES))
    for name, filename in STREAM_FILES:
        rows = stream_rows(filename, info)
        if not rows:
            print(f"  {name:<12}{'--':>6}")
            continue
        cells = []
        for balance in ACCOUNT_SIZES:
            got = [
                s for row in rows
                if (s := position_size(entry=row["entry"], stop=row["stop"],
                                       balance=balance, info=info[row["cfd"]],
                                       risk=risk)) is not None
            ]
            if not got:
                cells.append(f"{'--':>13}")
                continue
            pct = st.fmean(s.risk_pct for s in got)
            forced = sum(1 for s in got if s.below_minimum) / len(got)
            mark = "!" if forced > 0.05 else " "
            cells.append(f"{pct:>11.3%}{mark:>2}")
        print(f"  {name:<12}{len(rows):>6}" + "".join(cells))
    print("\n  ! = mais de 5% das operacoes ficam ABAIXO do lote minimo, e so")
    print("      existem arriscando mais que o alvo.\n")


def ticket(balance: float, risk: float, info: dict[str, dict],
           journal: Path) -> None:
    """A ordem pronta para cada decisao aberta do diario.

    Nao manda nada -- imprime o que digitar. O lote e arredondado para BAIXO
    porque arredondar para cima estoura o risco pretendido, e o limite diario
    da corretora e sobre a perda e nao sobre a intencao.
    """
    from liquidity_hunter.app.paper_journal import read_journal
    from liquidity_hunter.core.domain import PaperOutcome

    open_rows = [d for d in read_journal(journal) if d.outcome is PaperOutcome.OPEN]
    print(f"\n{'=' * 88}\nORDENS ABERTAS   conta ${balance:,.0f}   risco alvo "
          f"{risk:.2%} = ${balance * risk:,.2f}\n{'=' * 88}")
    if not open_rows:
        print("\n  nenhuma decisao aberta no diario\n")
        return
    for d in open_rows:
        symbol = _CRYPTO_CFD.get(d.symbol, d.symbol)
        spec = info.get(symbol)
        if spec is None:
            print(f"\n  {d.symbol}: sem ficha no meta.json -- reexporte")
            continue
        size = position_size(entry=d.signal_close, stop=d.stop_price,
                             balance=balance, info=spec, risk=risk)
        if size is None:
            continue
        side = "COMPRA" if d.direction.value == "bullish" else "VENDA"
        print(f"\n  {symbol}  {side}  ({d.timeframe.value})")
        print(f"    entrada {d.signal_close:<12g} stop {d.stop_price:<12g} "
              f"alvo {d.target_price:g}")
        print(f"    LOTE {size.lots:g}   risco ${size.risk_money:,.2f} "
              f"= {size.risk_pct:.3%} da conta")
        if size.below_minimum:
            print(f"    !! ABAIXO DO LOTE MINIMO: o ideal era {size.exact_lots:.4f} e o "
                  f"minimo e {spec['volume_min']:g}.")
            print(f"    !! Pegar isso arrisca {size.risk_pct / risk:.1f}x o alvo. "
                  "Pular, ou aceitar o risco de olhos abertos.")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", default=str(EXPORT_DIR))
    parser.add_argument("--risk", type=float, default=RISK_PER_TRADE)
    parser.add_argument("--balance", type=float, default=ACCOUNT_BALANCE,
                        help="tamanho da conta para o calculo do lote")
    parser.add_argument("--table", action="store_true",
                        help="mostra quanto do risco alvo sobrevive ao lote "
                        "discreto, por tamanho de conta, em vez das ordens")
    parser.add_argument("--journal",
                        default="research/.datasets/ftmo_paper_journal.jsonl")
    args = parser.parse_args()

    info = load_info(Path(args.export))
    if args.table:
        report(info, args.risk)
    else:
        ticket(args.balance, args.risk, info, Path(args.journal))


if __name__ == "__main__":
    main()
