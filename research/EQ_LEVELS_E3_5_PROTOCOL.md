# EQ E3.5 — memória retida e crescimento do histórico

Objetivo: quantificar o tamanho do estado Python do Replay de pesquisa e seu
crescimento dentro do histórico disponível, antes de decidir sobre integração.

## Método

Usar o manifesto E0 e o baseline E3 completos, verificando identidade e hash de
cada fixture. Excluir a última barra, como no restante do estudo. Observar o
Replay em 25%, 50%, 75% e 100% das barras fechadas; ao final, reconciliar todos
os campos de `shadow_metrics` com o baseline.

O estimador percorre os objetos alcançáveis pelo Replay, somando
`sys.getsizeof`: containers, atributos de instância e slots, inclusive os
campos Pydantic. Referências compartilhadas são contadas uma vez por medição;
ciclos terminam. Classes e infraestrutura global do interpretador não são
percorridas. Os testes cobrem compartilhamento, ciclos, slots e ausência de
mutação no replay observado.

Registrar em cada fronteira:

- bytes estimados de objetos Python retidos pelo Replay completo;
- bytes estimados da projeção de estado excluindo `snapshots`, `events` e
  `sweeps`, sem remover dados do Replay real;
- tamanho do checkpoint JSON de candles em bytes;
- quantidade de snapshots, eventos, sweeps, versões, pivôs e nós de linhagem.

A projeção sem logs serve para localizar o custo da instrumentação. Não é um
candidato executável, não elimina candles/versões/linhagens e não valida poda.
Referências compartilhadas impedem interpretar componentes como parcelas
independentes perfeitamente somáveis. Medições em diferentes charts também
podem compartilhar constantes no mesmo processo.

## Limites e regra de decisão

O tamanho estimado do grafo Python não é RSS, pico de alocação ou consumo total
do serviço. Exclui importações, fragmentação do allocator, temporários de
cálculo/serialização e buffers nativos não expostos pelos objetos. Não houve
orçamento de memória definido previamente; não será inventado um limite após
observar os dados. O custo do Replay inclui R, N e L e a instrumentação de
pesquisa, não representa isoladamente o incremento de memória de N em produção.

As curvas usam apenas os prefixos das fixtures existentes. Elas não provam
comportamento para séries mais longas, outros períodos ou todos os charts
simultaneamente residentes. O tempo inclui as travessias de medição, não é o
runtime do shadow simples.

O parecer de integração deve considerar conjuntamente a qualidade E2, a
continuidade E3.4, as limitações de compactação E1.4 e estes custos. Correção
mecânica não será tratada como evidência de benefício do indicador.

Execução: `poetry run python -m research.eq_levels_e3_5`.
Artefatos: [auditor](eq_levels_e3_5.py), [testes](test_eq_levels_e3_5.py).
Saída local ignorada pelo Git: `research/.replay_cache/eq_e3_5.json` e journal
`.partial.jsonl`. O painel final registra hashes do código, dados e baselines.
