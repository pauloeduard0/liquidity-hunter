# EQH/EQL — relatório consolidado E0–E1.7

**Decisão:** não alterar produção nesta etapa. A auditoria reproduz a regra
atual e identifica dependência temporal de janela, recomposição de identidade e
diferenças entre pool, memória e evento. Os experimentos causais e o holdout
não validaram uma substituição com ganho robusto.

## Respostas às 25 perguntas

1. **Implementação:** backend Python em `liquidity/detectors/equal_levels.py`,
   factories em `app/dashboard_data.py`, lifecycle em `liquidity/mitigation.py`,
   domínio em `core/domain/liquidity_zone.py`; frontend em `MainChart.tsx` e
   `EqlZonesPrimitive.ts`. O mapa detalhado está no [E0](EQ_LEVELS_E0_AUDIT.md).
2. **Definição:** três fractals estritos, agrupados por preço crescente usando a
   âncora do primeiro membro; o nível é uma faixa min/max formada no último pivô.
3. **Pivôs:** fractals locais confirmados por cinco candles de cada lado; EQH usa
   máximas e EQL mínimas. Não são pivôs de internal structure e não há provisional.
4. **Igualdade:** `abs(preço - âncora) <= âncora × 0,5 × mean(TR/close)`.
5. **Causalidade da tolerância:** não é estável; a média usa a lista inteira e
   pode incorporar volatilidade futura.
6. **Repaint:** sim, em tolerância, strength, recomposição e identidade histórica;
   confirmação e lifecycle são mudanças distintas.
7. **Clustering:** não é transitivo; C só entra se couber na âncora A. Cada pivô
   pertence a um grupo por snapshot.
8. **Duplicatas:** zero bandas sobrepostas estáticas no mesmo lado; há transições
   históricas com memberships compartilhadas.
9. **Quantidade:** 29.195 pivôs, 4.613 EQs finais, zero sobreposições e 11.289
   transições com pivôs compartilhados no replay E0.
10. **Toques:** o mínimo real é três; não há evidência causal de que mais toques
    sejam melhores. Cinco ou mais é uma coorte pequena no nascimento.
11. **Idade:** efeito varia por TF, lado e bloco; nenhum gradiente robusto.
12. **Distância:** explica carga visual, mas não há limiar de qualidade validado.
13. **Lifecycle:** mecanicamente consistente para uma faixa congelada; a
    recomposição pode renovar a janela de consumo e reabrir uma geometria.
14. **Wick/close:** são eventos diferentes; wick marca `invalidated_at` e close
    pode marcar `breached_at`, inclusive no mesmo candle.
15. **Simetria:** regras espelhadas, respostas de mercado diferentes por TF e período.
16. **TFs:** painel separado para M15, H1 e H4; não misturar resultados.
17. **Ruído visual:** níveis distantes, memória antiga e atributos reescritos são
    candidatos; não há prova para esconder ou deduplicar automaticamente.
18. **Qualidade:** nenhum eixo isolado passou simultaneamente estabilidade,
    controle e robustez para virar tier/score.
19. **Bug concreto:** futuro altera associação/strength de pivôs antigos e pools
    consumidos podem reaparecer como geometrias vivas.
20. **Primeira hipótese:** H1, causalidade e identidade histórica, mantendo 3/5/0,5.
21. **Simplificação possível:** vocabulário e contrato de pool/memória/strength;
    não tratar idade e recência do último pivô como eixos independentes.
22. **Preservar:** EQH/EQL, faixa, wick/close, TF, mínimo e tolerância atuais até
    experimento aprovado.
23. **Melhoria visual segura:** tornar confirmação, memória e estado auditáveis;
    não esconder níveis por regra nova.
24. **Melhoria lógica promissora:** publicação causal com identidade estável e
    journal, sem mudar thresholds.
25. **Mudar produção agora:** **não**. O holdout E1.7 deixou R/N praticamente
    iguais e L inconsistente.

## Evidência e próximos passos

O E0 completo está em [EQ_LEVELS_E0_RESULTS.md](EQ_LEVELS_E0_RESULTS.md). Os
experimentos de causalidade, estado, store, journal e holdout estão em
[E1](EQ_LEVELS_E1_RESULTS.md), [E1.5](EQ_LEVELS_E1_5_RESULTS.md),
[E1.6](EQ_LEVELS_E1_6_RESULTS.md) e [E1.7](EQ_LEVELS_E1_7_RESULTS.md).

Foram mantidos todos os artefatos em `research/`; baselines JSON permanecem
gitignored. A suíte atual passa com 57 testes e Ruff sem erros. O próximo passo,
caso seja desejado, é uma proposta experimental revisável para uma única
propriedade causal (`strength`/identidade), sem implementação produtiva.
