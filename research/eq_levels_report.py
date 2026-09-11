"""Render the auditable E0 numeric appendix from local baselines, without fitting."""

import json

from research.eq_levels_audit import ROOT, Candle, forward, quality_summary, scale_series, stats


def fmt(x, digits=3):
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.{digits}f}"
    return str(x)


def table(lines, columns, rows):
    lines.extend(
        ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    )
    lines.extend("| " + " | ".join(fmt(x) for x in row) + " |" for row in rows)
    lines.append("")


def main():
    b = json.loads((ROOT / "research/eq_levels_baseline.json").read_text())
    summaries = {k: v for k, v in b["summary"].items() if "funnel" in v}
    lines = [
        "# EQ E0 — apêndice numérico",
        "",
        "Gerado por `poetry run python -m research.eq_levels_report`. "
        "Este arquivo contém descrições e comparações exploratórias; não valida alterações.",
        "",
        f'Código de produção: `{b["commit"]}`. '
        f'{len(b["charts"])} snapshots; '
        f'{len(set(c["symbol"] for c in b["charts"]))} símbolos. '
        f'Replay principal a cada {b["replay_step"]} candles, '
        'com 40 candles de aquecimento, sem o último candle de cada snapshot.',
        "",
        "ATR neste relatório = média expansiva causal de TR/close × close do candle avaliado. "
        "Não é ATR de Wilder. Métricas normalizadas por formação/observação/evento, "
        "conforme o campo. "
        "Contagens de pares no replay são **observações de pares**, não pares únicos.",
        "",
        "## Painel e funil retrospectivo",
        "",
    ]
    table(
        lines,
        [
            "TF/lado",
            "charts",
            "pivôs",
            "pares próximos",
            "clusters (inclui <3)",
            "EQs",
            "vivos",
            "sweeps",
            "rejections",
            "breached",
        ],
        [
            [k, v["charts"]]
            + [
                v["funnel"][m]
                for m in [
                    "pivots",
                    "near_pairs",
                    "groups",
                    "formed",
                    "live",
                    "swept",
                    "rejected",
                    "breached",
                ]
            ]
            for k, v in summaries.items()
        ],
    )
    table(
        lines,
        ["TF/lado", "EQ/cluster", "vivos/EQ", "sweep/EQ", "rejection/sweep", "breach/EQ"],
        [
            [k]
            + [
                v["funnel"][a] / v["funnel"][den]
                for a, den in [
                    ("formed", "groups"),
                    ("live", "formed"),
                    ("swept", "formed"),
                    ("rejected", "swept"),
                    ("breached", "formed"),
                ]
            ]
            for k, v in summaries.items()
        ],
    )
    lines += [
        "O funil acima usa a reconstrução final; não deve ser encadeado com contagens dos cohorts "
        "abaixo. Um cluster exige vários pares, logo pares → clusters não é uma "
        "taxa de retenção simples.",
        "",
        "## Coortes observadas e revisitas",
        "",
        "Primeira observação congela a geometria. Um pivô já usado impede contar novamente "
        "aquela linhagem nas coortes. Isso evita inflar a amostra por crescimento do cluster, "
        "mas exclui reciclagens legítimas ou ilegítimas: não reproduz a política "
        "futura do detector.",
        "",
    ]
    table(
        lines,
        [
            "TF/lado",
            "coortes",
            "já swept ao observar",
            "multi-sweep",
            "1ª visita ≤40 (n/N)",
            "após wick ≤40 (n/N)",
            "após close ≤40 (n/N)",
        ],
        [
            [k, v["cohort_n"], v["already_swept"], v["multi_sweep_cohorts"]]
            + [
                f"{v['lifecycle'][s]['revisited']}/{v['lifecycle'][s]['eligible']}"
                for s in ["first_touch", "after_wick", "after_close"]
            ]
            for k, v in summaries.items()
        ],
    )
    lines += [
        "Após morte, revisita exige ao menos um candle inteiramente fora da banda "
        "antes da reentrada. "
        "Eventos sem 40 candles de exposição são censurados na taxa ≤40; reações "
        "têm censura própria por horizonte.",
        "",
        "## Clusters e toques",
        "",
        "2 toques não produz nível em produção; não há comparação de qualidade 2 vs "
        "3 neste painel.",
        "",
    ]
    table(
        lines,
        [
            "TF/lado",
            "toques",
            "n",
            "spread ATR p50",
            "spread ATR máximo",
            "intervalo toques p50",
            "idade final p50",
        ],
        [
            [
                k,
                t,
                len(rr),
                stats(r["spread_atr"] for r in rr)["p50"],
                stats(r["spread_atr"] for r in rr)["max"],
                stats(g for r in rr for g in r["gaps"])["p50"],
                stats(r["age"] for r in rr)["p50"],
            ]
            for k in summaries
            for t in ["2", "3", "4", "5+"]
            for rr in [
                [
                    r
                    for r in b["retrospective_levels"]
                    if f"{r['tf']}/{r['side']}" == k
                    and ("5+" if r["touches"] >= 5 else str(r["touches"])) == t
                ]
            ]
        ],
    )
    lines += [
        "Os preços, diferenças assinadas do extremo para **cada pivô**, midpoint, gaps e pivôs "
        "consumidos estão em `retrospective_levels` no JSON; não foram resumidos em um novo score.",
        "",
        "## Densidade e proximidade",
        "",
    ]
    table(
        lines,
        [
            "TF/lado",
            "vivos média",
            "p50",
            "p90",
            "máximo",
            "alvos exibíveis média",
            "observações >4 ATR",
            "exposições vivas",
            "pares ≤.1/.25/.5/1 ATR",
        ],
        [
            [k]
            + [v["density"][m] for m in ["mean", "p50", "p90", "max"]]
            + [
                v["rendered_targets"]["mean"],
                v["outside_4atr"],
                v["active_level_observations"],
                "/".join(
                    str(v["proximity_pair_observations"][str(r)]) for r in [0.1, 0.25, 0.5, 1.0]
                ),
            ]
            for k, v in summaries.items()
        ],
    )
    table(
        lines,
        ["TF/lado", "distância 0–.5/.5–1/1–2/2–4/4+", "idade 0–10/11–30/31–100/>100"],
        [
            [
                k,
                "/".join(
                    str(v["distance_buckets"].get(s, 0))
                    for s in ["0-0.5", "0.5-1", "1-2", "2-4", "4+"]
                ),
                "/".join(
                    str(v["age_buckets"].get(s, 0)) for s in ["0-10", "11-30", "31-100", "100+"]
                ),
            ]
            for k, v in summaries.items()
        ],
    )
    table(
        lines,
        ["TF", "vivos totais média/p50/p90/máx", "bandas EQ atuais média/p50/p90/máx"],
        [
            [
                tf,
                "/".join(fmt(s["detector"][m]) for m in ["mean", "p50", "p90", "max"]),
                "/".join(
                    fmt(
                        stats(
                            r["totalEQBands"]
                            for r in b.get("visual_current_snapshot", [])
                            if r["tf"] == tf
                        )[m]
                    )
                    for m in ["mean", "p50", "p90", "max"]
                ),
            ]
            for tf, s in b["summary"]["total_density_by_tf"].items()
        ],
    )
    lines += [
        "Bandas atuais incluem memória selecionada de grabs e a última vela do payload original. "
        "Replay exclui essa vela e conta alvos atuais, não memória histórica renderizada. "
        ">4 ATR é proxy declarado; não mede viewport nem colisão de labels em pixels.",
        "",
        "## Qualidade descritiva por TF/lado/fonte/horizonte",
        "",
        "MFE/MAE partem do close do evento e usam somente candles seguintes; "
        "EQH: direção de reação para baixo; EQL: para cima. Excursões são não negativas. "
        "Razão indefinida com MAE=0 fica null, não infinito. "
        "Controle de candles: mesmo símbolo, TF, lado, quarto temporal, ±80 candles, "
        "distância temporal >40 e volatilidade relativa entre 0,8 e 1,25. "
        "Δ é diferença em P(MFE>MAE), não retorno nem prova de edge.",
        "",
    ]
    table(
        lines,
        [
            "TF/lado",
            "fonte",
            "h",
            "n",
            "casados",
            "MFE p50",
            "MAE p50",
            "MFE/MAE p50",
            "P(MFE>MAE)",
            "move médio",
            "ΔP controle",
        ],
        [
            [
                k,
                source,
                h,
                q["n"],
                q["matched"],
                q["mfe"]["p50"],
                q["mae"]["p50"],
                q["ratio"]["p50"],
                q["won"]["mean"],
                q["move"]["mean"],
                q["delta_won"]["mean"],
            ]
            for k, v in summaries.items()
            for source in ["first_touch", "after_wick", "after_close"]
            for h, q in v["quality"][source].items()
        ],
    )
    lines += [
        "## Sweep quality — episódios, sem controle de seleção equivalente",
        "",
        "A = atravessa e fecha de volta; B/C = atravessa e fecha além. C identifica "
        "close anterior do lado interno; B já estava além. OHLC não permite afirmar "
        "qual foi o caminho intrabar. E = mais de um episódio, flag sobreposta a A/B/C. "
        "D aparece no primeiro contato sem atravessar, na tabela subsequente.",
        "",
    ]
    table(
        lines,
        [
            "TF/lado",
            "classe",
            "h",
            "n",
            "MFE p50",
            "MAE p50",
            "razão p50",
            "P(MFE>MAE)",
            "move médio",
        ],
        [
            [
                k,
                cat,
                h,
                q["n"],
                q["mfe"]["p50"],
                q["mae"]["p50"],
                q["ratio"]["p50"],
                q["won"]["mean"],
                q["move"]["mean"],
            ]
            for k, v in summaries.items()
            for cat in ["A", "B", "C"]
            for h, q in v["sweep_categories"][cat].items()
        ],
    )
    table(
        lines,
        ["TF/lado", "1º contato", "n h10", "MFE p50", "MAE p50", "P(MFE>MAE)", "ΔP"],
        [
            [
                k,
                cat,
                q["n"],
                q["mfe"]["p50"],
                q["mae"]["p50"],
                q["won"]["mean"],
                q["delta_won"]["mean"],
            ]
            for k, v in b["extra_axes"].items()
            for cat in ["rejection_A", "close_through_BC", "touch_D"]
            for q in [v[cat]["10"]]
        ],
    )
    lines += [
        "## Controle de lifecycle casado por pools de pivô único",
        "",
        b["followup_controls"]["definition"],
        "",
        "Taxas de rejection/close-through abaixo usam todos os eventos elegíveis como denominador "
        "(inclusive os sem revisita). São incidências em até 40 candles, não "
        "probabilidade condicional à revisita.",
        "",
    ]
    table(
        lines,
        [
            "TF/lado/fonte",
            "elegíveis",
            "casados",
            "revisit ≤40",
            "rejection ≤40",
            "close-through ≤40",
            "espera p50",
            "Δrevisit",
            "Δrejection",
            "Δclose",
        ],
        [
            [
                k,
                v["eligible"],
                v["matched"],
                v["raw"]["revisited"]["mean"],
                v["raw"]["rejection"]["mean"],
                v["raw"]["close_through"]["mean"],
                v["wait"]["p50"],
            ]
            + [v["delta"][m]["mean"] for m in ["revisited", "rejection", "close_through"]]
            for k, v in b["followup_controls"]["summary"].items()
        ],
    )
    lines += [
        "Reação em 5/10/20/40 e controles individuais estão em `followup_controls.rows`. "
        "Comparador de pivô único pode pertencer a outro EQ; não representa uma amostra "
        "independente de todos os clusters. Matching com reposição e poucas observações "
        "em alguns recortes impedem interpretar estes deltas como efeito causal de "
        "manter o nível vivo.",
        "",
        "## Eixos individuais — primeiro contato, h10",
        "",
        "As faixas são diagnósticas, sem seleção de threshold. Estrutura usa a função real "
        "`structureTrendByCandle` sobre eventos do snapshot final; sua disponibilidade histórica "
        "não foi certificada por replay estrutural nesta auditoria. Não se usa esse eixo para "
        "afirmar qualidade causal. Recência do último pivô coincide com idade desde formed_at.",
        "",
    ]
    table(
        lines,
        [
            "TF/lado",
            "eixo",
            "bucket",
            "n",
            "casados",
            "P(MFE>MAE)",
            "ΔP",
            "símbolos",
            "% símbolos Δ>0",
            "Δ mediana por símbolo",
        ],
        [
            [
                k,
                axis,
                bucket,
                q["n"],
                q["matched"],
                q["won"]["mean"],
                q["delta_won"]["mean"],
                q["symbols"],
                q["positive_symbol_fraction"],
                q["median_symbol_delta"],
            ]
            for k, v in summaries.items()
            for axis, buckets in v["axes"].items()
            for bucket, qq in buckets.items()
            for q in [qq["10"]]
        ],
    )
    lines += [
        "Não há comparação casada de idade/distância entre **todos** os buckets de strength. "
        "O controle temporal/volatilidade ajuda, mas não remove correlações entre os eixos. "
        "Portanto as curvas acima não autorizam score, gate, expiry ou quality tier.",
        "",
        "## Estabilidade temporal e robustez por símbolo — primeiro contato, h10",
        "",
    ]
    table(
        lines,
        [
            "TF/lado",
            "Δ bloco 1",
            "Δ bloco 2",
            "Δ bloco 3",
            "Δ bloco 4",
            "símbolos",
            "% Δ>0",
            "Δ mediana",
            "concentração top3",
        ],
        [
            [k]
            + [q["blocks"][str(i)]["mean"] for i in range(4)]
            + [
                q["symbols"],
                q["positive_symbol_fraction"],
                q["median_symbol_delta"],
                q["concentration_top3"],
            ]
            for k, v in summaries.items()
            for q in [v["quality"]["first_touch"]["10"]]
        ],
    )
    table(
        lines,
        ["TF/lado", "bottom 3 (símbolo: delta, n)", "top 3 (símbolo: delta, n)"],
        [
            [
                k,
                ", ".join(
                    f"{r['symbol']}: {r['delta']:.3f}, {r['n']}" for r in q["per_symbol"][:3]
                ),
                ", ".join(
                    f"{r['symbol']}: {r['delta']:.3f}, {r['n']}" for r in q["per_symbol"][-3:]
                ),
            ]
            for k, v in summaries.items()
            for q in [v["quality"]["first_touch"]["10"]]
        ],
    )
    lines += [
        "Top/bottom são descrições pós-hoc; nenhum ativo é escolhido para resgatar resultado. "
        "O JSON registra quatro blocos e símbolos por horizonte/bucket. Blocos dentro de "
        "uma mesma janela não equivalem a quatro regimes independentes; os TFs têm "
        "durações diferentes.",
        "",
        "## Overlap de geometria do snapshot final",
        "",
        "Interseção exata com banda EQ; BOS/CHoCH usam referência pontual; POI/bands usam "
        "range; VWAP usa ±1σ final; profile usa POC/VAH/VAL. Fontes históricas "
        "podem já estar mortas. "
        "Essas contagens não medem confluência causal nem informação incremental.",
        "",
    ]
    table(
        lines,
        [
            "TF/lado",
            "n EQ",
            "BOS",
            "CHoCH",
            "POI",
            "liquidation",
            "VWAP ±1σ final",
            "profile final",
        ],
        [
            [k, v["funnel"]["formed"]]
            + [
                v["overlap_snapshot"][m]
                for m in ["bos", "choch", "poi", "liquidation", "vwap_1sigma", "volume_profile"]
            ]
            for k, v in summaries.items()
        ],
    )
    lines += [
        "## Casos BTC / ETH / SOL",
        "",
        "Seleção pós-hoc para ilustrar mecânica, não amostra de validação. "
        "Datas UTC; faixa é [low, high]. A confirmação mínima é formed_at +5 candles; "
        "ela não garante que a tolerância daquela data já permitia o cluster final.",
        "",
    ]
    for symbol, cases in b["examples"].items():
        lines += [f"### {symbol}", ""]
        rows = []
        for label, r in cases.items():
            if label == "B_close_levels":
                if r:
                    a, z = r["first"], r["second"]
                    rows.append(
                        [
                            label,
                            a["chart"],
                            f"{a['midpoint']:.6g} / {z['midpoint']:.6g}",
                            f"gap={r['gap_atr']:.3f} ATR; coexistiram={r['coexisted']}",
                            f"{a['formed_at']} / {z['formed_at']}",
                        ]
                    )
                else:
                    rows.append([label, "—", "—", "Nenhum par encontrado ≤1 ATR", "—"])
                continue
            if not r:
                continue
            data = json.loads((ROOT / "frontend/research/fixtures" / r["chart"]).read_text())
            cs = [Candle.model_validate(x) for x in data["candles"][:-1]]
            units = scale_series(cs)
            event = r["death"]
            reaction = (
                forward(cs, event, 10, r["side"], units[event]) if event is not None else None
            )
            detail = f"{r['touches']} toques; spread={r['spread_atr']:.3f} ATR; idade={r['age']}"
            if reaction:
                detail += f"; MFE/MAE h10={reaction['mfe']:.2f}/{reaction['mae']:.2f}"
            timeline = (
                f"formou {r['formed_at']} → wick {r['invalidated_at']} → close {r['breached_at']}"
            )
            rows.append(
                [
                    label,
                    r["chart"],
                    f"{r['side']} [{r['low']:.8g}, {r['high']:.8g}]",
                    detail,
                    timeline,
                ]
            )
        table(lines, ["caso", "chart", "faixa/preço", "diagnóstico", "timeline"], rows)
        lines += [
            "A classe A ilustra geometria limpa/rejection; a reação h10 acima determina se "
            "o exemplo de fato reagiu melhor que sua excursão adversa. G é candidato por maior "
            "spread, não um “falso equal” comprovado. O JSON contém cada preço/toque.",
            "",
        ]
    lines += ["## Replay candle a candle e M5 diagnóstico", ""]
    for file in ["eq_levels_replay_baseline.json", "eq_levels_m5_baseline.json"]:
        p = ROOT / "research" / file
        if not p.exists():
            continue
        other = json.loads(p.read_text())
        lines += [
            f'`{file}`: {len(other["charts"])} charts; passo {other["replay_step"]}; '
            f'{len(other["retrospective_levels"])} EQs; {len(other["frozen_cohorts"])} coortes.',
            "",
        ]
        table(
            lines,
            ["TF/lado", "pivôs", "formados", "vivos", "n 1º contato h10", "ΔP h10"],
            [
                [
                    k,
                    v["funnel"]["pivots"],
                    v["funnel"]["formed"],
                    v["funnel"]["live"],
                    v["quality"]["first_touch"]["10"]["n"],
                    v["quality"]["first_touch"]["10"]["delta_won"]["mean"],
                ]
                for k, v in other["summary"].items()
                if "funnel" in v
            ],
        )
    lines += [
        "M5 vem do cache local de BTC/ETH/SOL até 05/09/2026 e usa outro período. "
        "É diagnóstico separado; não valida o painel principal até 10/09. "
        "Não há contexto estrutural no cache M5. A última barra também foi excluída.",
        "",
        "## Proveniência e cobertura",
        "",
    ]
    table(
        lines,
        ["chart", "candles fechados", "início UTC", "fim UTC", "SHA256 do snapshot"],
        [[c["file"], c["n"], c["start"], c["end"], c["sha256"]] for c in b["charts"]],
    )
    recent = {c["symbol"] for c in b["charts"] if c["end"] >= "2026-09-01"}
    common = (
        set.intersection(
            *[{c["symbol"] for c in b["charts"] if c["tf"] == tf} for tf in ["15m", "1h", "4h"]]
        )
        & recent
    )
    lines += [
        "## Sensibilidade: painel recente com os três TFs",
        "",
        f"{len(common)} símbolos / {len(common)*3} charts. Exclui EOS (2025) e LRC "
        "(só M15). Não muda thresholds nem usa o resultado para selecionar ativos.",
        "",
    ]
    table(
        lines,
        ["TF/lado", "n h10", "ΔP h10", "Δ blocos 1/2/3/4"],
        [
            [
                key,
                q["n"],
                q["delta_won"]["mean"],
                "/".join(fmt(q["blocks"][str(i)]["mean"]) for i in range(4)),
            ]
            for key in summaries
            for q in [
                quality_summary(
                    [
                        e
                        for e in b["events"]
                        if e["symbol"] in common
                        and f"{e['tf']}/{e['side']}" == key
                        and e["source"] == "first_touch"
                    ]
                )["10"]
            ]
        ],
    )
    (ROOT / "research/EQ_LEVELS_E0_RESULTS.md").write_text("\n".join(lines) + "\n")
    print("research/EQ_LEVELS_E0_RESULTS.md")


if __name__ == "__main__":
    main()
