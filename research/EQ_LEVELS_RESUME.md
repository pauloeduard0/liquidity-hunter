# EQ — retomada de 2026-09-11

## Estado recuperado e decisão

O trabalho pendente cobre E3.1 (contrato operacional), E3.2 (aplicação ao
baseline) e E4.2 (compatibilidade do payload). O candidato continua em
`shadow-only`; esta revisão não promove o detector nem modifica produção.

A reaplicação de `research.eq_levels_e3_2` ao JSON local confirmou 211 charts,
202 com divergências e orçamento de 300 s. O arquivo contém runtime de
211,300504506 s. Os relatórios anteriores registram 217,75 s: esse número foi
preservado como registro anterior, mas não descreve o JSON atual. Não foi
executado novamente o painel completo nesta retomada; o tempo acima é o
armazenado no baseline, não o tempo da validação.

Os totais por bloco/TF do JSON conferem com a tabela de
`EQ_LEVELS_E3_PANEL_RESULTS.md`. Atenção à unidade: o agregado
`snapshots_with_R_N_difference` conta pares snapshot/lado (EQH e EQL
separadamente); `block_divergent_snapshots` conta snapshots com divergência em
qualquer lado. Portanto, esses totais não são diretamente somáveis entre si.

## Ajustes da revisão

O contrato agora rejeita runtime ou orçamento não finito, orçamento não
positivo, contagem esperada inválida, linhas/identidades malformadas e valores
negativos, booleanos ou não inteiros nos contadores de divergência por bloco.
Foram adicionados testes dessas falhas e do resultado `eligible-for-review`.
Esse resultado continua sendo apenas elegibilidade para revisão.

O teste de payload passou a cobrir BTC/ETH/SOL × M15/H1/H4, os nove casos
citados no relatório E4.2. A execução direta confirmou compatibilidade nos nove.

## Próximo passo registrado antes do E3.3

A compatibilidade de payload está verificada, mas não resolve a ausência de
ganho robusto no holdout. Antes de considerar promoção, o próximo trabalho é
explicar as divergências por identidade causal e período, conforme o gate E4.
O baseline agregado mede frequência e magnitude, mas não atribui causas.

Também permanecem requisitos operacionais: confrontar as identidades com um
manifesto esperado (o contrato atual verifica quantidade e unicidade), medir
memória, verificar continuidade/restart no painel completo e implementar e
validar rollback antes de qualquer integração. O contrato de decisão em
research não é um mecanismo de rollback em produção.

Artefatos de baseline locais são ignorados pelo Git. O checkpoint versionado
inclui código, testes, protocolos e relatórios; não inclui esses datasets locais.

## Validação nesta retomada

- Suíte EQ de research e testes do detector equal levels: 95 testes passaram
  em 45,49 s (antes de ampliar a parametrização do payload).
- Teste de payload ampliado: 9 casos passaram em 14,09 s.
- Ruff nos módulos E3/E4 revisados e seus testes, e `git diff --check`: passaram.

## Continuação concluída — E3.3

A atribuição por identidade e período foi executada nos 211 charts e está em
[EQ_LEVELS_E3_3_RESULTS.md](EQ_LEVELS_E3_3_RESULTS.md). Todas as 32.338
observações barra/lado com diferença de membership obedeceram ao relógio de
pivôs; o painel reproduziu o baseline e passou 5.468 checagens de paridade
nativa. Não houve divergência semântica nas identidades comuns.

Ao concluir o E3.3, a decisão continuava `shadow-only` e o próximo requisito
era restart/continuidade no painel completo (concluído no E3.4 abaixo); a ausência de ganho robusto de
qualidade permanece independente dessas verificações. O E3.3 já confronta o
manifesto esperado, mas isso ainda não foi incorporado ao contrato genérico E3.1.

## Continuação concluída — E3.4

O painel completo passou em 633 restarts (três por chart), com igualdade do
estado restaurado e da execução final contínua. Foram preservados 252.003
snapshots e 56.478 eventos. Resultados, custos e limites estão em
[EQ_LEVELS_E3_4_RESULTS.md](EQ_LEVELS_E3_4_RESULTS.md).

Essa etapa encerra a pendência de continuidade do checkpoint de pesquisa nas
fronteiras ensaiadas. Não valida queda durante gravação nem recuperação de
processo. O próximo passo operacional é medir memória e crescimento do estado
com o histórico; persistência tolerante a interrupções e rollback seguem
pendentes. O candidato continua `shadow-only`, sem ganho robusto no holdout.

## Pausa acordada após o E3.4

O estudo fica pausado neste checkpoint por solicitação do usuário. Na retomada,
medir memória e crescimento do estado com o histórico e então avaliar se
compensa investir na integração, considerando a ausência de ganho robusto no
holdout. Persistência tolerante a interrupções e rollback operacional continuam
pendentes caso se decida avançar. Não há decisão de promover o candidato: o
estado permanece `shadow-only`.
