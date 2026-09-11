# EQ E4 — gate para eventual promoção

E4 não implementa flag. Ele registra as condições necessárias para que uma
futura revisão possa decidir se o candidato causal sai de `shadow-only`.

## Condições obrigatórias

1. Painel completo sem perda de chart, identidade duplicada ou gap de evento.
2. Restart contínuo reproduz eventos e snapshots em todos os TFs.
3. Divergências R/N têm explicação por identidade causal, sem alteração oculta
   de threshold, clustering ou lifecycle.
4. Qualidade no holdout não degrada em nenhum TF/lado de forma consistente e a
   mediana por símbolo não depende de poucos ativos.
5. Payload projetado permanece compatível com `LiquidityZone` e consumidores.
6. Runtime e memória ficam dentro do orçamento operacional medido em shadow.
7. Existe rollback automático para painel incompleto, latência excessiva,
   corrupção de estado ou divergência sem explicação.

## Estado atual

O E3.2 retorna `shadow-only`: 202/211 charts têm alguma divergência R/N. O
holdout E2 não mostrou ganho robusto de qualidade. O E3.3 explicou a composição
divergente pelo relógio de pivôs e o E4.2 validou compatibilidade de payload no
painel BTC/ETH/SOL. O E3.4 validou 633 restarts nos 211 charts, em três
fronteiras por chart, com checkpoints JSON completos; não simulou queda de
processo ou gravação interrompida. Esses resultados cumprem partes da investigação, mas o
conjunto de condições de promoção ainda não está satisfeito. Ver
[resultado E3.3](EQ_LEVELS_E3_3_RESULTS.md).

Uma futura execução deve permanecer observacional, com flag desligada por
default, até que todas as condições sejam verificadas em um novo período. Este
documento não autoriza alteração de produção.

Continuidade e custos de restauração: [resultado E3.4](EQ_LEVELS_E3_4_RESULTS.md).

O E3.5 mediu o grafo Python retido nos 211 charts: mediana final de 4,51 MiB
por chart, com crescimento também na projeção sem logs. Não mediu RSS/pico do
serviço e não há orçamento de memória definido; a condição 6 continua aberta.
O [parecer E3.5](EQ_LEVELS_E3_5_RESULTS.md) recomenda não investir na integração
agora, dada a ausência de ganho robusto e o trabalho operacional restante.
