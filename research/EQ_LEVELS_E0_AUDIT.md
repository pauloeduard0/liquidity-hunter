# EQ — E0: auditoria do indicador atual

**Decisão: NÃO alterar produção.** Há evidência reproduzível de dependência da janela e reescrita de identidade histórica. Ainda não há evidência suficiente para escolher uma alteração de tolerância, agrupamento, lifecycle, expiry, número de toques ou apresentação.

Auditoria local, sem fetch de mercado, sem otimização e sem alteração dos módulos de produção. Números completos, controles, exemplos e proveniência estão em [EQ_LEVELS_E0_RESULTS.md](EQ_LEVELS_E0_RESULTS.md). Os três baselines JSON são gitignored pela regra já existente `research/*_baseline.json`.

## 1. Implementação e caminhos de integração

| Papel | Implementação real |
|---|---|
| Entidade | `liquidity_hunter/core/domain/liquidity_zone.py`: `LiquidityZone` |
| Enums | `liquidity_hunter/core/domain/enums.py`: `EQUAL_HIGHS`, `EQUAL_LOWS`, `BUY_SIDE`, `SELL_SIDE`, `TimeFrame` |
| OHLCV e validação | `core/domain/candle.py`, `liquidity/detectors/_common.py` |
| Criação EQH/EQL | `liquidity/detectors/equal_levels.py`: `_EqualLevelDetector.detect`, subclasses `EqualHighDetector` / `EqualLowDetector` |
| Pivôs | `liquidity/detectors/swing_points.py`: `SwingHighDetector` / `SwingLowDetector` |
| TR | `indicators/supertrend.py`: `true_range_series` |
| Sweep/breach | `liquidity/mitigation.py`: `mark_swept_zones`, `_check_zone` |
| Configuração efetiva | `app/dashboard_data.py`: `_EQ_MIN_TOUCHES=3`, `_EQ_SWING_LOOKBACK=5`, `_EQ_TOLERANCE_ATR=0.5`, factories `_equal_high_detector` / `_equal_low_detector` |
| Dashboard | `load_dashboard_data`: detecta, marca consumo, conserva todos em `liquidity_zones`, envia apenas `not is_mitigated` para ranking |
| Overview | `app/overview.py` usa as mesmas factories e marcação, sobre `run.candles` |
| Ranking | `scoring/engine.py`, `scoring/models.py`, `scoring/weights.py` |
| Grabs agregados | `app/liquidity_grabs.py`, `core/domain/liquidity_grab.py`: agrupa por candle/lado e agrega EQ/OB |
| Hunt | `app/liquidity_hunt.py`: EQ como alvo por midpoint; consumo no candle corrente tem confirmação própria |
| Endpoints | `api/routes/dashboard.py`: `GET /api/dashboard`; `api/routes/overview.py`: `GET /api/overview`; serialização em `api/schemas.py` |
| Cliente | `frontend/src/api/dashboard.ts`, `frontend/src/types/dashboard.ts`, `frontend/src/App.tsx` |
| Seleção para desenho | `frontend/src/components/MainChart.tsx`: `standing`, `byProximity`, `poolZones`, `grabs`, `backingZone` |
| Desenho | `frontend/src/charting/EqlZonesPrimitive.ts`: faixa, bordas, alpha, terminador; labels em `MainChart.tsx`; cores em `frontend/src/theme.ts` |
| Fonte do PISO | `frontend/src/utils/defendedLevels.ts`: `buildDefenceLevels`, família `resting`, `died=invalidated_at` |
| Estrutura usada no diagnóstico | função real `structureTrendByCandle`, em `frontend/src/utils/tideRibbon.ts`, chamada pelo helper TS de pesquisa |

Não existe endpoint específico EQ. O parâmetro público `swing_lookback` do dashboard **não substitui** o lookback 5 das factories EQ. Os defaults da classe genérica são diferentes: `min_touches=2`, `swing_lookback=10`, `tolerance_pct=0.0005`, `tolerance_atr=None`. Usar esses defaults em uma auditoria não mediria o dashboard.

