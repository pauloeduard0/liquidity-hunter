# Dominant Liquidity D0 — distância e composição do ranking

## Conclusão inicial

A percepção de níveis distantes tem suporte nos dados, principalmente no H4.
O card mostra o maior score, não um próximo alvo validado. O score permite
que strength suplante proximidade; a partir de 5% de distância, não distingue
mais a distância dos candidatos. Isso não é um defeito do detector EQ.

A rodada mediu 211 charts do painel histórico EQ. Todos os rankings salvos
foram reproduzidos em composição, ordem e score. Sete snapshots não tinham
zonas ativas. O replay causal gerou 2.100 observações, das quais 70 não tinham
candidatos; as outras 2.030 são analisadas abaixo. Não houve ajuste de pesos.

## Snapshots capturados

| TF | Com candidatos | Distância mediana (%) | Mediana ATR14 | Vencedor ≥5% | Vencedor >3 ATR | Vencedor é o mais próximo |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 15m | 68 | 1,228 | 2,10 | 10,29% | 39,71% | 33,82% |
| 1h | 68 | 1,467 | 1,17 | 20,59% | 26,47% | 35,29% |
| 4h | 68 | 14,374 | 4,28 | 63,24% | 54,41% | 17,65% |

São snapshots históricos das fixtures, não uma leitura do chart atual do
usuário. A discrepância entre TFs mostra por que a escala percentual fixa não
é uma medida uniforme de proximidade operacional.

## Observações causais

| TF | Com candidatos | Distância mediana (%) | Mediana ATR14 | Vencedor ≥5% | Vencedor >3 ATR | Vencedor é o mais próximo |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 15m | 680 | 1,019 | 1,51 | 7,06% | 24,85% | 52,50% |
| 1h | 680 | 1,321 | 1,45 | 14,12% | 23,82% | 59,56% |
| 4h | 670 | 3,432 | 1,66 | 44,33% | 40,60% | 42,09% |

No total, 29,70% das escolhas ficaram acima de 3 ATR. Em 173 observações,
o vencedor estava acima de 3 ATR e havia outro nível ativo a até 1 ATR.
Em todas essas 173 escolhas, a strength do vencedor era maior. Há também
41 escolhas a pelo menos 5% com outro nível a menos de 1%.

Em 6,16% das observações, todos os candidatos tinham distance_score zero;
no H4, 14,48%. Nesses casos, a proximidade não influencia a ordem. Empates no
primeiro score ocorreram em 2,36% das observações e em 5,07% no H4; a ordenação
estável preserva a ordem de entrada, sem desempate explícito por proximidade.

A frequência de vencedores acima de 3 ATR cresceu nos quatro blocos relativos:
13,30%, 27,91%, 34,65% e 41,38% (406, 609, 609 e 406 observações). Isso descreve
este histórico e não demonstra que idade seja a causa do crescimento.

## Exemplo concreto e hipótese de escala

No snapshot BTCUSDT/M15, o vencedor EQH estava a 1,273% / 5,845 ATR, com
strength 1 e score 76,81. Um swing high do mesmo lado estava a apenas
0,165% / 0,755 ATR, mas com strength 0,0036 e score 45,83.

O ranking combina strength de EQ (volume de área) e de swing (proeminência)
como se fossem valores comparáveis no mesmo eixo de 40 pontos. O exemplo
mostra que isso merece uma auditoria de escala por família; não prova, sozinho,
que o nível EQ seja pior. No snapshot, 153/204 vencedores são EQ; no replay
causal, 1.068/2.030. Não se deve assumir viés universal com base só no snapshot.

## Contato futuro com a banda fixada no momento da escolha

| TF | Atual h20 / h40 | Mais próximo h20 / h40 | Mais próximo do mesmo lado h20 / h40 |
| --- | ---: | ---: | ---: |
| 15m | 48,82% / 55,15% | 62,79% / 71,91% | 55,00% / 63,09% |
| 1h | 52,06% / 61,47% | 58,53% / 70,29% | 58,24% / 69,71% |
| 4h | 46,72% / 53,28% | 72,84% / 81,19% | 61,79% / 69,40% |

No conjunto: atual 49,21% / 56,65%; próximo 64,68% / 74,43%; próximo do mesmo
lado 58,33% / 67,39%. Contato mede interseção high/low com a banda original,
não sweep, retorno ou manutenção da publicação. A vantagem geométrica dos
níveis próximos impede usar esses números como prova de melhor estratégia.

## Próxima etapa proposta

Separar a intenção do card: destacar uma concentração relevante ou indicar
um alvo próximo são objetivos diferentes. A regra atual mede uma combinação
heurística e não estima a probabilidade de visita.

Antes de alterar produção:

1. Auditar a escala de strength entre EQ e swings, controlando por distância,
   lado, timeframe e período; avaliar se ela acrescenta informação além de
   proximidade. Não escolher novos pesos pelo resultado de contato do D0.
2. Pré-definir uma alternativa de distância em ATR e um desempate por
   proximidade, distinguindo efeito de escala do efeito de strength.
3. Avaliar a alternativa em período independente, com critérios de utilidade
   do card e cobertura definidos antes da execução. Não tornar o nível mais
   próximo automaticamente o vencedor apenas para maximizar contato.

Uma melhoria de apresentação possível é explicitar distância e natureza do
ranking no card. Nenhuma alteração de produção foi feita nesta rodada.

## Validação e artefatos

13 testes passaram: três casos D0 (nível forte distante vence, saturação/empate,
exclusão de consumidos) e os testes do scoring existente. Ruff e
`git diff --check` passaram. Todos os 211 rankings capturados foram reproduzidos.

[Protocolo](DOMINANT_LIQUIDITY_D0_PROTOCOL.md),
[auditor](dominant_liquidity_audit.py), [testes](test_dominant_liquidity_audit.py).
Execução: `poetry run python -m research.dominant_liquidity_audit`.
Dados locais: `research/.replay_cache/dominant_liquidity_d0.json` (gitignored).
