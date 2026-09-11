# EQ E1 — protocolo do experimento

Escopo autorizado: causalidade e identidade histórica em pesquisa. Não modificar produção, parâmetros, UI ou significado de EQ para construir uma estratégia. Baseline é E0; o manifesto exige o mesmo commit de produção e os mesmos hashes das fixtures.

## Intervenções fixadas antes da leitura do painel completo

| Braço | Intervenção | O que permite isolar |
|---|---|---|
| R | Snapshots equivalentes ao detector real, em cada candle fechado | Referência operacional; não usar apenas o snapshot final como história |
| N | Reagrupar apenas quando confirma novo pivô **do mesmo lado**; congelar strength de um membership já publicado | Evitar que só volatilidade/volume, sem novo toque confirmado, reescrevam atributos publicados |
| L | Mesmas propostas N, com histórico de versões e consumo persistente na componente de pivôs compartilhados | Testar se impedir reativação de linhagem preserva semântica sem destruir cobertura |

Em todos: 3 toques, 5 candles por lado, fator 0,5 e saturação 118. Usar funções reais para fractals, agrupamento, área de volume e lifecycle. N muda o **instante de atualização**; manter o fator não significa que a regra de admissão seja idêntica. L muda elegibilidade, explicitamente como hipótese experimental.

Versionar não é escolher um score. Uma versão identifica o conjunto exato de timestamps dos pivôs. O registro contém faixa, `formed_at` da regra original, `known_at`/`known_time`, parâmetros observados e versões ancestrais. O diário só recebe novos registros; não altera publicações antigas. Exibição/retirada e consumo são eventos com tempo de observação separado do tempo de mercado.

N conserva o significado atual de cada snapshot no instante do pivô, inclusive grupos que já aparecem consumidos. Não retrodata sua **publicação** ao timestamp do último pivô. Uma geometria nova ganha versão nova; uma versão que reaparece mantém a força publicada originalmente. Eventos de wick/breach continuam sendo lidos pelo helper real.

L é deliberadamente conservador: se qualquer versão de uma componente histórica de pivôs foi observada consumida, descendentes conectados deixam de ser elegíveis como pools vivos. O vínculo pode ser transitivo entre versões; isso **não** altera o agrupamento de preços do detector, mas pode ligar muitos pools ao longo do tempo. Essa política pode estar errada. Não interpretar zerar reativação por construção como comprovação de qualidade.

A marca de consumo herdado fica no domínio de pesquisa. Não copiar `invalidated_at` de faixa antiga para uma nova `LiquidityZone`: o evento antigo refere-se à geometria antiga, não necessariamente à borda nova.

## Invariantes e critérios de decisão

1. Replay completo e replay truncado em cada quarto devem produzir publicações, estados observáveis e eventos idênticos até o corte.
2. A primeira publicação não antecede o quinto candle fechado após o último pivô necessário.
3. Vela explicitamente aberta não altera o estado confirmado; revisões intrabar ficam fora deste experimento.
4. Strength de uma versão não muda por volume futuro; crescimento pode criar versão, sem apagar a anterior.
5. Wick e close são eventos distintos, e o primeiro consumo observado não desaparece do diário.
6. Checkpoint persistido + cauda deve reproduzir execução contínua; sem estado anterior, testar e reportar dependência da borda esquerda. Não prometer invariância impossível a um detector sem histórico.
7. Referência R deve coincidir com `.detect` e `mark_swept_zones` reais nos checkpoints e testes de prefixos reais. Tolerância numérica somente para strength (`1e-10`), sem margem adicional no agrupamento.
8. Reportar custo: exposições vivas, versões, grupos alterados, cobertura de sweeps, eventos removidos e tamanho das linhagens. Zero repaint sozinho não autoriza produção.
9. Reação é descritiva em h=5/10/20/40. Não ajustar parâmetros pelo resultado. Comparar por TF/lado; quatro blocos e robustez por símbolo. Não misturar M15/H1/H4 para resgatar hipótese.
10. Se estabilidade exigir uma política que suprima pools legítimos ou os controles forem insuficientes, rejeitar a política para produção e registrar o limite. Rastreabilidade pode ser aprovada como contrato de pesquisa separadamente da elegibilidade.

## Amostra e métricas

Mesmas 211 fixtures de E0, excluindo o último candle; agora replay **candle a candle desde o início**, sem o aquecimento artificial de 40 candles do harness E0. São objetos de medição diferentes: não comparar diretamente contagens de coortes congeladas de E0 com versões de E1.

O sweep medido atravessa uma faixa que estava publicada e viva **antes** do candle do evento. Não incluir como sinal observável uma varrida histórica que só aparece ao recompor a geometria depois. Eventos simultâneos são elegíveis com base no estado anterior ao candle, evitando dependência da ordem em que percorremos as zonas.

MFE/MAE usam close do evento, candles seguintes, direção descendente EQH/ascendente EQL e normalizador causal expansivo de TR/close. Controles: sweeps de R, mesmo símbolo/TF/lado/quarto temporal, distância temporal >40 e ≤120 candles, mesmo bucket de idade/distância anterior e volatilidade relativa 0,8–1,25; até três controles mais próximos, com reposição. Sem controle elegível, registrar ausência, não inventar lift. Controles podem compartilhar movimento de mercado; não inferir independência.

As fixtures têm EOS antigo e LRC incompleto. Reportar também painel recente comum, sem mudar a lista conforme o resultado. Não há novo holdout; critérios causais são verificáveis, superioridade de reação exige evidência adicional.

## Limite de persistência

Checkpoint de pesquisa guarda os candles desde a origem e reconstrói o estado. É correto, porém não representa solução de memória limitada, storage, migração, distribuição ou invalidação de cache do backend. A API atual recompõe a janela e não persiste esse diário. Esses problemas não são resolvidos implicitamente pelo teste de checkpoint.