Testes existentes: `tests/liquidity/detectors/test_equal_levels.py`, `test_swing_points.py`, `tests/liquidity/test_mitigation.py`, `tests/scoring/test_engine.py`, `tests/app/test_dashboard_data.py`, `test_liquidity_grabs.py`, `test_liquidity_hunt.py`; no frontend, `defendedLevels.test.ts`. As afirmações históricas nos comentários do detector não foram tratadas como evidência atual.

## 2. Definição formal suficiente para reconstrução

Entrada: lista de candles positivos, de mesmo símbolo e TF, na ordem fornecida. O detector valida identidade de símbolo/TF, não ordena os candles. A marcação de consumo ordena por timestamp. O harness usa a ordem temporal das fixtures.

1. **Pivôs EQH:** para cada índice `i`, de 5 até `n−6`, aceitar `high[i]` somente se for estritamente maior que os highs dos cinco candles anteriores e cinco posteriores. Empates rejeitam o pivô. **EQL:** trocar high/maior por low/menor. São fractals locais da camada de liquidez, independentes dos pivôs da internal structure. Não existe filtro de consumo prévio dos pivôs.
2. O pivô é armazenado com `price_low=price_high=extremo[i]` e `formed_at=timestamp[i]`, embora precise de cinco candles posteriores. A lista não traz timestamp de confirmação nem estado provisional.
3. Definir `TR[0]=high[0]−low[0]`. Para `i>0`, `TR[i]=max(high[i]−low[i], abs(high[i]−close[i−1]), abs(low[i]−close[i−1]))`.
4. No dashboard, a tolerância relativa da **lista inteira** é `τ=0.5 × mean(TR[i]/close[i])`. Se essa média não for positiva, usar `0.0005`. Sem configuração ATR, a classe usaria diretamente `tolerance_pct`. A unidade de `τ` é uma fração do preço, não um percentual já multiplicado por 100, tick ou ATR de Wilder.
5. Ordenar os pivôs por preço crescente. Abrir um grupo com o primeiro pivô. Para cada próximo preço `p`, usar `a`, o preço do primeiro pivô do último grupo: anexar se `abs(p−a) <= a×τ`; caso contrário, abrir outro grupo. A âncora é sempre o **menor preço** do grupo, tanto em EQH quanto em EQL. Não usar média móvel do cluster nem o último membro como âncora.
6. Emitir um EQ somente para grupo com pelo menos três pivôs. O mínimo é inclusivo. Grupos de um/dois membros existem internamente, mas não viram pools.
7. `price_low=min(preços)`, `price_high=max(preços)`, `formed_at=max(timestamps dos pivôs)`. O detector emite uma **faixa**, não um único preço central. `symbol` e `timeframe` vêm do último pivô cronológico. Não persiste o número de toques, IDs dos pivôs, primeiro toque ou ID estável do pool.
8. Definir `first=min(timestamps)` e `last=max(timestamps)`. Somar o volume **inteiro** de cada candle com `first <= timestamp <= last` cuja faixa intersecte `[price_low, price_high]`. Não ratear volume pela fração de interseção. `strength=min(1, area_volume/(mean(volume de todos os candles)×118))`; se volume médio não positivo, strength=0.
9. Inicialmente `is_mitigated=False`, `invalidated_at=None`, `breached_at=None`, `sweep_rejected=False`. O detector não faz lifecycle; o pós-processamento faz.
10. Para EQH, após `formed_at` estritamente: primeiro `high > price_high` define `invalidated_at`, `is_mitigated=True`; `sweep_rejected=(close <= price_high)` nesse candle. Primeiro `close > price_high` define `breached_at`. Para EQL: primeiro `low < price_low`, rejection se `close >= price_low`, breach se `close < price_low`. Igualdade exata não consome nem dá breach. Ambos os timestamps podem coincidir. Breach posterior não apaga o rejection da primeira varrida.
11. Consumo pré-fornecido por um caller é autoritativo para a metade sweep; a rotina ainda procura breach se ausente. Os validators exigem faixa válida e `breached_at >= invalidated_at` quando ambos existem, mas não estabelecem todas as implicações semânticas possíveis entre flags.

