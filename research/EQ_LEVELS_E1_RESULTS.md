# EQ E1 — causalidade e identidade histórica

E1 foi executada somente em pesquisa, preservando os parâmetros atuais: três toques, cinco candles de lookback e tolerância de 0,5 da média de TR/close. O código de produção não foi alterado.

O experimento compara três braços:

| braço | regra | finalidade |
| --- | --- | --- |
| R | detector e lifecycle nativos, reconstruídos a cada candle fechado | referência atual |
| N | atualiza propostas quando surge pivô confirmado; congela `strength` por membership | remover repaint causado apenas por janela/volume |
| L | N com versões de pivôs e consumo persistente na linhagem conectada | testar se um pool consumido pode reaparecer como pool vivo |

Cada versão guarda pivôs, instante em que ficou conhecida, faixa, força, ancestrais e eventos de sweep/breach. Um evento descoberto ao reconstruir uma janela conserva `market_at`, mas seu registro aparece em `observed_at`; não há backdating de conhecimento.

## Resultado principal

R e N têm densidade quase idêntica. Isso é esperado: N corrige estabilidade de atributos, mas ainda admite uma nova versão quando chega um novo pivô. L reduz bastante os níveis vivos, porque bloqueia qualquer descendente de uma linhagem historicamente consumida.

| TF/lado | R vivos médio / p90 | N vivos médio / p90 | L elegíveis médio / p90 | exposições bloqueadas por L |
| --- | ---: | ---: | ---: | ---: |
| M15/EQH | 0,652 / 2 | 0,652 / 2 | 0,368 / 1 | 24.110 |
| M15/EQL | 2,223 / 5 | 2,220 / 5 | 1,080 / 3 | 97.019 |
| H1/EQH | 0,856 / 3 | 0,857 / 3 | 0,557 / 2 | 25.178 |
| H1/EQL | 1,513 / 4 | 1,513 / 4 | 0,752 / 2 | 63.902 |
| H4/EQH | 1,797 / 5 | 1,799 / 5 | 0,689 / 2 | 92.063 |
| H4/EQL | 1,087 / 3 | 1,087 / 3 | 0,573 / 2 | 42.651 |

N muda a identidade temporal dos snapshots em milhares de pontos, mas não reduz o número médio de zonas porque continua permitindo crescimento legítimo do cluster. Isso separa claramente dois problemas: causalidade de atributos e política de elegibilidade.

## Qualidade descritiva

O delta é a diferença em `P(MFE > MAE)` contra controles casados por símbolo, TF, lado, quarto temporal, distância, idade e volatilidade. Ele é descritivo; não é retorno nem prova de edge.

| TF/lado | R h10 (n/casados/Δ) | N h10 | L h10 | versões bloqueadas h10 |
| --- | ---: | ---: | ---: | ---: |
| M15/EQH | 896 / 122 / +0,008 | 906 / 123 / +0,008 | 533 / 60 / +0,125 | 373 / 63 / −0,103 |
| M15/EQL | 1.059 / 178 / −0,001 | 1.060 / 178 / −0,001 | 540 / 77 / +0,091 | 520 / 101 / −0,071 |
| H1/EQH | 988 / 148 / −0,009 | 990 / 150 / −0,002 | 582 / 73 / +0,046 | 408 / 77 / −0,048 |
| H1/EQL | 922 / 127 / +0,001 | 925 / 127 / +0,001 | 517 / 62 / −0,008 | 408 / 65 / +0,010 |
| H4/EQH | 1.204 / 204 / +0,042 | 1.200 / 203 / +0,038 | 575 / 72 / +0,019 | 625 / 131 / +0,048 |
| H4/EQL | 1.221 / 234 / +0,002 | 1.219 / 234 / +0,006 | 607 / 98 / +0,034 | 612 / 136 / −0,013 |

Os sinais de L mudam entre TF e lado, e o braço bloqueado costuma apresentar o sinal oposto ao braço elegível. Isso é compatível com seleção: L retira justamente pools que depois poderiam reagir. Não é evidência para ativar L.

R versus N é praticamente indistinguível nos resultados de reação. Portanto congelar `strength` e atualizar por relógio de pivô resolve uma classe causal real sem demonstrar benefício de qualidade. L não passa o critério de robustez.

## O que E1 provou

1. Uma vela aberta não modifica o estado confirmado.
2. A primeira publicação ocorre apenas depois das cinco velas fechadas necessárias, embora `formed_at` continue sendo o timestamp do pivô.
3. Volume e volatilidade futuros não mudam a força de uma versão N já publicada.
4. O crescimento de um cluster cria uma versão nova, mantendo a versão anterior e seu evento de consumo.
5. Wick e close permanecem eventos distintos.
6. Um checkpoint contendo a origem e a linhagem reproduz a execução contínua após receber a cauda.
7. O detector nativo continua sendo reproduzido nos checkpoints; não houve alteração do agrupamento dentro de R.
8. O braço L elimina muitos reusos, mas também elimina revisitas e versões legítimas; a reação não é estável entre TFs.

## Decisão

Não aplicar L em produção. O filtro de linhagem é agressivo demais e seu aparente lift vem acompanhado de forte seleção.

Não aplicar N em produção ainda. N é a intervenção tecnicamente mais promissora: elimina repaint de `strength` e impede volatilidade futura, sem mudar thresholds. Porém E1 não mediu custo operacional de persistência, migração, cache distribuído, múltiplos símbolos ou reinício do backend. A persistência usada aqui guarda a série desde a origem e é um artefato de pesquisa, não uma implementação pronta.

Próxima etapa recomendada: E1.1, um replay de contrato de estado com uma versão causal mínima, sem L. Medir apenas invariantes de publicação, restart/checkpoint, crescimento, múltiplos consumidores e compatibilidade de payload. Depois repetir o painel de qualidade em um período de holdout. Se o contrato passar, propor uma mudança produtiva pequena e revisável para causalidade de `strength`/tolerância. Nenhum threshold, expiry, deduplicação visual ou score deve ser alterado nessa etapa.

## Reprodução

```bash
poetry run python -m research.eq_levels_e1 --workers 2
poetry run pytest -q research/test_eq_levels_e1.py
poetry run ruff check research/eq_levels_e1.py research/test_eq_levels_e1.py
```

Artefatos: [harness E1](eq_levels_e1.py), [protocolo](EQ_LEVELS_E1_PROTOCOL.md), [testes](test_eq_levels_e1.py) e `research/eq_levels_e1_baseline.json` (gitignored). A execução usa os hashes das fixtures E0 e falha se o commit de produção mudar.
