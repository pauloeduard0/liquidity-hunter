# EQ E1.2 — contrato de estado e payload

E1.2 testou se a proposta causal pode sobreviver a restart e chegar ao frontend sem criar uma API paralela. Nenhum módulo de produção foi conectado ou alterado.

## Contrato validado

O envelope versionado contém `schema`, `producer`, símbolo, timeframe, último timestamp, contagem de candles, checkpoint e SHA-256 do checkpoint. A validação rejeita estado vazio, schema desconhecido, checksum inválido, identidade divergente, contagem incorreta, timestamp divergente e candles fora de ordem. A migração não tenta adivinhar campos antigos: schema desconhecido falha explicitamente.

O checkpoint atual preserva a origem completa dos candles. Isso é intencional. Cortar para uma cauda arbitrária perde pivôs anteriores, confirmação e linhagem; o teste reproduz essa divergência. Portanto o contrato de restart está correto, mas ainda não é um formato compacto de armazenamento.

O append usa uma guarda CAS: o escritor precisa informar o checksum anterior. Um escritor atrasado falha com `stale state writer`; o append com checksum corrente gera novo estado. Isso cobre o modelo de um escritor por `(symbol, timeframe)` em pesquisa. Não resolve ainda concorrência distribuída, TTL ou invalidação de cache.

## Medição de tamanho

| chart BTC | candles fechados | versões publicadas | zonas no payload N | estado JSON | payload EQ |
| --- | ---: | ---: | ---: | ---: | ---: |
| M15 | 1.199 | 76 | 26 | 214 KB | 7,6 KB |
| H1 | 1.199 | 84 | 30 | 215 KB | 9,0 KB |
| H4 | 1.199 | 69 | 28 | 216 KB | 8,4 KB |

O custo do estado completo é pequeno para um artefato isolado, mas multiplicado por dezenas de símbolos e três TFs já exige política de retenção. Reduzir para 30 candles não é seguro sem persistir pivôs, memberships, strength congelada, versões, consumo e ancestrais. Essa compactação é a próxima pesquisa de storage; não deve ser improvisada no backend.

## Payload

`zone_payload()` projeta as zonas N atuais exatamente para o domínio existente `LiquidityZone`, sem novos campos. A validação aceita todos os campos atuais e rejeita campos desconhecidos ou ausentes. Os consumidores atuais continuam recebendo `liquidity_zones`; nenhum campo frontend foi inventado. A projeção foi validada por roundtrip Pydantic.

O payload é uma lista de zonas publicadas, não um diário. Para preservar observabilidade causal, uma futura API precisará decidir separadamente se expõe `known_at`, versão e eventos. Adicionar esses campos agora quebraria o contrato atual e não é necessário para testar o núcleo.

## Critérios

Passaram oito testes E1.2 e os quatorze testes E1. O contrato demonstra:

- serialização JSON e fingerprint idempotente;
- checksum, schema, contagem, identidade e ordenação protegidos;
- migração explícita;
- append concorrente rejeitando estado obsoleto;
- restart e cauda incremental equivalentes à execução contínua;
- payload compatível com `LiquidityZone`;
- candle aberto sem mutação;
- rejeição de cauda curta sem linhagem.

## Decisão

O braço N pode avançar para uma implementação produtiva **somente** depois de uma decisão de armazenamento. A especificação mínima é: estado por símbolo/TF, um escritor lógico, append idempotente por timestamp, checksum, checkpoint atômico, recuperação após falha e retenção explícita da linhagem necessária. O payload existente pode continuar sendo usado enquanto o diário permanecer interno.

Não implementar ainda. A próxima etapa é E1.3: comparar três políticas de retenção — origem completa, pivôs+versões compactos e janela limitada — com o mesmo replay e medir divergência, tamanho, tempo de restart e comportamento após gap de dados. Só uma política que preserve decisões publicadas pode ser considerada para produção.

Artefatos: [contrato](eq_levels_e1_2.py), [testes](test_eq_levels_e1_2.py), [E1](EQ_LEVELS_E1_RESULTS.md) e `research/eq_levels_e1_2_baseline.json` se um baseline de storage for gerado.