**Qual preço é “o nível”?** Lifecycle, grab e label usam extremo externo: high do EQH, low do EQL. Scoring e alvos EQ do Hunt usam `(low+high)/2`. Proximidade visual de alvos usa a borda interna: low do EQH acima do preço, high do EQL abaixo. O renderer desenha ambas as bordas. As diferenças por pivô estão registradas no baseline; substituir tudo por uma média mudaria várias semânticas.

O ranking atual já existe: `score=.4×distance_score+.4×(100×strength)+.2×timeframe_score`; distance score cai linearmente até zero a 5% do preço atual, usando o midpoint. Pesos de TF: M5=.20, M15=.35, H1=.65, H4=.80. Não foi criado score novo. `touch_score` é nome legado para strength de volume, não contagem de toques. Não há `confidence` no EQ.

## 3. Clustering, duplicidade e identidade

A perto de B e B perto de C **não** é suficiente para colocar C com A. C precisa caber na tolerância da âncora A. Cada pivô pertence a um único grupo em um snapshot. Os intervalos de preço dos grupos são disjuntos: um preço fora da faixa admitida abre o próximo grupo, nunca volta a um anterior.

Medição principal: 29.195 pivôs candidatos, 4.613 EQs finais. **Zero** bandas EQ do mesmo lado sobrepostas e **zero** pivôs compartilhados entre grupos no mesmo snapshot. Portanto deduplicação estática não é a primeira correção sustentada por esta evidência.

No replay, a unidade é diferente: existem 11.289 transições entre grupos que compartilham pivôs; 2.246 conservam exatamente a faixa de preço com membership diferente; 6.325 deslocam `formed_at` para frente; 2.413 pares de transição passam de geometria anterior consumida para geometria nova viva. São contagens de transições comparadas, com dependência e possível multiplicidade, **não 2.413 pools independentes defeituosos**. Exemplos estão em `provenance_replay[].reset_examples`.

A pergunta relevante é identidade ao longo do tempo: a recomposição pode usar pivôs que já tiveram seus extremos ultrapassados, mudar a última formação e reabrir a varredura a partir dessa nova data. Foram encontrados 12.243 pivôs de origem já atravessados entre seu timestamp e a formação do cluster final. Isso não é filtrado pela regra atual.

As contagens de proximidade simultânea ≤0.1/0.25/0.5/1 ATR estão separadas por TF/lado no apêndice. Nenhum par vivo esteve a ≤0.1 ATR no replay amostrado; a 0.25 ATR houve 24 observações, todas EQL. Proximidade não é identidade de pool. A demonstração de “duplicata/confusão” deve dizer se os níveis realmente coexistiram.

## 4. Causalidade e repaint

**Prova isolada:** manter os mesmos pivôs confirmados de um prefixo e apenas substituir a tolerância desse prefixo pela da janela final. Houve mudança em 718/1.266 comparações, em 190/211 charts, somando 3.455 memberships na diferença simétrica. Isso isola volatilidade futura sem atribuir a mudança a pivôs novos.

**Força:** em 109.546 de 113.822 comparações com membership inalterado, `strength` mudou. O numerador de volume pode estar congelado, mas a média de volume do denominador usa a janela completa.

**Borda esquerda:** remover o primeiro quarto da janela gerou 1.850 diferenças de memberships, comparando também grupos antigos cujos pivôs sobrevivem à borda de confirmação. Esse teste mistura perda de contexto, reancoragem do agrupamento e normalização; não atribuímos todos os casos ao ATR.

| Fenômeno | Classificação | Interpretação |
|---|---|---|
| Pivô só conhecido cinco candles depois, desenhado na vela de origem | A — semântico | É backdating de confirmação; não se deve avaliar ação histórica como se estivesse disponível em `formed_at` |
| Última vela de confirmação ainda aberta muda o extremo e remove o pool | B/C | Intrabar pode mudar, mas EQ não tem flag provisional; B aceitável exigiria identificação explícita que hoje falta |
| Volatilidade futura altera associação dos mesmos pivôs antigos | D; C para leitura histórica causal | Dependência da janela comprovada, não simples espera por confirmação |
| Volume futuro muda strength de cluster idêntico | D/E; C se usado como atributo histórico | Afeta opacidade, dots e score; não é somente cosmético em todos os consumidores |
| Novo pivô cresce/reparte cluster, altera faixa e formed_at | A para cluster atual; risco C na história | Atualização corrente pode ser semântica; apagar identidade/consumo anterior não pode ser ignorado por um replay |
| Wick/breach novos em uma geometria congelada | A | Atualização normal; testes preservam o primeiro sweep e distinguem close posterior |
| Close da última vela aberta oscila para além/de volta | B/C | `breached_at` pode aparecer/sumir; domínio EQ não marca pending |
| Seleção dos 2 alvos por lado muda com preço; memória muda com estrutura | E | Visibilidade atual muda sem que isso, isoladamente, signifique bug do detector |

