# Dominant Liquidity D0 — diagnóstico do ranking atual

Esta rodada mede o ranking existente, sem modificar pesos, detector ou card.
O painel usa as 211 fixtures do manifesto EQ E0, conferidas por SHA-256.
Não é uma captura do mercado atual nem necessariamente do chart visto pelo usuário.

## Leituras separadas

1. Snapshot salvo: recalcular o ranking com as zonas e o preço capturados e
   comparar composição, ordem e score com `ranked_zones`. Medir distância do
   vencedor, do nível mais próximo e do mais próximo do mesmo lado.
2. Prefixos causais: excluir a última barra da fixture, começar com 200 barras,
   avançar em passos de 100 e exigir 40 barras futuras completas. Reexecutar
   os quatro detectores usados pelo dashboard e a mitigação somente no prefixo.
   Fixar a seleção antes de observar as barras futuras.

Métricas: distância percentual ao ponto médio, distância em ATR14 simples de
true range, strength, score, tipo/lado, empates no primeiro score e casos em
que todos os candidatos já têm distância zerada. Medir o contato da banda
original com o high/low futuro em 20 e 40 barras, sem exigir que a zona continue
publicada. Contato não é sweep, retorno, probabilidade calibrada ou trade.

Comparadores são o nível ativo mais próximo e o mais próximo do mesmo lado
do vencedor. Limiar descritivo de distância: 5% (saturação do score existente)
e 3 ATR (escala de volatilidade, sem servir como filtro novo). Blocos temporais
são quartos relativos a cada série. Não há busca de pesos nem otimização da
taxa de contato. Ausência de candidatos é registrada, não removida silenciosamente.

A ordenação estável em empates mantém a ordem de entrada dos detectores.
O componente de timeframe é constante dentro de cada chart. Strength compara
medidas diferentes entre swings (proeminência) e EQ (volume da área).

## Limites

Níveis próximos têm vantagem geométrica na taxa de contato. O comparador do
mesmo lado reduz confusão de direção, mas não elimina essa vantagem. Observações
compartilham histórico e níveis, não são amostras independentes. Datas diferem
entre TFs; não extrapolar para outro período ou para desempenho financeiro.
A compatibilidade do snapshot não garante paridade integral com todos os demais
campos do dashboard; só zonas/score utilizados pelo card são confrontados.

Antes de propor uma mudança, distinguir falta de níveis próximos, preferência
por strength, saturação da distância e semântica do card. Uma regra nova
precisará de critérios próprios e avaliação em período independente.

Execução: `poetry run python -m research.dominant_liquidity_audit`.
Código: [auditor](dominant_liquidity_audit.py), [testes](test_dominant_liquidity_audit.py).
Dados locais: `research/.replay_cache/dominant_liquidity_d0.json` (gitignored).