O replay candle a candle em BTC/ETH/SOL × M15/H1/H4 confirmou o problema; nele foram 116 diferenças de memberships nos 54 testes isolados. As 225 coortes observadas nesse replay não são os 243 clusters finais: linhagem, disponibilidade e crescimento são objetos diferentes. Não comparar reações dos clusters finais como se fossem sinais historicamente publicados.

Ao acrescentar somente a última vela original, 27/422 comparações chart/lado mudaram geometria/formed_at: 28 assinaturas adicionadas e 19 removidas. Isso mistura confirmação normal, tolerância e recomposição; **não** é uma contagem de 27 pools provisional. O teste intrabar sintético isola separadamente a remoção de um pivô na última vela de confirmação.

Os testes de pesquisa **caracterizam** os problemas e passam quando o comportamento atual é reproduzido. Não são testes de uma correção já implementada.

## 5. Lifecycle e visual

`LiquidityZone` não tem `died` ou `end_time`. `died` é construído pelo PISO a partir de `invalidated_at`; `end_time` pertence a outras fontes. No EQ do gráfico, a faixa começa em `formed_at` — **último** pivô, apesar de um comentário dizer “first touch” — e termina em `invalidated_at` ou num sentinel futuro se ainda estiver viva.

Wick consome o pool como alvo atual: exclui do ranking. Close posterior registra outra informação, o breach. Um nível consumido pode continuar produzindo reações, mas isso não prova que continua contendo o mesmo pool de ordens. O domínio descreve memória, e o gráfico pode preservar memória de consumo; “nível de reação” e “liquidez ainda não consumida” precisam permanecer separados.

A memória desenhada é seletiva: no máximo três grabs do lado consumido pela tendência atual, desde o último BOS/CHoCH não provisional. Há um grab por candle/lado, potencialmente agregando EQ e OB. Para um grab com vários EQ, `backingZone` escolhe a zona de maior strength. Nem todo EQ invalidado permanece desenhado.

Alvos: no máximo dois EQH acima e dois EQL abaixo do preço. A seleção é por proximidade, não por score. Assim o chart tem no máximo quatro bandas ativas mais até três bandas EQ de memória. O renderer usa mesma cor-base para os lados, fill 0.06–0.16 escalado por strength, fator 0.4 para swept, altura mínima de 3 pixels, bordas sólidas/dashed e terminador conforme rejection. Não medimos colisão real de labels/pixels.

Existe **pending no grab**, não no EQ: `rejection_confirmed` pode ser `None` enquanto não se completam dois candles incluindo o da varrida. Labels `⚡?`, `⚡`, `⚡✕` expressam essa confirmação. `sweep_rejected` da zona é apenas o resultado do primeiro candle e pode diferir da confirmação agregada do grab. O Hunt também impede tratar consumo no candle ainda aberto como captura confirmada. Não estender essas proteções à entidade EQ por suposição.

Revisit em até 40 candles, depois de sair e voltar à banda, foi frequente após wick e após close. Comparador: pools de um único pivô confirmado, casados por símbolo, TF, lado, quarto temporal, ±80 candles, volatilidade, bucket de idade e distância anterior ao consumo. Houve **4.925 observações elegíveis e 2.029 casadas**. Deltas variam de sinal entre TF/lado e não demonstram que wick deva deixar o pool ativo. Reações, controles, censura e tempos constam do apêndice/JSON.

## 6. Qualidade: o que foi e o que não foi estabelecido

Nos recortes de qualidade, spread, strength e distância são congelados na primeira observação; idade é medida no evento de contato; estrutura é a leitura do snapshot na data do contato. Isso evita misturar idade atual com idade no evento.

Os resultados não autorizam “3 é melhor que 2”. Produção não emite níveis de 2 toques. As coortes congeladas na primeira observação são predominantemente de 3 toques; 5+ tem amostra muito pequena, porque crescimentos posteriores não são contados como nova coorte. Os clusters finais têm 2.111 níveis com 3, 1.266 com 4 e 1.236 com 5+; usar esses números finais como atributos de formação vazaria o futuro.

Idade e distância têm efeitos que variam por lado, TF e bloco, sem gradiente robusto certificado. Por exemplo, o primeiro contato EQL M15 em h10 tem ΔP(MFE>MAE) de +9,5 pontos percentuais contra controle de candles; seus quatro blocos têm aproximadamente −10,5, −0,2, +12,7 e +23,4 pp. Esse achado não é estável nos quatro períodos e não passa a disciplina solicitada. EQH H4 e EQL H4 não devem ser agregados com M15 para “salvar” resultado.

Cada eixo foi descrito separadamente: toques, spread, idade, distância, strength/volume normalizado, estrutura e estado de sweep. Recência do último **pivô do cluster** é a mesma variável que idade desde formed_at; proximidade entre pivôs é expressa pelos gaps de preço/spread e gaps temporais, não por indicador adicionado. Volume do candle inteiro que intersecta o pool não prova volume de ordens naquela faixa.

Para alegações de qualidade, há controle temporal e volatilidade por evento; para lifecycle há também matching de idade e distância. **Não há identificação causal completa dos gradientes entre todos os buckets**: eixos se correlacionam, eventos/controles podem compartilhar movimentos, não há holdout nem correção de múltiplas comparações. Por isso não se declara nenhum quality tier real.

BOS/CHoCH, POI, liquidação, profile e VWAP foram quantificados como geometria do snapshot final. Isso responde a sobreposição de referências, **não** a informação nova nem confluência causal histórica. O contexto estrutural usa a implementação real do frontend, mas não foi certificado pelo replay completo da estrutura; permanece diagnóstico. Profile e liquidation snapshot não podem ser retroprojetados como fatos conhecidos no nascimento do EQ.

## 7. Painel, controles e limites explícitos

- 211 fixtures disponíveis: 71 M15, 70 H1, 70 H4; 71 símbolos. LRC só tem M15. EOS tem snapshots de maio de 2025, com apenas 214 candles H4 antes da exclusão final. Os demais terminam em setembro de 2026. O apêndice expõe datas e hashes por arquivo, e inclui sensibilidade no painel recente/comum sem EOS/LRC.
- Excluir sempre a última vela de cada snapshot para métricas do detector. Assim os charts completos usam 1.199 candles; o H4 curto usa 213. A paridade com payload foi checada separadamente, incluindo a última vela original: **422/422 chart/lado**.
- Replay principal começa com 40 candles de aquecimento e segue a cada 20: pode observar formação até 19 candles depois; formações anteriores ao início têm atraso maior. BTC/ETH/SOL também têm replay a cada candle após os mesmos 40 candles de aquecimento. Não tratar o replay amostrado como um log tick a tick.
- Coortes congelam geometria e não permitem reaproveitar pivôs para inflar a amostra de qualidade. O detector de produção **continua** livre para reagrupá-los; essa política de amostragem não é proposta de produção.
- M5 adicional: três símbolos, 69 EQs finais, cache até 05/09/2026, outro período. Dados e resultados separados; sem fontes estruturais nesse cache.
- Divisão em quatro blocos é por posição dentro de cada chart, mantendo controles na mesma janela temporal do símbolo/TF. Não é um teste em quatro regimes econômicos independentes.
- “Fora da faixa útil” foi operacionalizado como >4 ATR para diagnóstico. Sem viewport capturado, não se afirma que estava fora da tela. Maioria das exposições vivas está nesse bucket em vários recortes, mas o frontend já restringe os alvos.
- Casos reais A–G estão no apêndice. “Limpo que funciona” é caso pós-hoc com rejection e MFE>MAE h10. “Falso equal” permanece candidato por spread largo: não há verdade de referência que permita declarar falso.

## 8. Respostas às 25 perguntas de entrega

| # | Pergunta | Resposta |
|---|---|---|
| 1 | Onde é implementado? | Backend Python, `equal_levels.py`, factories em `dashboard_data.py`; lifecycle em `mitigation.py`; apresentação em `MainChart.tsx`/`EqlZonesPrimitive.ts`. Mapa completo acima. |
| 2 | Definição exata? | Três fractals estritos agrupados por menor preço com tolerância relativa global; faixa min/max; formação no último pivô; volume relativo como strength. Algoritmo formal acima. |
| 3 | Escolha de pivôs? | Extremos locais com cinco candles de cada lado, sem internal structure, sem filtro de consumo, sem flag provisional. |
| 4 | Igualdade? | `p−anchor <= anchor×0.5×mean(TR/close)` na janela recebida. |
| 5 | Tolerância causal? | Disponível no snapshot atual, mas não causal como atributo histórico: futuros candles reescrevem igualdade antiga. |
| 6 | Repaint? | Sim, tolerância, strength, crescimento/repartição e borda esquerda; confirmação e atualizações de lifecycle precisam de classificação separada. |
| 7 | Clustering? | Guloso ordenado por preço, âncora fixa no menor membro, não transitivo. |
| 8 | Duplicatas? | Não como bandas estáticas do mesmo lado; há proximidade e reciclagem de membership/identidade entre snapshots. |
| 9 | Quantas? | Zero sobreposições estáticas; 24 observações de pares vivos ≤0,25 ATR; 11.289 transições com pivôs compartilhados. Unidades e TFs separados no apêndice. |
| 10 | Toques importam? | Não demonstrado de forma robusta; 2 ausente em produção e 5+ raro no nascimento observável. Não mudar mínimo. |
| 11 | Idade importa? | Sem regra monotônica validada; não criar expiry. |
| 12 | Distância importa? | Descreve carga visual e seleção atual; não há limiar de qualidade validado. |
| 13 | Lifecycle correto? | Rotina é consistente para faixa congelada; identidade recalculada pode renovar a janela de consumo. A distinção pool/memória é necessária. |
| 14 | Wick vs close? | Sim: wick consome como alvo; close marca breach. Revisit posterior não demonstra que o pool original permanece intacto. |
| 15 | EQH/EQL simétricos? | Mesma configuração/operações espelhadas; resposta observada não é igual e varia por TF/período. |
| 16 | Resultado por TF? | 1.496 EQs M15, 1.518 H1, 1.599 H4; métricas separadas por lado no apêndice. M5=69, diagnóstico separado. |
| 17 | Ruído visual? | Proximidade, alvos distantes, dots/score instáveis e identidade refeita merecem exame; chart já limita a quatro alvos e três memórias. Não confundir contagem backend com marcas visíveis. |
| 18 | Eixos de qualidade real? | Nenhum certificado com estabilidade, controle suficiente e robustez necessários para produção. Há descrições exploratórias, não um score aprovado. |
| 19 | Bug concreto? | Contraexemplos reproduzem futuro alterando clusters antigos, strength e pools consumidos reaparecendo vivos; há também comentários de “first touch”/“touch count” divergentes do comportamento. |
| 20 | Primeira hipótese? | H1: causalidade e disponibilidade/identidade histórica, mantendo todos os thresholds. |
| 21 | O que simplificar? | Vocabulário de formação/confirmação, pool consumido/memória e strength de volume; evitar tratar recência do último pivô e idade como eixos independentes. Sem mudança nesta etapa. |
| 22 | O que preservar? | Separação EQH/EQL, significado de pool, bordas da faixa, distinção wick/close, controle de TF, mínimos/tolerância atuais até experimento. |
| 23 | Visual mais seguro? | Primeiramente tornar origem/estado auditáveis em replay de pesquisa; não adicionar deduplicação, expiry, cor ou score sem prova. Qualquer futura UI deve esclarecer confirmação/memória, não inventar força. |
| 24 | Lógica mais promissora? | Experimento isolado de normalização causal e identidade estável, com rastreamento de pivôs/tempo conhecido, mantendo 3/5/0,5. Nenhuma alternativa foi implementada. |
| 25 | Evidência suficiente para mudar produção agora? | **NÃO. Encerrar E0 aqui.** A evidência identifica problemas, mas não seleciona uma substituição validada. |

## 9. Hipóteses priorizadas, sem implementação

| Ordem | Hipótese | Impacto | Evidência E0 | Risco de regressão | Facilidade de teste |
|---|---|---|---|---|---|
| 1 | H1 — causalidade | Alto: presença, identidade, score e desenho histórico | Forte: testes sintéticos, replay, isolamento de tolerância | Alto se corrigir sem preservar semântica | Alta para invariantes de prefixo |
| 2 | H2 — clustering/identidade | Alto na continuidade histórica; baixo para dedupe estático | Forte para transições; zero duplicata estática | Alto: alterar pertencimento altera pools | Alta com ledger de membership |
| 3 | H4 — lifecycle | Alto: pool vs memória e reabertura | Forte mecanicamente; qualidade posterior inconclusiva | Alto | Média: controles e geometria/identidade congeladas |
| 4 | H8 — limpeza visual | Médio | Limites atuais e instabilidade conhecidos; pixels não medidos | Baixo para esclarecimento, alto para esconder dados | Média: replay visual com mesmas séries |
| 5 | H3 — largura de tolerância | Potencialmente alto | E0 prova dependência temporal, não largura ideal | Alto | Média depois de H1; não fazer grid agora |
| 6 | H7 — distância/relevância | Médio | Muitas exposições distantes; efeito variável | Médio | Alta com matching de distância/idade/volatilidade |
| 7 | H5 — idade/expiry | Médio | Não há gradiente robusto que justifique prazo | Alto: pode eliminar memória útil | Média |
| 8 | H6 — número de toques | Médio | Produção 3; pouca cobertura causal de 5+ | Alto | Média: precisa landmarks por estado do cluster |
| 9 | H9 — strength/quality tier | Incerto | Strength varia com futuro; nenhum ranking validado | Alto: falsa confiança | Baixa antes de H1 e amostra independente |

Registro para uma **eventual** próxima pesquisa: comparar versões de leitura causal/identidade em harness, congelar os mesmos 3 toques, 5 velas e fator 0,5; testar append, left-window, crescimento e histórico de sweep. Exigir preservação de decisões após tempo de conhecimento e depois repetir o painel, controles, quatro blocos e robustez por símbolo. Isso é hipótese documentada, não autorização nem implementação da próxima etapa.

## 10. Reprodução e artefatos

```bash
node --experimental-strip-types frontend/research/eqLevelsContext.ts /tmp/eq_context_baseline.json /tmp/eq_visual_baseline.json
poetry run python research/eq_levels_audit.py --context /tmp/eq_context_baseline.json
poetry run python -m research.eq_levels_followup
poetry run python -m research.eq_levels_repaint
poetry run python research/eq_levels_audit.py --symbols BTCUSDT ETHUSDT SOLUSDT --step 1 --context /tmp/eq_context_baseline.json --json research/eq_levels_replay_baseline.json
poetry run python -m research.eq_levels_followup --json research/eq_levels_replay_baseline.json
poetry run python -m research.eq_levels_m5
poetry run python research/eq_levels_audit.py --fixtures /tmp/eq_m5_fixtures --json research/eq_levels_m5_baseline.json
poetry run python -m research.eq_levels_report
poetry run pytest -q research/test_eq_levels_audit.py liquidity_hunter/tests/liquidity/detectors/test_equal_levels.py liquidity_hunter/tests/liquidity/detectors/test_swing_points.py liquidity_hunter/tests/liquidity/test_mitigation.py liquidity_hunter/tests/app/test_liquidity_grabs.py liquidity_hunter/tests/scoring/test_engine.py
```

Entradas são as fixtures locais do PISO; as datas/hashes devem ser preservadas. Capturar novamente de uma API móvel produz outro painel, não reprodução exata deste baseline. Os scripts não baixam dados nem alteram regras reais. Os novos testes são de caracterização causal/lifecycle e de integridade de medição, sem estratégia de entrada.

Validação final: **82 testes passaram**; Ruff passou nos scripts/testes Python de pesquisa; o helper TS executou com a função real de contexto; `git diff` dos arquivos de produção permaneceu vazio.
