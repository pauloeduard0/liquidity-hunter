# EQ E0 — apêndice numérico

Gerado por `poetry run python -m research.eq_levels_report`. Este arquivo contém descrições e comparações exploratórias; não valida alterações.

Código de produção: `9119562dd8ae8ac06625cb1dd0d173141fb130df`. 211 snapshots; 71 símbolos. Replay principal a cada 20 candles, com 40 candles de aquecimento, sem o último candle de cada snapshot.

ATR neste relatório = média expansiva causal de TR/close × close do candle avaliado. Não é ATR de Wilder. Métricas normalizadas por formação/observação/evento, conforme o campo. Contagens de pares no replay são **observações de pares**, não pares únicos.

## Painel e funil retrospectivo

| TF/lado | charts | pivôs | pares próximos | clusters (inclui <3) | EQs | vivos | sweeps | rejections | breached |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 15m/EQH | 71 | 4622 | 7616 | 1954 | 739 | 196 | 543 | 258 | 519 |
| 15m/EQL | 71 | 4907 | 8926 | 2016 | 757 | 213 | 544 | 292 | 509 |
| 1h/EQH | 70 | 4895 | 8897 | 2056 | 743 | 71 | 672 | 310 | 661 |
| 1h/EQL | 70 | 4837 | 8898 | 1972 | 775 | 246 | 529 | 281 | 484 |
| 4h/EQH | 70 | 4991 | 9320 | 1991 | 774 | 128 | 646 | 299 | 614 |
| 4h/EQL | 70 | 4943 | 9751 | 1870 | 825 | 184 | 641 | 336 | 617 |

| TF/lado | EQ/cluster | vivos/EQ | sweep/EQ | rejection/sweep | breach/EQ |
| --- | --- | --- | --- | --- | --- |
| 15m/EQH | 0.378 | 0.265 | 0.735 | 0.475 | 0.702 |
| 15m/EQL | 0.375 | 0.281 | 0.719 | 0.537 | 0.672 |
| 1h/EQH | 0.361 | 0.096 | 0.904 | 0.461 | 0.890 |
| 1h/EQL | 0.393 | 0.317 | 0.683 | 0.531 | 0.625 |
| 4h/EQH | 0.389 | 0.165 | 0.835 | 0.463 | 0.793 |
| 4h/EQL | 0.441 | 0.223 | 0.777 | 0.524 | 0.748 |

O funil acima usa a reconstrução final; não deve ser encadeado com contagens dos cohorts abaixo. Um cluster exige vários pares, logo pares → clusters não é uma taxa de retenção simples.

## Coortes observadas e revisitas

Primeira observação congela a geometria. Um pivô já usado impede contar novamente aquela linhagem nas coortes. Isso evita inflar a amostra por crescimento do cluster, mas exclui reciclagens legítimas ou ilegítimas: não reproduz a política futura do detector.

| TF/lado | coortes | já swept ao observar | multi-sweep | 1ª visita ≤40 (n/N) | após wick ≤40 (n/N) | após close ≤40 (n/N) |
| --- | --- | --- | --- | --- | --- | --- |
| 15m/EQH | 717 | 259 | 491 | 264/424 | 298/378 | 317/401 |
| 15m/EQL | 734 | 219 | 498 | 255/484 | 271/364 | 278/354 |
| 1h/EQH | 742 | 237 | 486 | 283/474 | 353/470 | 345/489 |
| 1h/EQL | 765 | 294 | 474 | 218/441 | 243/311 | 243/311 |
| 4h/EQH | 726 | 223 | 529 | 272/491 | 324/451 | 341/465 |
| 4h/EQL | 790 | 251 | 571 | 318/514 | 378/462 | 363/469 |

Após morte, revisita exige ao menos um candle inteiramente fora da banda antes da reentrada. Eventos sem 40 candles de exposição são censurados na taxa ≤40; reações têm censura própria por horizonte.

## Clusters e toques

2 toques não produz nível em produção; não há comparação de qualidade 2 vs 3 neste painel.

| TF/lado | toques | n | spread ATR p50 | spread ATR máximo | intervalo toques p50 | idade final p50 |
| --- | --- | --- | --- | --- | --- | --- |
| 15m/EQH | 2 | 0 | — | — | — | — |
| 15m/EQH | 3 | 368 | 0.382 | 1.002 | 56.500 | 402.500 |
| 15m/EQH | 4 | 191 | 0.423 | 1.685 | 54 | 461 |
| 15m/EQH | 5+ | 180 | 0.446 | 1.525 | 45.000 | 288.500 |
| 15m/EQL | 2 | 0 | — | — | — | — |
| 15m/EQL | 3 | 334 | 0.376 | 1.196 | 64.000 | 497.000 |
| 15m/EQL | 4 | 210 | 0.405 | 0.723 | 53.500 | 342.000 |
| 15m/EQL | 5+ | 213 | 0.436 | 1.656 | 47 | 240 |
| 1h/EQH | 2 | 0 | — | — | — | — |
| 1h/EQH | 3 | 350 | 0.416 | 1.120 | 53.000 | 403.000 |
| 1h/EQH | 4 | 209 | 0.462 | 1.063 | 45 | 527 |
| 1h/EQH | 5+ | 184 | 0.514 | 0.966 | 35.000 | 564.500 |
| 1h/EQL | 2 | 0 | — | — | — | — |
| 1h/EQL | 3 | 375 | 0.385 | 0.862 | 67.500 | 391 |
| 1h/EQL | 4 | 218 | 0.477 | 0.969 | 49.000 | 471.000 |
| 1h/EQL | 5+ | 182 | 0.497 | 0.881 | 38 | 519.500 |
| 4h/EQH | 2 | 0 | — | — | — | — |
| 4h/EQH | 3 | 319 | 0.342 | 0.573 | 52.000 | 410 |
| 4h/EQH | 4 | 233 | 0.404 | 0.652 | 58 | 366 |
| 4h/EQH | 5+ | 222 | 0.445 | 0.809 | 39.000 | 306.000 |
| 4h/EQL | 2 | 0 | — | — | — | — |
| 4h/EQL | 3 | 365 | 0.337 | 0.648 | 64.000 | 406 |
| 4h/EQL | 4 | 205 | 0.392 | 0.624 | 54 | 289 |
| 4h/EQL | 5+ | 255 | 0.421 | 0.588 | 38.000 | 289 |

Os preços, diferenças assinadas do extremo para **cada pivô**, midpoint, gaps e pivôs consumidos estão em `retrospective_levels` no JSON; não foram resumidos em um novo score.

## Densidade e proximidade

| TF/lado | vivos média | p50 | p90 | máximo | alvos exibíveis média | observações >4 ATR | exposições vivas | pares ≤.1/.25/.5/1 ATR |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 15m/EQH | 0.688 | 0 | 2 | 10 | 0.549 | 1755 | 2884 | 0/0/18/279 |
| 15m/EQL | 2.278 | 2 | 5 | 10 | 1.276 | 7918 | 9542 | 0/11/605/2397 |
| 1h/EQH | 0.898 | 0.000 | 3 | 7 | 0.714 | 2445 | 3709 | 0/0/40/337 |
| 1h/EQL | 1.558 | 1.000 | 4 | 10 | 1.027 | 5090 | 6433 | 0/1/393/1415 |
| 4h/EQH | 1.838 | 1 | 5 | 10 | 1.116 | 5816 | 7500 | 0/0/32/878 |
| 4h/EQL | 1.133 | 1 | 3 | 9 | 0.837 | 2583 | 4625 | 0/12/305/1087 |

| TF/lado | distância 0–.5/.5–1/1–2/2–4/4+ | idade 0–10/11–30/31–100/>100 |
| --- | --- | --- |
| 15m/EQH | 36/114/318/661/1755 | 376/653/975/880 |
| 15m/EQL | 34/116/419/1055/7918 | 375/858/1500/6809 |
| 1h/EQH | 16/119/362/767/2445 | 437/674/1046/1552 |
| 1h/EQL | 17/94/384/848/5090 | 347/695/1339/4052 |
| 4h/EQH | 44/157/476/1007/5816 | 500/881/1600/4519 |
| 4h/EQL | 53/232/632/1125/2583 | 501/892/1333/1899 |

| TF | vivos totais média/p50/p90/máx | bandas EQ atuais média/p50/p90/máx |
| --- | --- | --- |
| 15m | 2.966/3/6/12 | 3.775/4/5/7 |
| 1h | 2.456/2.000/5/11 | 3.071/3.000/5/6 |
| 4h | 2.971/3/6/13 | 3.371/3.000/5/7 |

Bandas atuais incluem memória selecionada de grabs e a última vela do payload original. Replay exclui essa vela e conta alvos atuais, não memória histórica renderizada. >4 ATR é proxy declarado; não mede viewport nem colisão de labels em pixels.

## Qualidade descritiva por TF/lado/fonte/horizonte

MFE/MAE partem do close do evento e usam somente candles seguintes; EQH: direção de reação para baixo; EQL: para cima. Excursões são não negativas. Razão indefinida com MAE=0 fica null, não infinito. Controle de candles: mesmo símbolo, TF, lado, quarto temporal, ±80 candles, distância temporal >40 e volatilidade relativa entre 0,8 e 1,25. Δ é diferença em P(MFE>MAE), não retorno nem prova de edge.

| TF/lado | fonte | h | n | casados | MFE p50 | MAE p50 | MFE/MAE p50 | P(MFE>MAE) | move médio | ΔP controle |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 15m/EQH | first_touch | 5 | 376 | 376 | 1.069 | 1.021 | 0.941 | 0.489 | -0.142 | 0.003 |
| 15m/EQH | first_touch | 10 | 376 | 376 | 1.384 | 1.599 | 0.852 | 0.487 | -0.342 | 0.002 |
| 15m/EQH | first_touch | 20 | 369 | 369 | 1.970 | 2.341 | 0.794 | 0.466 | -0.705 | -0.017 |
| 15m/EQH | first_touch | 40 | 365 | 365 | 2.490 | 3.450 | 0.644 | 0.430 | -1.229 | -0.051 |
| 15m/EQH | after_wick | 5 | 354 | 354 | 1.016 | 1.314 | 0.826 | 0.449 | -0.301 | -0.033 |
| 15m/EQH | after_wick | 10 | 353 | 353 | 1.401 | 1.751 | 0.845 | 0.450 | -0.342 | -0.039 |
| 15m/EQH | after_wick | 20 | 351 | 351 | 1.946 | 2.381 | 0.750 | 0.447 | -0.665 | -0.042 |
| 15m/EQH | after_wick | 40 | 347 | 347 | 2.627 | 3.332 | 0.799 | 0.447 | -1.248 | -0.025 |
| 15m/EQH | after_close | 5 | 374 | 374 | 0.955 | 1.276 | 0.826 | 0.444 | -0.340 | -0.035 |
| 15m/EQH | after_close | 10 | 374 | 374 | 1.401 | 1.686 | 0.880 | 0.473 | -0.272 | -0.011 |
| 15m/EQH | after_close | 20 | 373 | 373 | 2.036 | 2.282 | 0.794 | 0.458 | -0.516 | -0.024 |
| 15m/EQH | after_close | 40 | 371 | 371 | 2.685 | 3.048 | 0.857 | 0.466 | -1.134 | 0.009 |
| 15m/EQL | first_touch | 5 | 408 | 408 | 1.350 | 0.809 | 1.400 | 0.598 | 0.600 | 0.111 |
| 15m/EQL | first_touch | 10 | 408 | 408 | 1.747 | 1.159 | 1.321 | 0.569 | 0.432 | 0.095 |
| 15m/EQL | first_touch | 20 | 404 | 404 | 2.271 | 1.712 | 1.303 | 0.564 | 0.751 | 0.096 |
| 15m/EQL | first_touch | 40 | 372 | 372 | 3.085 | 2.396 | 1.260 | 0.562 | 1.040 | 0.054 |
| 15m/EQL | after_wick | 5 | 365 | 364 | 1.163 | 0.899 | 1.192 | 0.581 | 0.133 | 0.107 |
| 15m/EQL | after_wick | 10 | 365 | 364 | 1.497 | 1.229 | 1.222 | 0.553 | 0.026 | 0.100 |
| 15m/EQL | after_wick | 20 | 358 | 357 | 1.952 | 1.952 | 0.950 | 0.503 | 0.222 | 0.068 |
| 15m/EQL | after_wick | 40 | 321 | 320 | 2.717 | 2.579 | 0.985 | 0.505 | 0.238 | 0.058 |
| 15m/EQL | after_close | 5 | 368 | 367 | 1.046 | 0.922 | 1.105 | 0.549 | 0.086 | 0.079 |
| 15m/EQL | after_close | 10 | 365 | 364 | 1.348 | 1.226 | 1.068 | 0.529 | 0.075 | 0.075 |
| 15m/EQL | after_close | 20 | 351 | 350 | 1.700 | 1.961 | 0.895 | 0.479 | 0.199 | 0.041 |
| 15m/EQL | after_close | 40 | 314 | 313 | 2.694 | 2.651 | 0.911 | 0.481 | 0.164 | 0.039 |
| 1h/EQH | first_touch | 5 | 467 | 467 | 1.036 | 1.293 | 0.845 | 0.473 | -0.499 | 0.000 |
| 1h/EQH | first_touch | 10 | 467 | 467 | 1.433 | 1.796 | 0.759 | 0.450 | -0.683 | -0.024 |
| 1h/EQH | first_touch | 20 | 467 | 467 | 1.848 | 2.782 | 0.614 | 0.413 | -1.908 | -0.063 |
| 1h/EQH | first_touch | 40 | 455 | 455 | 2.501 | 3.861 | 0.612 | 0.413 | -3.992 | -0.050 |
| 1h/EQH | after_wick | 5 | 420 | 420 | 1.097 | 1.116 | 0.912 | 0.488 | -0.174 | 0.015 |
| 1h/EQH | after_wick | 10 | 419 | 419 | 1.397 | 1.615 | 0.894 | 0.465 | -0.451 | -0.007 |
| 1h/EQH | after_wick | 20 | 418 | 418 | 1.832 | 2.277 | 0.821 | 0.459 | -1.390 | -0.006 |
| 1h/EQH | after_wick | 40 | 409 | 409 | 2.334 | 3.500 | 0.755 | 0.445 | -3.298 | 0.004 |
| 1h/EQH | after_close | 5 | 412 | 412 | 1.113 | 1.070 | 0.987 | 0.502 | -0.049 | 0.035 |
| 1h/EQH | after_close | 10 | 411 | 411 | 1.497 | 1.488 | 0.914 | 0.484 | -0.327 | 0.021 |
| 1h/EQH | after_close | 20 | 410 | 410 | 1.847 | 2.225 | 0.850 | 0.473 | -1.005 | 0.027 |
| 1h/EQH | after_close | 40 | 402 | 402 | 2.363 | 3.377 | 0.797 | 0.455 | -2.842 | 0.042 |
| 1h/EQL | first_touch | 5 | 341 | 341 | 1.035 | 0.897 | 1.091 | 0.522 | 0.042 | 0.018 |
| 1h/EQL | first_touch | 10 | 334 | 334 | 1.411 | 1.222 | 1.142 | 0.542 | -0.152 | 0.034 |
| 1h/EQL | first_touch | 20 | 329 | 329 | 1.925 | 1.794 | 1.233 | 0.547 | -0.072 | 0.032 |
| 1h/EQL | first_touch | 40 | 304 | 304 | 2.727 | 2.592 | 1.000 | 0.503 | -0.053 | -0.037 |
| 1h/EQL | after_wick | 5 | 315 | 315 | 1.041 | 0.867 | 1.023 | 0.511 | -0.098 | -0.001 |
| 1h/EQL | after_wick | 10 | 306 | 306 | 1.370 | 1.387 | 1.054 | 0.510 | -0.112 | -0.011 |
| 1h/EQL | after_wick | 20 | 298 | 298 | 1.879 | 2.024 | 0.947 | 0.490 | -0.207 | -0.035 |
| 1h/EQL | after_wick | 40 | 282 | 282 | 2.511 | 2.871 | 0.962 | 0.496 | 0.381 | -0.024 |
| 1h/EQL | after_close | 5 | 323 | 323 | 1.005 | 0.967 | 1.000 | 0.502 | -0.119 | -0.013 |
| 1h/EQL | after_close | 10 | 317 | 317 | 1.439 | 1.382 | 1.183 | 0.536 | -0.070 | 0.007 |
| 1h/EQL | after_close | 20 | 309 | 309 | 2.031 | 1.842 | 1.165 | 0.528 | 0.064 | -0.007 |
| 1h/EQL | after_close | 40 | 300 | 300 | 2.841 | 2.612 | 1.022 | 0.510 | 0.866 | -0.024 |
| 4h/EQH | first_touch | 5 | 457 | 457 | 1.014 | 1.061 | 1.076 | 0.508 | -0.188 | -0.019 |
| 4h/EQH | first_touch | 10 | 456 | 456 | 1.408 | 1.485 | 0.965 | 0.482 | -0.759 | -0.061 |
| 4h/EQH | first_touch | 20 | 450 | 450 | 1.919 | 2.010 | 0.903 | 0.478 | -0.720 | -0.069 |
| 4h/EQH | first_touch | 40 | 437 | 437 | 2.554 | 2.471 | 1.055 | 0.515 | -0.630 | -0.011 |
| 4h/EQH | after_wick | 5 | 399 | 399 | 0.952 | 0.818 | 1.000 | 0.499 | -0.087 | -0.020 |
| 4h/EQH | after_wick | 10 | 395 | 395 | 1.392 | 1.254 | 1.319 | 0.539 | -0.297 | 0.010 |
| 4h/EQH | after_wick | 20 | 390 | 390 | 2.040 | 1.694 | 1.311 | 0.533 | -0.296 | 0.009 |
| 4h/EQH | after_wick | 40 | 378 | 378 | 2.781 | 2.158 | 1.334 | 0.558 | 0.070 | 0.090 |
| 4h/EQH | after_close | 5 | 409 | 409 | 0.972 | 0.796 | 1.103 | 0.513 | 0.018 | -0.000 |
| 4h/EQH | after_close | 10 | 403 | 403 | 1.411 | 1.241 | 1.341 | 0.536 | -0.107 | 0.015 |
| 4h/EQH | after_close | 20 | 399 | 399 | 1.968 | 1.687 | 1.327 | 0.536 | -0.108 | 0.025 |
| 4h/EQH | after_close | 40 | 389 | 389 | 2.766 | 2.154 | 1.319 | 0.550 | 0.277 | 0.101 |
| 4h/EQL | first_touch | 5 | 461 | 461 | 0.738 | 0.820 | 0.883 | 0.479 | -0.015 | -0.028 |
| 4h/EQL | first_touch | 10 | 457 | 457 | 1.082 | 1.249 | 0.805 | 0.466 | -0.113 | -0.057 |
| 4h/EQL | first_touch | 20 | 453 | 453 | 1.521 | 1.526 | 0.963 | 0.494 | -0.068 | -0.035 |
| 4h/EQL | first_touch | 40 | 451 | 451 | 2.198 | 2.305 | 0.918 | 0.461 | -0.183 | -0.063 |
| 4h/EQL | after_wick | 5 | 450 | 450 | 0.855 | 0.808 | 0.936 | 0.496 | 0.007 | -0.007 |
| 4h/EQL | after_wick | 10 | 449 | 449 | 1.108 | 1.174 | 0.907 | 0.470 | -0.073 | -0.036 |
| 4h/EQL | after_wick | 20 | 448 | 448 | 1.408 | 1.647 | 0.856 | 0.469 | -0.219 | -0.039 |
| 4h/EQL | after_wick | 40 | 447 | 447 | 2.066 | 2.474 | 0.724 | 0.432 | -0.416 | -0.056 |
| 4h/EQL | after_close | 5 | 457 | 457 | 0.834 | 0.789 | 1.000 | 0.505 | -0.020 | 0.013 |
| 4h/EQL | after_close | 10 | 456 | 456 | 1.115 | 1.163 | 0.978 | 0.489 | 0.013 | -0.005 |
| 4h/EQL | after_close | 20 | 455 | 455 | 1.429 | 1.697 | 0.835 | 0.462 | -0.183 | -0.030 |
| 4h/EQL | after_close | 40 | 454 | 454 | 2.092 | 2.670 | 0.686 | 0.407 | -0.380 | -0.058 |

## Sweep quality — episódios, sem controle de seleção equivalente

A = atravessa e fecha de volta; B/C = atravessa e fecha além. C identifica close anterior do lado interno; B já estava além. OHLC não permite afirmar qual foi o caminho intrabar. E = mais de um episódio, flag sobreposta a A/B/C. D aparece no primeiro contato sem atravessar, na tabela subsequente.

| TF/lado | classe | h | n | MFE p50 | MAE p50 | razão p50 | P(MFE>MAE) | move médio |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 15m/EQH | A | 5 | 1750 | 0.889 | 0.925 | 0.909 | 0.485 | -0.009 |
| 15m/EQH | A | 10 | 1743 | 1.344 | 1.352 | 1.000 | 0.507 | -0.076 |
| 15m/EQH | A | 20 | 1717 | 1.947 | 1.897 | 0.984 | 0.497 | -0.200 |
| 15m/EQH | A | 40 | 1648 | 2.627 | 2.729 | 0.913 | 0.478 | -0.377 |
| 15m/EQH | B | 5 | 116 | 0.868 | 1.361 | 0.663 | 0.379 | -0.154 |
| 15m/EQH | B | 10 | 116 | 1.429 | 1.688 | 0.979 | 0.500 | 0.010 |
| 15m/EQH | B | 20 | 115 | 2.163 | 2.331 | 0.979 | 0.496 | 0.038 |
| 15m/EQH | B | 40 | 115 | 3.047 | 3.242 | 0.857 | 0.487 | -0.685 |
| 15m/EQH | C | 5 | 1788 | 1.014 | 0.934 | 0.961 | 0.506 | 0.091 |
| 15m/EQH | C | 10 | 1780 | 1.474 | 1.380 | 1.000 | 0.514 | 0.012 |
| 15m/EQH | C | 20 | 1745 | 2.134 | 1.856 | 1.000 | 0.506 | -0.091 |
| 15m/EQH | C | 40 | 1719 | 2.798 | 2.827 | 0.943 | 0.490 | -0.134 |
| 15m/EQL | A | 5 | 1755 | 1.100 | 0.862 | 1.095 | 0.538 | 0.294 |
| 15m/EQL | A | 10 | 1739 | 1.517 | 1.195 | 1.100 | 0.539 | 0.121 |
| 15m/EQL | A | 20 | 1703 | 2.013 | 1.935 | 0.995 | 0.503 | 0.169 |
| 15m/EQL | A | 40 | 1584 | 2.683 | 2.594 | 1.000 | 0.506 | 0.229 |
| 15m/EQL | B | 5 | 76 | 1.427 | 0.627 | 1.500 | 0.697 | 0.712 |
| 15m/EQL | B | 10 | 76 | 1.681 | 0.916 | 1.469 | 0.684 | 0.294 |
| 15m/EQL | B | 20 | 70 | 2.301 | 1.549 | 1.548 | 0.614 | 0.011 |
| 15m/EQL | B | 40 | 69 | 2.794 | 2.267 | 1.500 | 0.551 | -0.056 |
| 15m/EQL | C | 5 | 1361 | 1.168 | 0.898 | 1.121 | 0.547 | 0.297 |
| 15m/EQL | C | 10 | 1353 | 1.610 | 1.300 | 1.140 | 0.540 | 0.252 |
| 15m/EQL | C | 20 | 1326 | 2.074 | 1.919 | 1.000 | 0.514 | 0.303 |
| 15m/EQL | C | 40 | 1234 | 2.861 | 2.590 | 1.006 | 0.512 | 0.239 |
| 1h/EQH | A | 5 | 1583 | 0.989 | 0.912 | 1.065 | 0.517 | -0.161 |
| 1h/EQH | A | 10 | 1576 | 1.369 | 1.417 | 0.951 | 0.495 | -0.466 |
| 1h/EQH | A | 20 | 1560 | 1.800 | 2.074 | 0.857 | 0.471 | -0.982 |
| 1h/EQH | A | 40 | 1528 | 2.257 | 3.216 | 0.708 | 0.431 | -2.435 |
| 1h/EQH | B | 5 | 152 | 1.191 | 1.523 | 0.573 | 0.401 | -0.856 |
| 1h/EQH | B | 10 | 152 | 1.866 | 2.302 | 1.035 | 0.513 | -0.071 |
| 1h/EQH | B | 20 | 149 | 2.792 | 2.871 | 0.995 | 0.503 | -0.995 |
| 1h/EQH | B | 40 | 148 | 3.761 | 3.769 | 0.892 | 0.466 | -1.921 |
| 1h/EQH | C | 5 | 1355 | 1.040 | 1.160 | 0.909 | 0.480 | -0.368 |
| 1h/EQH | C | 10 | 1352 | 1.367 | 1.705 | 0.790 | 0.462 | -0.691 |
| 1h/EQH | C | 20 | 1345 | 1.845 | 2.681 | 0.607 | 0.428 | -1.829 |
| 1h/EQH | C | 40 | 1323 | 2.369 | 3.882 | 0.596 | 0.410 | -3.955 |
| 1h/EQL | A | 5 | 1632 | 1.044 | 0.876 | 1.107 | 0.529 | 0.052 |
| 1h/EQL | A | 10 | 1608 | 1.444 | 1.251 | 1.136 | 0.535 | 0.099 |
| 1h/EQL | A | 20 | 1576 | 2.046 | 1.831 | 1.069 | 0.515 | 0.324 |
| 1h/EQL | A | 40 | 1482 | 3.058 | 2.339 | 1.250 | 0.545 | 1.671 |
| 1h/EQL | B | 5 | 88 | 1.037 | 0.565 | 1.609 | 0.591 | -0.427 |
| 1h/EQL | B | 10 | 88 | 1.342 | 1.161 | 1.553 | 0.545 | -0.672 |
| 1h/EQL | B | 20 | 85 | 2.239 | 1.740 | 1.343 | 0.576 | -0.344 |
| 1h/EQL | B | 40 | 85 | 3.275 | 2.300 | 1.643 | 0.600 | -0.610 |
| 1h/EQL | C | 5 | 1146 | 1.045 | 0.771 | 1.300 | 0.553 | 0.171 |
| 1h/EQL | C | 10 | 1129 | 1.402 | 1.110 | 1.304 | 0.555 | 0.210 |
| 1h/EQL | C | 20 | 1112 | 1.997 | 1.605 | 1.308 | 0.551 | 0.363 |
| 1h/EQL | C | 40 | 1046 | 2.880 | 2.142 | 1.419 | 0.565 | 1.050 |
| 4h/EQH | A | 5 | 1935 | 0.922 | 0.848 | 1.084 | 0.514 | -0.059 |
| 4h/EQH | A | 10 | 1917 | 1.323 | 1.211 | 1.079 | 0.520 | -0.308 |
| 4h/EQH | A | 20 | 1866 | 1.815 | 1.679 | 1.047 | 0.510 | -0.278 |
| 4h/EQH | A | 40 | 1798 | 2.413 | 2.274 | 1.052 | 0.518 | -0.228 |
| 4h/EQH | B | 5 | 83 | 1.291 | 1.143 | 0.954 | 0.494 | 0.039 |
| 4h/EQH | B | 10 | 83 | 1.775 | 1.571 | 1.033 | 0.518 | -0.049 |
| 4h/EQH | B | 20 | 81 | 2.332 | 1.772 | 1.197 | 0.556 | -0.346 |
| 4h/EQH | B | 40 | 79 | 2.579 | 2.583 | 1.161 | 0.532 | -0.009 |
| 4h/EQH | C | 5 | 1638 | 1.075 | 0.951 | 1.133 | 0.519 | -0.042 |
| 4h/EQH | C | 10 | 1626 | 1.435 | 1.316 | 1.085 | 0.520 | -0.221 |
| 4h/EQH | C | 20 | 1603 | 1.996 | 1.884 | 1.072 | 0.515 | -0.324 |
| 4h/EQH | C | 40 | 1530 | 2.665 | 2.513 | 1.049 | 0.509 | -0.197 |
| 4h/EQL | A | 5 | 2381 | 0.738 | 0.840 | 0.835 | 0.471 | -0.121 |
| 4h/EQL | A | 10 | 2342 | 1.087 | 1.174 | 0.880 | 0.472 | -0.117 |
| 4h/EQL | A | 20 | 2315 | 1.507 | 1.701 | 0.875 | 0.468 | -0.199 |
| 4h/EQL | A | 40 | 2257 | 2.004 | 2.419 | 0.788 | 0.453 | -0.153 |
| 4h/EQL | B | 5 | 105 | 1.005 | 0.624 | 1.511 | 0.610 | 0.224 |
| 4h/EQL | B | 10 | 105 | 1.185 | 1.085 | 1.150 | 0.533 | 0.342 |
| 4h/EQL | B | 20 | 105 | 1.799 | 1.485 | 1.150 | 0.514 | 0.972 |
| 4h/EQL | B | 40 | 105 | 2.367 | 1.892 | 1.150 | 0.524 | 1.556 |
| 4h/EQL | C | 5 | 1574 | 0.804 | 0.775 | 1.058 | 0.509 | 0.066 |
| 4h/EQL | C | 10 | 1557 | 1.181 | 1.092 | 1.046 | 0.511 | -0.006 |
| 4h/EQL | C | 20 | 1545 | 1.618 | 1.493 | 1.035 | 0.507 | 0.137 |
| 4h/EQL | C | 40 | 1522 | 2.251 | 2.168 | 0.965 | 0.489 | 0.227 |

| TF/lado | 1º contato | n h10 | MFE p50 | MAE p50 | P(MFE>MAE) | ΔP |
| --- | --- | --- | --- | --- | --- | --- |
| 15m/EQH | rejection_A | 100 | 1.298 | 1.994 | 0.480 | -0.009 |
| 15m/EQH | close_through_BC | 76 | 1.798 | 1.474 | 0.539 | 0.067 |
| 15m/EQH | touch_D | 200 | 1.267 | 1.405 | 0.470 | -0.017 |
| 15m/EQL | rejection_A | 161 | 2.021 | 0.858 | 0.634 | 0.179 |
| 15m/EQL | close_through_BC | 93 | 1.900 | 1.928 | 0.484 | 0.010 |
| 15m/EQL | touch_D | 154 | 1.523 | 1.126 | 0.552 | 0.058 |
| 1h/EQH | rejection_A | 133 | 1.433 | 1.978 | 0.429 | -0.050 |
| 1h/EQH | close_through_BC | 123 | 1.639 | 2.528 | 0.439 | -0.036 |
| 1h/EQH | touch_D | 211 | 1.348 | 1.637 | 0.469 | -0.001 |
| 1h/EQL | rejection_A | 124 | 1.493 | 1.194 | 0.548 | 0.033 |
| 1h/EQL | close_through_BC | 71 | 1.464 | 1.107 | 0.563 | 0.043 |
| 1h/EQL | touch_D | 139 | 1.311 | 1.306 | 0.525 | 0.031 |
| 4h/EQH | rejection_A | 128 | 1.421 | 1.511 | 0.461 | -0.054 |
| 4h/EQH | close_through_BC | 111 | 1.708 | 1.666 | 0.514 | -0.045 |
| 4h/EQH | touch_D | 217 | 1.241 | 1.302 | 0.479 | -0.073 |
| 4h/EQL | rejection_A | 123 | 0.985 | 1.316 | 0.407 | -0.132 |
| 4h/EQL | close_through_BC | 86 | 1.098 | 1.495 | 0.419 | -0.126 |
| 4h/EQL | touch_D | 248 | 1.079 | 1.132 | 0.512 | 0.005 |

## Controle de lifecycle casado por pools de pivô único

Confirmed single swing pools, first wick/close after confirmation; same symbol, TF, side, quarter, +/-80 bars, volatility ratio .8-1.25, age/distance bucket. Up to 3 nearest, with replacement. Overlapping outcome windows and shared market moves remain dependent. Not independent random pools.

Taxas de rejection/close-through abaixo usam todos os eventos elegíveis como denominador (inclusive os sem revisita). São incidências em até 40 candles, não probabilidade condicional à revisita.

| TF/lado/fonte | elegíveis | casados | revisit ≤40 | rejection ≤40 | close-through ≤40 | espera p50 | Δrevisit | Δrejection | Δclose |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 15m/EQH/after_wick | 378 | 145 | 0.788 | 0.294 | 0.272 | 6.000 | -0.045 | -0.067 | -0.133 |
| 15m/EQH/after_close | 401 | 164 | 0.791 | 0.349 | 0.322 | 6 | -0.024 | -0.009 | -0.099 |
| 15m/EQL/after_wick | 364 | 116 | 0.745 | 0.280 | 0.203 | 8 | -0.019 | -0.020 | -0.211 |
| 15m/EQL/after_close | 354 | 134 | 0.785 | 0.316 | 0.319 | 7.000 | 0.009 | -0.076 | -0.031 |
| 1h/EQH/after_wick | 470 | 202 | 0.751 | 0.211 | 0.330 | 7 | 0.004 | -0.058 | -0.134 |
| 1h/EQH/after_close | 489 | 241 | 0.706 | 0.225 | 0.356 | 6 | 0.052 | -0.017 | -0.032 |
| 1h/EQL/after_wick | 311 | 101 | 0.781 | 0.273 | 0.260 | 7 | 0.046 | -0.152 | -0.099 |
| 1h/EQL/after_close | 311 | 110 | 0.781 | 0.293 | 0.370 | 7 | 0.005 | -0.094 | -0.008 |
| 4h/EQH/after_wick | 451 | 172 | 0.718 | 0.266 | 0.293 | 6.000 | -0.019 | -0.047 | -0.089 |
| 4h/EQH/after_close | 465 | 223 | 0.733 | 0.256 | 0.361 | 6 | 0.000 | -0.019 | -0.055 |
| 4h/EQL/after_wick | 462 | 197 | 0.818 | 0.279 | 0.310 | 7.000 | 0.013 | -0.073 | -0.110 |
| 4h/EQL/after_close | 469 | 224 | 0.774 | 0.299 | 0.377 | 7 | -0.058 | -0.091 | -0.065 |

Reação em 5/10/20/40 e controles individuais estão em `followup_controls.rows`. Comparador de pivô único pode pertencer a outro EQ; não representa uma amostra independente de todos os clusters. Matching com reposição e poucas observações em alguns recortes impedem interpretar estes deltas como efeito causal de manter o nível vivo.

## Eixos individuais — primeiro contato, h10

As faixas são diagnósticas, sem seleção de threshold. Estrutura usa a função real `structureTrendByCandle` sobre eventos do snapshot final; sua disponibilidade histórica não foi certificada por replay estrutural nesta auditoria. Não se usa esse eixo para afirmar qualidade causal. Recência do último pivô coincide com idade desde formed_at.

| TF/lado | eixo | bucket | n | casados | P(MFE>MAE) | ΔP | símbolos | % símbolos Δ>0 | Δ mediana por símbolo |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 15m/EQH | touches | 3 | 345 | 345 | 0.478 | -0.007 | 68 | 0.485 | -0.000 |
| 15m/EQH | touches | 4 | 31 | 31 | 0.581 | 0.102 | 25 | 0.640 | 0.423 |
| 15m/EQH | age | 0-10 | 56 | 56 | 0.500 | 0.021 | 40 | 0.500 | 0.005 |
| 15m/EQH | age | 100+ | 58 | 58 | 0.397 | -0.086 | 40 | 0.425 | -0.136 |
| 15m/EQH | age | 11-30 | 141 | 141 | 0.525 | 0.040 | 59 | 0.576 | 0.075 |
| 15m/EQH | age | 31-100 | 121 | 121 | 0.479 | -0.009 | 62 | 0.468 | -0.025 |
| 15m/EQH | distance | 0-0.5 | 14 | 14 | 0.357 | -0.058 | 13 | 0.385 | -0.375 |
| 15m/EQH | distance | 0.5-1 | 48 | 48 | 0.438 | -0.022 | 34 | 0.441 | -0.124 |
| 15m/EQH | distance | 1-2 | 100 | 100 | 0.470 | -0.035 | 50 | 0.460 | -0.027 |
| 15m/EQH | distance | 2-4 | 148 | 148 | 0.514 | 0.025 | 61 | 0.475 | -0.038 |
| 15m/EQH | distance | 4+ | 66 | 66 | 0.515 | 0.036 | 43 | 0.535 | 0.037 |
| 15m/EQH | spread | .1-.25 | 70 | 70 | 0.329 | -0.167 | 40 | 0.375 | -0.339 |
| 15m/EQH | spread | .25-.5 | 282 | 282 | 0.525 | 0.040 | 68 | 0.544 | 0.032 |
| 15m/EQH | spread | .5+ | 13 | 13 | 0.462 | -0.004 | 12 | 0.500 | -0.091 |
| 15m/EQH | spread | 0-.1 | 11 | 11 | 0.545 | 0.111 | 11 | 0.545 | 0.350 |
| 15m/EQH | structure_snapshot | bearish | 119 | 119 | 0.580 | 0.071 | 53 | 0.642 | 0.197 |
| 15m/EQH | structure_snapshot | bullish | 254 | 254 | 0.437 | -0.035 | 67 | 0.388 | -0.102 |
| 15m/EQH | structure_snapshot | neutral | 3 | 3 | 1.000 | 0.401 | 3 | 1.000 | 0.350 |
| 15m/EQH | previous_sweep | False | 376 | 376 | 0.487 | 0.002 | 68 | 0.500 | 0.001 |
| 15m/EQH | strength | .25-.5 | 105 | 105 | 0.429 | -0.051 | 57 | 0.386 | -0.112 |
| 15m/EQH | strength | .5-.75 | 46 | 46 | 0.500 | -0.039 | 33 | 0.455 | -0.038 |
| 15m/EQH | strength | .75-1 | 16 | 16 | 0.500 | -0.007 | 13 | 0.538 | 0.375 |
| 15m/EQH | strength | 0-.25 | 209 | 209 | 0.512 | 0.038 | 68 | 0.544 | 0.046 |
| 15m/EQL | touches | 3 | 377 | 377 | 0.568 | 0.094 | 68 | 0.603 | 0.092 |
| 15m/EQL | touches | 4 | 30 | 30 | 0.600 | 0.122 | 23 | 0.652 | 0.405 |
| 15m/EQL | touches | 5+ | 1 | 1 | 0.000 | -0.500 | 1 | 0.000 | -0.500 |
| 15m/EQL | age | 0-10 | 38 | 38 | 0.500 | 0.061 | 29 | 0.517 | 0.025 |
| 15m/EQL | age | 100+ | 98 | 98 | 0.592 | 0.164 | 48 | 0.562 | 0.109 |
| 15m/EQL | age | 11-30 | 153 | 153 | 0.582 | 0.092 | 65 | 0.615 | 0.104 |
| 15m/EQL | age | 31-100 | 119 | 119 | 0.555 | 0.052 | 57 | 0.526 | 0.025 |
| 15m/EQL | distance | 0-0.5 | 16 | 16 | 0.500 | 0.029 | 16 | 0.500 | 0.013 |
| 15m/EQL | distance | 0.5-1 | 43 | 43 | 0.512 | 0.006 | 36 | 0.472 | -0.018 |
| 15m/EQL | distance | 1-2 | 122 | 122 | 0.574 | 0.102 | 60 | 0.617 | 0.077 |
| 15m/EQL | distance | 2-4 | 151 | 151 | 0.589 | 0.114 | 60 | 0.617 | 0.135 |
| 15m/EQL | distance | 4+ | 76 | 76 | 0.566 | 0.108 | 45 | 0.578 | 0.173 |
| 15m/EQL | spread | .1-.25 | 92 | 92 | 0.543 | 0.080 | 50 | 0.540 | 0.145 |
| 15m/EQL | spread | .25-.5 | 295 | 295 | 0.563 | 0.090 | 68 | 0.632 | 0.083 |
| 15m/EQL | spread | 0-.1 | 21 | 21 | 0.762 | 0.220 | 19 | 0.737 | 0.381 |
| 15m/EQL | structure_snapshot | bearish | 265 | 265 | 0.543 | 0.091 | 64 | 0.641 | 0.109 |
| 15m/EQL | structure_snapshot | bullish | 142 | 142 | 0.620 | 0.105 | 56 | 0.607 | 0.092 |
| 15m/EQL | structure_snapshot | neutral | 1 | 1 | 0.000 | -0.525 | 1 | 0.000 | -0.525 |
| 15m/EQL | previous_sweep | False | 408 | 408 | 0.569 | 0.095 | 68 | 0.603 | 0.077 |
| 15m/EQL | strength | .25-.5 | 107 | 107 | 0.682 | 0.214 | 51 | 0.706 | 0.275 |
| 15m/EQL | strength | .5-.75 | 48 | 48 | 0.479 | 0.029 | 30 | 0.467 | -0.009 |
| 15m/EQL | strength | .75-1 | 25 | 25 | 0.440 | -0.021 | 19 | 0.421 | -0.250 |
| 15m/EQL | strength | 0-.25 | 228 | 228 | 0.548 | 0.065 | 65 | 0.600 | 0.085 |
| 1h/EQH | touches | 3 | 408 | 408 | 0.446 | -0.032 | 68 | 0.456 | -0.041 |
| 1h/EQH | touches | 4 | 58 | 58 | 0.466 | 0.021 | 39 | 0.538 | 0.050 |
| 1h/EQH | touches | 5+ | 1 | 1 | 1.000 | 0.509 | 1 | 1.000 | 0.509 |
| 1h/EQH | age | 0-10 | 50 | 50 | 0.540 | 0.055 | 35 | 0.514 | 0.006 |
| 1h/EQH | age | 100+ | 122 | 122 | 0.311 | -0.156 | 55 | 0.236 | -0.200 |
| 1h/EQH | age | 11-30 | 165 | 165 | 0.503 | 0.032 | 62 | 0.548 | 0.046 |
| 1h/EQH | age | 31-100 | 130 | 130 | 0.477 | -0.001 | 59 | 0.492 | -0.012 |
| 1h/EQH | distance | 0-0.5 | 8 | 8 | 0.625 | 0.139 | 7 | 0.571 | 0.400 |
| 1h/EQH | distance | 0.5-1 | 39 | 39 | 0.436 | -0.058 | 25 | 0.360 | -0.108 |
| 1h/EQH | distance | 1-2 | 150 | 150 | 0.487 | 0.012 | 62 | 0.419 | -0.057 |
| 1h/EQH | distance | 2-4 | 189 | 189 | 0.444 | -0.020 | 65 | 0.492 | -0.050 |
| 1h/EQH | distance | 4+ | 81 | 81 | 0.383 | -0.100 | 51 | 0.451 | -0.154 |
| 1h/EQH | spread | .1-.25 | 102 | 102 | 0.392 | -0.081 | 54 | 0.407 | -0.086 |
| 1h/EQH | spread | .25-.5 | 327 | 327 | 0.465 | -0.008 | 68 | 0.529 | 0.008 |
| 1h/EQH | spread | .5+ | 25 | 25 | 0.480 | 0.011 | 20 | 0.500 | 0.072 |
| 1h/EQH | spread | 0-.1 | 13 | 13 | 0.462 | -0.059 | 11 | 0.455 | -0.215 |
| 1h/EQH | structure_snapshot | bearish | 159 | 159 | 0.503 | 0.027 | 65 | 0.523 | 0.042 |
| 1h/EQH | structure_snapshot | bullish | 307 | 307 | 0.423 | -0.049 | 68 | 0.397 | -0.062 |
| 1h/EQH | structure_snapshot | neutral | 1 | 1 | 0.000 | -0.609 | 1 | 0.000 | -0.609 |
| 1h/EQH | previous_sweep | False | 467 | 467 | 0.450 | -0.024 | 68 | 0.515 | 0.002 |
| 1h/EQH | strength | .25-.5 | 126 | 126 | 0.421 | -0.034 | 60 | 0.467 | -0.015 |
| 1h/EQH | strength | .5-.75 | 52 | 52 | 0.423 | -0.066 | 36 | 0.444 | -0.161 |
| 1h/EQH | strength | .75-1 | 25 | 25 | 0.560 | 0.113 | 22 | 0.500 | 0.023 |
| 1h/EQH | strength | 0-.25 | 264 | 264 | 0.458 | -0.024 | 67 | 0.448 | -0.038 |
| 1h/EQL | touches | 3 | 303 | 303 | 0.538 | 0.034 | 67 | 0.612 | 0.037 |
| 1h/EQL | touches | 4 | 30 | 30 | 0.600 | 0.056 | 23 | 0.522 | 0.200 |
| 1h/EQL | touches | 5+ | 1 | 1 | 0.000 | -0.525 | 1 | 0.000 | -0.525 |
| 1h/EQL | age | 0-10 | 32 | 32 | 0.500 | -0.021 | 28 | 0.464 | -0.169 |
| 1h/EQL | age | 100+ | 58 | 58 | 0.586 | 0.088 | 36 | 0.583 | 0.356 |
| 1h/EQL | age | 11-30 | 130 | 130 | 0.477 | -0.022 | 60 | 0.450 | -0.079 |
| 1h/EQL | age | 31-100 | 114 | 114 | 0.605 | 0.087 | 54 | 0.704 | 0.150 |
| 1h/EQL | distance | 0-0.5 | 6 | 6 | 0.333 | -0.106 | 6 | 0.333 | -0.308 |
| 1h/EQL | distance | 0.5-1 | 29 | 29 | 0.621 | 0.063 | 25 | 0.640 | 0.325 |
| 1h/EQL | distance | 1-2 | 98 | 98 | 0.520 | 0.012 | 56 | 0.589 | 0.037 |
| 1h/EQL | distance | 2-4 | 147 | 147 | 0.551 | 0.055 | 60 | 0.617 | 0.070 |
| 1h/EQL | distance | 4+ | 54 | 54 | 0.537 | 0.019 | 35 | 0.514 | 0.043 |
| 1h/EQL | spread | .1-.25 | 73 | 73 | 0.507 | -0.013 | 42 | 0.524 | 0.054 |
| 1h/EQL | spread | .25-.5 | 251 | 251 | 0.562 | 0.059 | 66 | 0.576 | 0.077 |
| 1h/EQL | spread | 0-.1 | 10 | 10 | 0.300 | -0.240 | 8 | 0.375 | -0.512 |
| 1h/EQL | structure_snapshot | bearish | 215 | 215 | 0.540 | 0.041 | 64 | 0.547 | 0.061 |
| 1h/EQL | structure_snapshot | bullish | 118 | 118 | 0.551 | 0.027 | 51 | 0.667 | 0.075 |
| 1h/EQL | structure_snapshot | neutral | 1 | 1 | 0.000 | -0.475 | 1 | 0.000 | -0.475 |
| 1h/EQL | previous_sweep | False | 334 | 334 | 0.542 | 0.034 | 67 | 0.612 | 0.057 |
| 1h/EQL | strength | .25-.5 | 64 | 64 | 0.641 | 0.153 | 40 | 0.625 | 0.250 |
| 1h/EQL | strength | .5-.75 | 46 | 46 | 0.522 | 0.005 | 30 | 0.533 | 0.067 |
| 1h/EQL | strength | .75-1 | 30 | 30 | 0.600 | 0.066 | 21 | 0.619 | 0.325 |
| 1h/EQL | strength | 0-.25 | 194 | 194 | 0.505 | -0.003 | 64 | 0.516 | 0.007 |
| 4h/EQH | touches | 3 | 415 | 415 | 0.475 | -0.069 | 67 | 0.388 | -0.040 |
| 4h/EQH | touches | 4 | 38 | 38 | 0.579 | 0.045 | 28 | 0.571 | 0.229 |
| 4h/EQH | touches | 5+ | 3 | 3 | 0.333 | -0.275 | 3 | 0.333 | -0.525 |
| 4h/EQH | age | 0-10 | 49 | 49 | 0.592 | 0.021 | 36 | 0.528 | 0.231 |
| 4h/EQH | age | 100+ | 111 | 111 | 0.441 | -0.082 | 56 | 0.464 | -0.058 |
| 4h/EQH | age | 11-30 | 144 | 144 | 0.500 | -0.019 | 61 | 0.508 | 0.034 |
| 4h/EQH | age | 31-100 | 152 | 152 | 0.461 | -0.112 | 63 | 0.381 | -0.133 |
| 4h/EQH | distance | 0-0.5 | 18 | 18 | 0.278 | -0.219 | 15 | 0.267 | -0.500 |
| 4h/EQH | distance | 0.5-1 | 52 | 52 | 0.481 | -0.030 | 35 | 0.486 | -0.108 |
| 4h/EQH | distance | 1-2 | 132 | 132 | 0.485 | -0.084 | 61 | 0.410 | -0.094 |
| 4h/EQH | distance | 2-4 | 175 | 175 | 0.463 | -0.072 | 58 | 0.431 | -0.085 |
| 4h/EQH | distance | 4+ | 79 | 79 | 0.570 | 0.016 | 50 | 0.560 | 0.044 |
| 4h/EQH | spread | .1-.25 | 92 | 92 | 0.413 | -0.143 | 53 | 0.415 | -0.138 |
| 4h/EQH | spread | .25-.5 | 310 | 310 | 0.519 | -0.018 | 65 | 0.462 | -0.014 |
| 4h/EQH | spread | .5+ | 36 | 36 | 0.444 | -0.127 | 28 | 0.393 | -0.250 |
| 4h/EQH | spread | 0-.1 | 18 | 18 | 0.278 | -0.256 | 17 | 0.294 | -0.475 |
| 4h/EQH | structure_snapshot | bearish | 143 | 143 | 0.538 | -0.015 | 62 | 0.500 | -0.003 |
| 4h/EQH | structure_snapshot | bullish | 310 | 310 | 0.458 | -0.081 | 65 | 0.323 | -0.098 |
| 4h/EQH | structure_snapshot | neutral | 3 | 3 | 0.333 | -0.196 | 2 | 0.000 | -0.287 |
| 4h/EQH | previous_sweep | False | 456 | 456 | 0.482 | -0.061 | 67 | 0.358 | -0.086 |
| 4h/EQH | strength | .25-.5 | 103 | 103 | 0.534 | -0.005 | 54 | 0.463 | -0.126 |
| 4h/EQH | strength | .5-.75 | 48 | 48 | 0.479 | -0.114 | 34 | 0.382 | -0.257 |
| 4h/EQH | strength | .75-1 | 26 | 26 | 0.192 | -0.351 | 20 | 0.100 | -0.509 |
| 4h/EQH | strength | 0-.25 | 279 | 279 | 0.491 | -0.045 | 66 | 0.394 | -0.052 |
| 4h/EQL | touches | 3 | 410 | 410 | 0.468 | -0.052 | 67 | 0.358 | -0.051 |
| 4h/EQL | touches | 4 | 43 | 43 | 0.465 | -0.072 | 35 | 0.429 | -0.175 |
| 4h/EQL | touches | 5+ | 4 | 4 | 0.250 | -0.328 | 3 | 0.333 | -0.588 |
| 4h/EQL | age | 0-10 | 58 | 58 | 0.483 | -0.079 | 45 | 0.489 | -0.037 |
| 4h/EQL | age | 100+ | 85 | 85 | 0.376 | -0.105 | 52 | 0.404 | -0.227 |
| 4h/EQL | age | 11-30 | 196 | 196 | 0.464 | -0.040 | 66 | 0.455 | -0.074 |
| 4h/EQL | age | 31-100 | 118 | 118 | 0.525 | -0.039 | 59 | 0.458 | -0.069 |
| 4h/EQL | distance | 0-0.5 | 18 | 18 | 0.389 | -0.141 | 15 | 0.333 | -0.350 |
| 4h/EQL | distance | 0.5-1 | 78 | 78 | 0.462 | -0.090 | 48 | 0.438 | -0.069 |
| 4h/EQL | distance | 1-2 | 171 | 171 | 0.485 | -0.038 | 64 | 0.469 | -0.015 |
| 4h/EQL | distance | 2-4 | 142 | 142 | 0.493 | -0.012 | 61 | 0.492 | -0.034 |
| 4h/EQL | distance | 4+ | 48 | 48 | 0.354 | -0.171 | 33 | 0.394 | -0.350 |
| 4h/EQL | spread | .1-.25 | 108 | 108 | 0.361 | -0.182 | 53 | 0.283 | -0.272 |
| 4h/EQL | spread | .25-.5 | 328 | 328 | 0.512 | -0.002 | 67 | 0.567 | 0.029 |
| 4h/EQL | spread | 0-.1 | 21 | 21 | 0.286 | -0.264 | 18 | 0.333 | -0.494 |
| 4h/EQL | structure_snapshot | bearish | 333 | 333 | 0.417 | -0.098 | 67 | 0.313 | -0.125 |
| 4h/EQL | structure_snapshot | bullish | 122 | 122 | 0.598 | 0.055 | 59 | 0.627 | 0.176 |
| 4h/EQL | structure_snapshot | neutral | 2 | 2 | 0.500 | -0.011 | 2 | 0.500 | -0.011 |
| 4h/EQL | previous_sweep | False | 457 | 457 | 0.466 | -0.057 | 67 | 0.373 | -0.075 |
| 4h/EQL | strength | .25-.5 | 119 | 119 | 0.437 | -0.098 | 51 | 0.392 | -0.097 |
| 4h/EQL | strength | .5-.75 | 39 | 39 | 0.513 | -0.019 | 30 | 0.567 | 0.167 |
| 4h/EQL | strength | .75-1 | 8 | 8 | 0.500 | 0.020 | 5 | 0.600 | 0.169 |
| 4h/EQL | strength | 0-.25 | 291 | 291 | 0.471 | -0.047 | 66 | 0.394 | -0.065 |

Não há comparação casada de idade/distância entre **todos** os buckets de strength. O controle temporal/volatilidade ajuda, mas não remove correlações entre os eixos. Portanto as curvas acima não autorizam score, gate, expiry ou quality tier.

## Estabilidade temporal e robustez por símbolo — primeiro contato, h10

| TF/lado | Δ bloco 1 | Δ bloco 2 | Δ bloco 3 | Δ bloco 4 | símbolos | % Δ>0 | Δ mediana | concentração top3 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 15m/EQH | -0.024 | -0.040 | -0.038 | 0.131 | 68 | 0.500 | 0.001 | 0.072 |
| 15m/EQL | -0.105 | -0.002 | 0.127 | 0.234 | 68 | 0.603 | 0.077 | 0.078 |
| 1h/EQH | -0.032 | 0.069 | -0.144 | 0.020 | 68 | 0.515 | 0.002 | 0.073 |
| 1h/EQL | -0.020 | 0.091 | -0.150 | 0.081 | 67 | 0.612 | 0.057 | 0.078 |
| 4h/EQH | 0.102 | -0.101 | -0.041 | -0.108 | 67 | 0.358 | -0.086 | 0.070 |
| 4h/EQL | -0.012 | -0.011 | -0.125 | -0.084 | 67 | 0.373 | -0.075 | 0.072 |

| TF/lado | bottom 3 (símbolo: delta, n) | top 3 (símbolo: delta, n) |
| --- | --- | --- |
| 15m/EQH | ZECUSDT: -0.434, 5, LINKUSDT: -0.408, 6, GRTUSDT: -0.396, 3 | XTZUSDT: 0.448, 7, BANDUSDT: 0.483, 4, IOTAUSDT: 0.485, 6 |
| 15m/EQL | CELRUSDT: -0.750, 1, DOTUSDT: -0.645, 3, ICPUSDT: -0.540, 5 | COMPUSDT: 0.644, 2, TRXUSDT: 0.700, 2, LTCUSDT: 0.787, 1 |
| 1h/EQH | YFIUSDT: -0.466, 5, GRTUSDT: -0.457, 6, ICPUSDT: -0.403, 7 | ZECUSDT: 0.310, 7, IMXUSDT: 0.349, 5, SANDUSDT: 0.367, 6 |
| 1h/EQL | ZRXUSDT: -0.525, 1, HBARUSDT: -0.513, 4, LDOUSDT: -0.495, 5 | LINKUSDT: 0.450, 1, ZILUSDT: 0.568, 3, ETCUSDT: 0.625, 3 |
| 4h/EQH | ZECUSDT: -0.447, 7, INJUSDT: -0.419, 6, ARBUSDT: -0.401, 9 | BANDUSDT: 0.331, 11, DOGEUSDT: 0.346, 5, GALAUSDT: 0.362, 2 |
| 4h/EQL | TRXUSDT: -0.441, 6, BATUSDT: -0.440, 5, RUNEUSDT: -0.436, 7 | QTUMUSDT: 0.398, 6, ADAUSDT: 0.469, 4, EGLDUSDT: 0.562, 4 |

Top/bottom são descrições pós-hoc; nenhum ativo é escolhido para resgatar resultado. O JSON registra quatro blocos e símbolos por horizonte/bucket. Blocos dentro de uma mesma janela não equivalem a quatro regimes independentes; os TFs têm durações diferentes.

## Overlap de geometria do snapshot final

Interseção exata com banda EQ; BOS/CHoCH usam referência pontual; POI/bands usam range; VWAP usa ±1σ final; profile usa POC/VAH/VAL. Fontes históricas podem já estar mortas. Essas contagens não medem confluência causal nem informação incremental.

| TF/lado | n EQ | BOS | CHoCH | POI | liquidation | VWAP ±1σ final | profile final |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 15m/EQH | 739 | 345 | 145 | 645 | 603 | 14 | 20 |
| 15m/EQL | 757 | 291 | 159 | 676 | 619 | 13 | 21 |
| 1h/EQH | 743 | 333 | 162 | 647 | 579 | 16 | 20 |
| 1h/EQL | 775 | 368 | 146 | 693 | 574 | 13 | 19 |
| 4h/EQH | 774 | 303 | 196 | 688 | 603 | 19 | 31 |
| 4h/EQL | 825 | 430 | 182 | 733 | 643 | 20 | 41 |

## Casos BTC / ETH / SOL

Seleção pós-hoc para ilustrar mecânica, não amostra de validação. Datas UTC; faixa é [low, high]. A confirmação mínima é formed_at +5 candles; ela não garante que a tolerância daquela data já permitia o cluster final.

### BTCUSDT

| caso | chart | faixa/preço | diagnóstico | timeline |
| --- | --- | --- | --- | --- |
| A_clean_rejection | BTCUSDT_15m.json | EQH [78289.2, 78314.9] | 4 toques; spread=0.138 ATR; idade=95; MFE/MAE h10=1.73/1.32 | formou 2026-09-09T21:00:00+00:00 → wick 2026-09-10T00:00:00+00:00 → close 2026-09-10T02:45:00+00:00 |
| C_old | BTCUSDT_1h.json | EQH [65689.6, 65780] | 3 toques; spread=0.398 ATR; idade=1087; MFE/MAE h10=0.13/18.84 | formou 2026-07-27T13:00:00+00:00 → wick 2026-08-19T14:00:00+00:00 → close 2026-08-19T14:00:00+00:00 |
| D_sweep_rejection | BTCUSDT_15m.json | EQH [77393.9, 77478.8] | 3 toques; spread=0.458 ATR; idade=24; MFE/MAE h10=2.11/0.39 | formou 2026-09-10T14:45:00+00:00 → wick 2026-09-10T17:30:00+00:00 → close None |
| E_close_through | BTCUSDT_15m.json | EQH [77700, 77767.8] | 4 toques; spread=0.353 ATR; idade=823; MFE/MAE h10=1.17/0.53 | formou 2026-09-02T07:00:00+00:00 → wick 2026-09-03T02:30:00+00:00 → close 2026-09-03T02:45:00+00:00 |
| F_3plus | BTCUSDT_15m.json | EQH [77393.9, 77478.8] | 3 toques; spread=0.458 ATR; idade=24; MFE/MAE h10=2.11/0.39 | formou 2026-09-10T14:45:00+00:00 → wick 2026-09-10T17:30:00+00:00 → close None |
| G_widest_equal_candidate | BTCUSDT_1h.json | EQL [63847, 64000] | 5 toques; spread=0.650 ATR; idade=559 | formou 2026-08-18T13:00:00+00:00 → wick None → close None |
| B_close_levels | BTCUSDT_4h.json | 75822 / 76305.9 | gap=0.493 ATR; coexistiram=True | 2026-08-23T04:00:00+00:00 / 2026-09-02T08:00:00+00:00 |

A classe A ilustra geometria limpa/rejection; a reação h10 acima determina se o exemplo de fato reagiu melhor que sua excursão adversa. G é candidato por maior spread, não um “falso equal” comprovado. O JSON contém cada preço/toque.

### ETHUSDT

| caso | chart | faixa/preço | diagnóstico | timeline |
| --- | --- | --- | --- | --- |
| A_clean_rejection | ETHUSDT_15m.json | EQH [2502.86, 2504.71] | 3 toques; spread=0.219 ATR; idade=209; MFE/MAE h10=3.12/0.31 | formou 2026-09-08T16:30:00+00:00 → wick 2026-09-08T18:15:00+00:00 → close 2026-09-09T04:30:00+00:00 |
| C_old | ETHUSDT_4h.json | EQH [2167.2, 2175.64] | 4 toques; spread=0.180 ATR; idade=944; MFE/MAE h10=1.69/0.62 | formou 2026-04-06T08:00:00+00:00 → wick 2026-04-07T20:00:00+00:00 → close 2026-04-07T20:00:00+00:00 |
| D_sweep_rejection | ETHUSDT_15m.json | EQH [2418.82, 2422.46] | 3 toques; spread=0.444 ATR; idade=634; MFE/MAE h10=0.05/11.26 | formou 2026-09-03T07:00:00+00:00 → wick 2026-09-03T12:30:00+00:00 → close 2026-09-03T13:30:00+00:00 |
| E_close_through | ETHUSDT_15m.json | EQH [2394.98, 2399] | 3 toques; spread=0.497 ATR; idade=662; MFE/MAE h10=1.08/0.73 | formou 2026-09-03T00:00:00+00:00 → wick 2026-09-03T02:30:00+00:00 → close 2026-09-03T02:30:00+00:00 |
| F_3plus | ETHUSDT_15m.json | EQH [2394.98, 2399] | 3 toques; spread=0.497 ATR; idade=662; MFE/MAE h10=1.08/0.73 | formou 2026-09-03T00:00:00+00:00 → wick 2026-09-03T02:30:00+00:00 → close 2026-09-03T02:30:00+00:00 |
| G_widest_equal_candidate | ETHUSDT_1h.json | EQL [1846.17, 1852.22] | 6 toques; spread=0.576 ATR; idade=725 | formou 2026-08-11T15:00:00+00:00 → wick None → close None |
| B_close_levels | ETHUSDT_15m.json | 2486.88 / 2490.3 | gap=0.411 ATR; coexistiram=True | 2026-09-09T03:45:00+00:00 / 2026-09-09T05:45:00+00:00 |

A classe A ilustra geometria limpa/rejection; a reação h10 acima determina se o exemplo de fato reagiu melhor que sua excursão adversa. G é candidato por maior spread, não um “falso equal” comprovado. O JSON contém cada preço/toque.

### SOLUSDT

| caso | chart | faixa/preço | diagnóstico | timeline |
| --- | --- | --- | --- | --- |
| A_clean_rejection | SOLUSDT_15m.json | EQL [104.91, 105.03] | 3 toques; spread=0.243 ATR; idade=378; MFE/MAE h10=3.84/1.01 | formou 2026-09-06T22:15:00+00:00 → wick 2026-09-07T02:00:00+00:00 → close 2026-09-07T03:45:00+00:00 |
| C_old | SOLUSDT_1h.json | EQL [73.36, 73.55] | 3 toques; spread=0.453 ATR; idade=1140; MFE/MAE h10=0.85/0.76 | formou 2026-07-25T08:00:00+00:00 → wick 2026-07-28T01:00:00+00:00 → close 2026-07-28T01:00:00+00:00 |
| D_sweep_rejection | SOLUSDT_15m.json | EQH [99.65, 99.77] | 3 toques; spread=0.259 ATR; idade=685; MFE/MAE h10=1.01/0.78 | formou 2026-09-02T18:15:00+00:00 → wick 2026-09-02T20:00:00+00:00 → close 2026-09-02T23:00:00+00:00 |
| E_close_through | SOLUSDT_15m.json | EQH [99.65, 99.77] | 3 toques; spread=0.259 ATR; idade=685; MFE/MAE h10=1.01/0.78 | formou 2026-09-02T18:15:00+00:00 → wick 2026-09-02T20:00:00+00:00 → close 2026-09-02T23:00:00+00:00 |
| F_3plus | SOLUSDT_15m.json | EQH [99.65, 99.77] | 3 toques; spread=0.259 ATR; idade=685; MFE/MAE h10=1.01/0.78 | formou 2026-09-02T18:15:00+00:00 → wick 2026-09-02T20:00:00+00:00 → close 2026-09-02T23:00:00+00:00 |
| G_widest_equal_candidate | SOLUSDT_1h.json | EQL [74.97, 75.27] | 8 toques; spread=0.731 ATR; idade=570 | formou 2026-08-18T02:00:00+00:00 → wick None → close None |
| B_close_levels | SOLUSDT_15m.json | 104.885 / 105.12 | gap=0.499 ATR; coexistiram=True | 2026-09-09T12:45:00+00:00 / 2026-09-09T08:30:00+00:00 |

A classe A ilustra geometria limpa/rejection; a reação h10 acima determina se o exemplo de fato reagiu melhor que sua excursão adversa. G é candidato por maior spread, não um “falso equal” comprovado. O JSON contém cada preço/toque.

## Replay candle a candle e M5 diagnóstico

`eq_levels_replay_baseline.json`: 9 charts; passo 1; 243 EQs; 225 coortes.

| TF/lado | pivôs | formados | vivos | n 1º contato h10 | ΔP h10 |
| --- | --- | --- | --- | --- | --- |
| 15m/EQH | 233 | 43 | 19 | 28 | 0.067 |
| 15m/EQL | 246 | 40 | 7 | 34 | 0.041 |
| 1h/EQH | 239 | 40 | 5 | 36 | 0.006 |
| 1h/EQL | 224 | 39 | 14 | 29 | 0.118 |
| 4h/EQH | 230 | 41 | 3 | 33 | -0.049 |
| 4h/EQL | 222 | 40 | 12 | 29 | -0.008 |

`eq_levels_m5_baseline.json`: 3 charts; passo 20; 69 EQs; 74 coortes.

| TF/lado | pivôs | formados | vivos | n 1º contato h10 | ΔP h10 |
| --- | --- | --- | --- | --- | --- |
| 5m/EQH | 214 | 36 | 7 | 19 | 0.091 |
| 5m/EQL | 218 | 33 | 15 | 13 | -0.128 |

M5 vem do cache local de BTC/ETH/SOL até 05/09/2026 e usa outro período. É diagnóstico separado; não valida o painel principal até 10/09. Não há contexto estrutural no cache M5. A última barra também foi excluída.

## Proveniência e cobertura

| chart | candles fechados | início UTC | fim UTC | SHA256 do snapshot |
| --- | --- | --- | --- | --- |
| AAVEUSDT_15m.json | 1199 | 2026-08-28T10:15:00+00:00 | 2026-09-10T21:00:00+00:00 | 18e5a7b90710b3e45a79ee3af07ab5cee314e20f8ba7d58f0135b433fba71d9d |
| AAVEUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | a26a02a53e60b3d5fcd502a7d34bdbd4f0c7a3f295faeb3c6b2943588f782d45 |
| AAVEUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | e09b4d43f70604baa002f785b5324ab8f2f23c47f6dbd06ddcc3359fd66de2c9 |
| ADAUSDT_15m.json | 1199 | 2026-08-28T10:15:00+00:00 | 2026-09-10T21:00:00+00:00 | 0fcb00fea82525abb7290da696d834b9b2ae5ee8f77a3faeeaac673ec0abe6e7 |
| ADAUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 4323022675f1f889ec13923c6734806b447d46fb8f9810b6d22b0b8fdfc72399 |
| ADAUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 93688cddee19d2acf1aa993617ed92d54adf8f8c41f5426d5228eda467c59ee6 |
| ALGOUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | ccc059e6be7c0a39f3d6e179c9e935e5b10999e0ee7f7ab1fe28962079912b52 |
| ALGOUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | c403245f95a7dfb38726e4d9e5dde57a9d2d0f1335ef6d615772b06f57a50ede |
| ALGOUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 9219eca8daad023b47715d5010e38845ddd80b24959ce89b5dc7107c46437b86 |
| ANKRUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | a6d05c37f246436695eff907d2bd8b9da9c64586d3ed64ad190ac85b0640b124 |
| ANKRUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 2762a7cfb2071dc2191f4e760e505be7da2ef0d7b6b924c0e27f3ce31c26be12 |
| ANKRUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 1a44ec37442c74ffd340e9c0d16b2664ae4b7ce612932441c1973ab0c8d65fef |
| APTUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 9a07dc6e3ade61fb7bfe38e5971b84b78791f8e9fff4d70c06da7d0d7f833d41 |
| APTUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 2359452d08643014ce7f2b16555a6660258a45107ef349d44dc987de3a9ae594 |
| APTUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | b06d0f863285b55926f7449702e55e44cfbb56b63dbc8fdbd21e70f993a3b37c |
| ARBUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 07e4e131c5f2572added13a24fdf631c39a9b300df532070e6ee62fbeccd185b |
| ARBUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 578b3e45e6359f9da6c4dbdf19c6085ebe18ddf6daa5dd3e008daed2e0675ac5 |
| ARBUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 6c51525d49a989a200f7f095a7455c198e2ebe0d5eef675470b65a3164c351a1 |
| ATOMUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 58f517a4a9d61303f1fccb0d85ec7ccddeb00dad9343ca0eef17b3927a6106b1 |
| ATOMUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | ea856b80be28ba16b90fe3dce9ff8813df7b3f5a9a5b7e0b0f24d427d87a0afc |
| ATOMUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 0385a3ea47fbb899a0110bf2599896db4ec3b120f35885424de40820f2d497db |
| AVAXUSDT_15m.json | 1199 | 2026-08-28T10:15:00+00:00 | 2026-09-10T21:00:00+00:00 | 71641ce38d94c9e3d4b1b40366f0b76e6df93e8e08a46dbc4da42273e187c172 |
| AVAXUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 89bb040fe3680aac8e93e61ff61d2eb7a4a84bd28f761686b4199027910fb647 |
| AVAXUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | f3cd71d19ec72bf810eba6ecab3f9a99a1dd484eddc94ca0295d5c54b5884735 |
| AXSUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 7078c3ae46b870e83c67f2121e1c41555b41cbb1ea254a14478a3a3de531870d |
| AXSUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 5733cc49098c1864e90b13417391a91c757d9ee6c9080afc6a1a9d27971c6a14 |
| AXSUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 71a5fa87dfe52e3c64f1f03b1c25023bca9a63f8df4becc3d9551998fc3c5d7e |
| BANDUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | ee10d0327f1e3389eac1e9e28711321b445e410439f7abd6fac665565bb3c677 |
| BANDUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | f327b3952f3c8a422cb4aeba95e2680bef9b8acc70e25a75116421c90d25264e |
| BANDUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | b70d7d8dcbc02bc3f9a20b29ffe683be6af25bc3c50f795c9243192ac653221c |
| BATUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 57662ebce9d3d55ec993fb311418f1af89b2552f9555bbcc65951716804537d7 |
| BATUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 3728542639fc5645e27f6f7f763cb7603693ded7a220419de942df9a8324f258 |
| BATUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 6fb9075f83847bad5fc608a302d2ce1293982826365ddf2f6abc38f27a92112e |
| BNBUSDT_15m.json | 1199 | 2026-08-28T10:15:00+00:00 | 2026-09-10T21:00:00+00:00 | 56ab4318cd1d9a7ffeb4b0a438309cce18f8fb9864e9b296d362a0dc83db00c1 |
| BNBUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 4b708a741eaa17fd42681d674c3a1a016b007ccf21a23b5456db388e1e7d5944 |
| BNBUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 5580f4c63ba589bcc379b616854f052f9fc2527f38a6f4fa8a589f6389e1a154 |
| BTCUSDT_15m.json | 1199 | 2026-08-29T09:15:00+00:00 | 2026-09-10T20:45:00+00:00 | 4270859ef7d127a406e0c347113fe3e4d981e84c44313fa867c53c16865f5f74 |
| BTCUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | dd0c17d2e850c0f068d50edd58726aa4327d6b7a44208f91be7e9c02556318ed |
| BTCUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | c1edd2846d56f04bba3d63ac6df3afcbef77ec6486dee02b812fab4a22027857 |
| CELRUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | f8ee8ebf0c1c68f2638505655f5328de5abd31390ed8699b760befc3aeb689d1 |
| CELRUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | b4efdbfeccea54da8eb823c68107e07057ad6da3676cca6016c8c30a8d070ab7 |
| CELRUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | ba774d18d5b1f8358a63f0ae59b0ec68051f1106c9fe6fc93d2d3afa2e0f0b00 |
| CHZUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 4238c26ddeb0744eabac73cd3fbedc313ac06832040c6f17b9f0abd10bbd84a1 |
| CHZUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 03db41bb226061e18bddf2aad79d36e8ca5a49d85ea13a090f9e059c5214e6f5 |
| CHZUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 6bce79d4641d9a0119aea05f077d34527b1bc4c6db397ddaabaad6f59a2c58e6 |
| COMPUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 8a85a09a6ae060e7676e01104d1d2c0b25071f5774659b5b0ac246a005b48507 |
| COMPUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 66e0049f3521f75645153e2afc3c3a9750f5ec0c9cbc0f1bf8e60f6f4d385543 |
| COMPUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 43e3bddf8cefd20070107839ccf1d88b74a3dcea18d544c3f26bca233239757b |
| CRVUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | c9c20e0fa95961b85c55d13914fdc7d9974ed0a3d82470af8ef1da3f24121976 |
| CRVUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 5e150c158129453460ed7ef6b333151c9a65975d25a33523a6df471202d5998e |
| CRVUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | c80bab1ef34fa46859a1d95a61fe6290053d3748e099b9dcebd2a16e91ccf30a |
| DASHUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 012834157482b71cdaa37cc643ac498765f42680308e273b0eee6c078a6cb834 |
| DASHUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 2b5e13330b5ac86c8c9e569e375bcd0b0fb34a8f701723a8194f1df3c824b68d |
| DASHUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | f5f05d073f56cc1875f603240da5182fab1852d897f6d27ae06764228ced7eb4 |
| DOGEUSDT_15m.json | 1199 | 2026-08-28T10:15:00+00:00 | 2026-09-10T21:00:00+00:00 | 93322e874b1a0994fa603c0103caef265140761a6dda6ebf93d965891e767f0e |
| DOGEUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 22942f13f8035293e258757464fdc678d5afb6a1e5960830ee7b75889e875d29 |
| DOGEUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 1d37f42efa2779df483874579c86c2b754e88122ed5e4b035d1719fa5aa2fe89 |
| DOTUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 623dce93902623536ffa8b789a81e419440ab6dabffd3975728da11c8b26ae99 |
| DOTUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 884a396ea53a3058b013e3c035896a066ccf010ac826cbd9e19c7e02084839d8 |
| DOTUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 967bb195b822cdcfad876cd6b16e92046d725c714ff82630006cc53a0160ddb0 |
| EGLDUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | bbb93579a386e4264797c7e9245ed15b00f3d14ca6a9cddd31346cc41a7dd669 |
| EGLDUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 97e1631535098d08e54a2c943eebe710e8db2d3899c7ef064d148f4fbda12471 |
| EGLDUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | d7a71b480bfa31c1a5505d876a8f9e6cb90fdf376fd146944e07a4789b31e8e4 |
| ENJUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | e702060c303838a6f3cc5e62472cca356c6f6d615fe6e21412a812dc4ae9115c |
| ENJUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | a4feb55871b9e687431ba2c0c26be595bacd7afbb82e4de5cc3d8ee669670d7a |
| ENJUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 91fa961b5a96719e1884980c9d18bf329b2c0052ef8d315569ebebea4c7fc3f7 |
| EOSUSDT_15m.json | 1199 | 2025-05-08T21:15:00+00:00 | 2025-05-21T08:45:00+00:00 | 6be787f2248c3c30b0d7a205b700b88cb3cf191fdad21561fe550cb0db9d0bc1 |
| EOSUSDT_1h.json | 1199 | 2025-04-01T10:00:00+00:00 | 2025-05-21T08:00:00+00:00 | ef2eb5e67eb5f6b6e161cc75786b8c482e307eb543a518ecc4387d7c60e7c52c |
| EOSUSDT_4h.json | 213 | 2025-04-15T20:00:00+00:00 | 2025-05-21T04:00:00+00:00 | f53ac86b48688581c6ec8f0a65ed864e91f5f167577d69ed0472cac1c49b3600 |
| ETCUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 498249f1460a275fae0b29e22c9a5a2a8e6a9cb0d3f74ae90e673ff8b87dbfb1 |
| ETCUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | aef696785c97a763272b72eb6fa35904b84fac1f82a854d7eeb7233308507ede |
| ETCUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | ffce815e146089712c543a3e13b56c8151913f7492675dc801fb9f07347209ad |
| ETHUSDT_15m.json | 1199 | 2026-08-28T10:00:00+00:00 | 2026-09-10T20:45:00+00:00 | 1a4548f13cfbddd326eda57c387c8a9963ec9ec0235f9c0bdd70810565260af9 |
| ETHUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 790ea210709e3bf2943235dc1c45e12a789ae1e1bdce445197fb7f5f5a5da878 |
| ETHUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 3fe83f63f014c993c67ede73b9cd534201c71f1f77ec7a05420be7f44fbdfd0e |
| FILUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 14caaf0bd25c8a841c6b73bbec019e81a5735cfc7fb4b58ffbd4379b0ae48f88 |
| FILUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 2c5151086ca71b73808748ea90a99bd79ce7d5b120f353c732ae94ff063a9db7 |
| FILUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 930fac67cc6ab6fdd8d566db14c71ba7c76663ee280948044d3c997163c4af9f |
| GALAUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | b2a887f2fe76115daa077eafa1636bcbd38a9b64d758f1f3408852b56d8e94f1 |
| GALAUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | b732f7be9667c177a8e3ad81940f6abc42472a3d79127fe3c1f37014c77df0d2 |
| GALAUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | fe9368676f6a270221806fd2bd391e0f34a012d264007ae8304b374c281b7173 |
| GRTUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 5250ec49b03bb951085981a145079518ff5b3a678dd6495bfa411ba72a7a05cb |
| GRTUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 8e757f89c9b3d1bfcddb3cc1bf8ee8392837ebf8d3d0f990d90b653aff1ee007 |
| GRTUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | e2e7a8b0e4c044ce10f938277981c1186536da4c4ebc4699b46179203c04a08c |
| HBARUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 3f953c847bccec6c72c107dc3c48ccdf3c703fa411f5bea801f7a88a4abbc847 |
| HBARUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 77397899fa48e59b80ca117b2771b08c4506374e7017a240a8d77a91189b2a3c |
| HBARUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 8321dbb3f8634d26b6345bbfda69027ff40d62b6ea04a62479c48024c40b6674 |
| ICPUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | ae14df02da1bca1024119d6c62ccb072791a3c9e4b04437b7a7aaaf60c50fb01 |
| ICPUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 9a5166d7e9e64727ec6142b683a61e3067461844685a871e311b9905f84ad4ae |
| ICPUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 8ad09fd71d453bfab560412aaf72851112a081487968a9cc6a7e37aff8e19977 |
| IMXUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | ebd92553376d561f500765d4cb218c49323334023871d634857ebd0538045ea5 |
| IMXUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 0f9aabb4bf984963e03bc3602a0cdad5811ee6025a1866053a7ac319bfe7841f |
| IMXUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | aeaad03251d400a8cefb19021964031362bae15ada3d4ea081025d7ebf2e7424 |
| INJUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 811bffa268c676fd2eea1749d3c401789752427e57db8b70fad5d94281b2b39a |
| INJUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 7f4e406a06864064629e13344c4f29069c246d9b79412b601154249371c8a4f8 |
| INJUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 1e4cfd5dfbb7c4acfad7622a0481c503f43ed7b9dbc8ca0e0105f6fa3c592f8a |
| IOSTUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | b719851f3721f99df68b8c906e24936db6b5d3c962ea8a9b9e627b500cb2e513 |
| IOSTUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | d7deec2d927287b823c4a66b0efc390f7f27a3baf410a384c9307ae3ca10b5cd |
| IOSTUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 8239d8869cdae78915780d8a210d8b70a5cedbcfbf168872fe8999e841134ba0 |
| IOTAUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | f4fd1ddcdc947a4160c7c8b5147c7c30921f69f9763cdb71517212e224306d0b |
| IOTAUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 0304bad2064f1e085698a13f9cf9d00995592a238b1ddf5f87377340e62f76d9 |
| IOTAUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 8bba0023eff86a789985ceb3f6e65093caeadc9876833b8152b37e0e79e960b7 |
| KAVAUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | f37080b6531f1350cc98e95fe643e29ce94fce564476da1b3ce64f3ee62fe9fe |
| KAVAUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 07f3c00e7b04f0aa66c46aa0cdcaa51ebea0fd0762284c147d2f75dd1bccbdc8 |
| KAVAUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 2af0416ebdbf7e616ee42955f44dabec61bbb49d165eae4c08f81df86a1a0192 |
| KNCUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 8247d522b20025a32da7596fea3d544a999a9f7b7795ecf2b425ba5331e368e1 |
| KNCUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 043d455a3ebadfc562fba7cb794659267fe3804534bc7dc2f29b557151aa4d6a |
| KNCUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | cf38a93c74eccd0c035af2a582d077693dc37016aa35f25afc32dbc48334b09d |
| LDOUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | f6965d5f0e36f8c490d3c82903b0bb2add31a7fb77ea8d14b3018bb904e6f7b4 |
| LDOUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | c7a5a297321300002d4e1f1ca6d59defe8427841de089079b044d63c181a5d75 |
| LDOUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | f9e4d89761646ae8e5a5ee6aa6077cf29212f6c9baf5d9eda15d19eeead124a0 |
| LINKUSDT_15m.json | 1199 | 2026-08-28T10:15:00+00:00 | 2026-09-10T21:00:00+00:00 | 268781a04da4e6d3b23cda0dd99f556153c0ebe0d07209cf96baa9d1cb53649d |
| LINKUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 2004a503c05dfb4f115f5b6713d81ca7734d8e1080521b8b35b9db2276565947 |
| LINKUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 0170dae444208e2f14b0b57d796f13ae04701cc80c92c109362a91f91a1a2ae6 |
| LRCUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 64243a783d9f90c0ed133bd5a3fd8ea3fc641f087e691be83afe11ed69e81e23 |
| LTCUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | b8f6fc15d0684fb12186e0814f4c85ba787527caab798a3de671e55a3118da28 |
| LTCUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | eb4e83ec0982e3c33f4fec4afe72505a1db776a5a03fb756174cbde2bc84c1c7 |
| LTCUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 1bfc6c31ad50a7101861c72278ca406872521f133fbc2d57ddb94010e40232da |
| MANAUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 0a1a6945f1a8e8af8501139c1c31ea998ad0c332d1d5b1bf21378a167e214181 |
| MANAUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 82126a2db2303e36b07c9b202f2e377ec65295c3e32863697b01350ae719dc16 |
| MANAUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 3d22712dc1b335b78449528e50f4dac0f358b49859b46255316259a40351f971 |
| MKRUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 74a8b36585afc222f866b2346b0cb1c912d886cf477b5fa5b477f0fb6f491cc0 |
| MKRUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 10929e4358bc066e54897033db907ecda0049d5417a60c991c01d60d6f9ca31e |
| MKRUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | a9cce8b4874a9ac50a967d6e02b5729253b53e9572883e496a9f8ec376e7354c |
| NEARUSDT_15m.json | 1199 | 2026-08-28T10:15:00+00:00 | 2026-09-10T21:00:00+00:00 | 747f4bd94d77eab41833ff917a4b3e23f14d2e616a4dd6849e77c0c8f9f0345c |
| NEARUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 90f1df4cab3fa7de2efd54788e3c545648b8f9de4194cee0aebe30e414f8f741 |
| NEARUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | a38e27ba3e7ad61abf0d4847740bb55e06266ee6b48c98338f9862e947de8b51 |
| NEOUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 719fa7b7140a4e3c87707ec6e42eb830375c15c286a5da1d10b19c5868d64e16 |
| NEOUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | ff7a70c0061bd0e05d7f05b78d0f0f326803dfafbd1d467c7c1cb37be11ee903 |
| NEOUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | f41313904a28820e18bb0ef2d5ca7413e3374bf67142d0ace431294de67df81c |
| ONEUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 9dc52e32fb9375579aa9caef93f6e463f4aa2826534da4ead20b83a0356143c3 |
| ONEUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 34247ee509a06d0673b85babe4a1deb8153b8c8349857e36391e7104e4e0a97d |
| ONEUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | ed2d8ef58d028301b2d9b45a5f5ce00fb9f7c30f0c1c7da1eac6668340d2e4b8 |
| ONTUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 6083db39bcb9183368195a3eccfa4b794570823de840d1232c5ff66906c6ad76 |
| ONTUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | d145ab293982021a68c7b3c3094d32d8340aad23cfec6aa57fabc929c52b33f9 |
| ONTUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 3eb016944cbf7fc2396aaa8cfcfa8fa871acd6fc4d843c484cf254d88501b9ab |
| OPUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 23866ef03d145bbba0d5362bae89c578aba4aa88f95b786e1c0fcc2da87b0f11 |
| OPUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 0c6f959e7300ed437978552112698b22ccce57ae118bb8f2e60f3b8b0303fc7a |
| OPUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 0339b2305140f5f3bf2a1dd48861374d32f924ea0f666af44f2a8e15c1a0eddf |
| QTUMUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | f54c5f44535df7791ce99ca892ec953967aab844a601759d1bfa0e4c0a202d3f |
| QTUMUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 24b79be217632f9c6d7f6611e19122b2178e0d22a44871be288371c435ac4d3b |
| QTUMUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 78a9430e645fa3fbfe36b49df42b58c902ea936ac44b4556cf7508caf901fb17 |
| RUNEUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | fa63da6162be69da146763eae66ff487cc3b177308492c48caa60201420138e1 |
| RUNEUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | b8b4b0a089865ff4c027d97cc6c662397c0265d8f52d39d78ca5b24e7a487d69 |
| RUNEUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | a3f562d0f90b9addb8735137eeaef6ab04116b18936aa31cb1abd6cf05a5ebfb |
| RVNUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | d03460ccc42e3cfcad20c41ebbda55c51abbab8d424395de3fb214533180fada |
| RVNUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 1e990a2d789a114fecd23a13ec3c66b23f2de2d3480283f2bdbf07af4f18f91c |
| RVNUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 34b0059aad2bede7293dcd9b081c1f3642693a3a9eb0d13694c4f01d1153fd78 |
| SANDUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 6bfba0a9f3352a9ada0af83226f4d7b3e77342baa2c4cf25117df40372c3c46f |
| SANDUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 6749c71913e17b2bdb9b0f9c437ab54fb8dd240ec83a547fb2a8f13de2e4885c |
| SANDUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 7459130bc9cdfe26aa00db3ac6d7ae2f961a7d5134786674a56bd3311142dab8 |
| SEIUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 8949473dc23d199f3cd76ba1ba47d2172ecbf8192a13a6a7d5df394ee8606373 |
| SEIUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 8f12fc6c8bd731757a8dbd2f3768dee841065a54049dbbaf2ed09ecaa0d5a9e7 |
| SEIUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 89f9074199ef07104252e935abb10ebd8a91f4ae96dbea41ef460aed3650dbda |
| SNXUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | d17118189687c0aceb1d5e5dfbebe094b163dbc7da9f00827c79526a983b0c40 |
| SNXUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | c5e7cf01010d46a2e2d935b5488d0a4e2fd5db9ab9d5ec546696fbc983aa30ff |
| SNXUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 4c4bfbcd7a804d5699877b50414a0719fe51ce851ed76a214973e66c73b184f3 |
| SOLUSDT_15m.json | 1199 | 2026-08-28T10:00:00+00:00 | 2026-09-10T20:45:00+00:00 | d07317d13226c76eea5854ce9ad0944ce2f34fbe53f4b7a079916bf485a93f7d |
| SOLUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 123c01999e5fe6e3c17d525f35c3da1fcc0aaedec240516b7c7dfa9cb6d15b91 |
| SOLUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 7cf2be3eeb642fa54e86eef27a41b3302a287666b2855efa231557400afd34db |
| STXUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 0eca03999d710af16829e50b237a7f6011c0efca6ac9c3f33610f975f6fe9583 |
| STXUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 01b76bab4282c85eac275c27badf9b9eb88c16fddb951b7b10c81b3dd37707e8 |
| STXUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 82cc614d2fa25ced7fe458fdfe2015a9bc6027cad269fe47b1b13ea84f41a9c3 |
| SUIUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | d9aefa41964a0deb3b122b7ec37279deb638c71c14fa4c0b7226d145a7edf5c4 |
| SUIUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 408322362c796572846a0154c01726a2924e831fce419f8e129318c8362d2aad |
| SUIUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 8fa9d05403309a8c5940be773e93b32ecbcad0439e165f613e6bf76c0a0c8965 |
| SUSHIUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 4fe5fe86ec95071b7b02b6bc65c1ad7fc2e5adec58073594ec3dbb9ca7a57f71 |
| SUSHIUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | b36c0aea9cd149d8f82b3b6f4de3e0a06a0e4fc3546536d40cebea5849bc6595 |
| SUSHIUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 11e6f3e7abf15d5c33b6fb5fdd41f606356b81eaf20f24e30281077d4443264e |
| THETAUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 79cf6893ab7bcc0f9e5a91ff96f7cf5d505beb76113ae8a60a0240379476ea91 |
| THETAUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 5af7305f96a0c79a2a2a1b67140091c6ee056e7653a85ecb90f63f81e8c5aebf |
| THETAUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | ced40892f4075997be95bbc922d6c89e694f8c05b40b5d0ef72490c0e5cbb506 |
| TIAUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 88e7ea768c8f88fedd66b7c794dc9b5e182f07caa7e8ca637a9380de98239627 |
| TIAUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 706b0ee5f4d5264a6121407cf9b934ddbe125fd7679ff891d37bf97962c18ee9 |
| TIAUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 5c5bc63c7534893e97d3de8fac4844eea4c6d7c4995124b676159ea6c41c04c7 |
| TRXUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | fa8d94bb91b0c8ef42cc61e2388777e475dc624e43e8e9b42e962d8d7a239378 |
| TRXUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | bdb7faeb42b95fa10736944927668e6239597e5ec4bc65347dccfcb37e06c442 |
| TRXUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | fb31cecacda0bb9837f6fb2a933f2cbfb64f1b0f3df8fd45afd7c28e16f354e6 |
| UNIUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 45f9002f3013cf34d665183f072c87e86c4682c4b4a13f0bff2df44c31a0ad01 |
| UNIUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 783268df2059acfc8c938a1d6db1c9ff75576cf5ceee5491436bded2101b492f |
| UNIUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 0f571d277117eebda8949c90ee5e55dc62ee6a876d0189bee468a0815abf4bb7 |
| VETUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 3fc1f7e9daa85ecad6ca7f6f14eb27e88d248a79d285dd6cef9fa5a8f1cd2bd8 |
| VETUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 82cb7201c6473fe215fe94c788ac29991e908bae453afe6ebe3fdee93f35a9dc |
| VETUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 7b0ac95ed211ea21573d373d0977e692510276fc17b3f21b0cab40af6c0e3e16 |
| WAVESUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 9f15abf5f3a713416c79a694c153eebb24c6cbe257e7dd9309cf5591c0053b97 |
| WAVESUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 91f3886e4d35e20d2a2ce48029d84c9af55774a4ca143b0c263d2279e8ed2eed |
| WAVESUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 513fd82eef1c0f7c2f69a83bbda9a26a1689a9eba3cd40011b2392fe493028b8 |
| WLDUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | e3444e0861d70c25fba8f73fdb4602c6694b0750fde2f7d113fe31e2b02aa21d |
| WLDUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 4b3953a8a854d9e0332be51b9071048f1030d04ec76f4cff3e103e31b241bba4 |
| WLDUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 940cb1f25338ef8575eeded7bc18864101410e2c0182b26467f892565083184d |
| XLMUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | b27c02f83a25b7bda6b9f1dc009a65ee6b59ba8b391a641ee52a1ae769acfa90 |
| XLMUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 7cfc7c165c2119749837f27c36fd2f0dabd340bbbb1fae35afdade789b86508c |
| XLMUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 3f8a6e9ccfd217ef4ead693f2b639da0e5d837c26094de0f01654f9a163bdcc3 |
| XRPUSDT_15m.json | 1199 | 2026-08-28T10:15:00+00:00 | 2026-09-10T21:00:00+00:00 | b93072ad5064857d8fd0f1ccf2ba0df1b78ffd7a15cd47373855e74cb10876a3 |
| XRPUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | fa69aeec807b20df3f6ba3cc504be44bc59b4f0086462c7d9d7cdf92a8248389 |
| XRPUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | ebe0728c3747a0e0feb5a7dac55756edb35939eea9dadd4d625609e65f86ec75 |
| XTZUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 3961503ab57ed1c461e91556b8496950f530518940cd096b473d8b26f79feb5a |
| XTZUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | d5440e1723d69f37d9d4cb533a54509c672f8fe689b5879eb287b1faa927859f |
| XTZUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | d30a2b2887d04b97e75d73a58e09102c8d45ab11da1c450736c83fb78cce2a56 |
| YFIUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 8c3fc4520ef2970e7d126edc650e80393a274c8e95eb69e6f351502c96d89280 |
| YFIUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | cacd75992c221e1e0fdf02126d469297ab90e2ab1fc3f9af24e71b4205fdb41c |
| YFIUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 0a7a3a4baea8ff58bfda66dab020090b3ef0c293ccecd0df5aa175373ec3f45f |
| ZECUSDT_15m.json | 1199 | 2026-08-28T10:15:00+00:00 | 2026-09-10T21:00:00+00:00 | 6751857425e43b1fc4a0fe0a684e1de40378a905bb34c344a6d1462535332105 |
| ZECUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 294a7078dfe34f3b91e33847ae46ef3e1e7a2507b8505ad16cf5ed6394a707d3 |
| ZECUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | 779039cbe25dd37a5a968206a896d11d907c9db6f0282bd958bd5bee2e0f9925 |
| ZILUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 74c08676201765f89a8fbdf1282b0483a39ab5f1eba5132fc0279530acf1ed90 |
| ZILUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | 8a92e0dfa6b8e4eb78418e972c5598b2058f7c6331a7b587c72f2366e03d95c3 |
| ZILUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | fe67998f0267101c674dd5283d07a877c793a521660364678cedca55953c04d7 |
| ZRXUSDT_15m.json | 1199 | 2026-08-29T09:30:00+00:00 | 2026-09-10T21:00:00+00:00 | 9f0fa2cc8af9f6ffde3b0606a90225f00d29ea1eb294fddb375b715567d08088 |
| ZRXUSDT_1h.json | 1199 | 2026-07-22T22:00:00+00:00 | 2026-09-10T20:00:00+00:00 | e8018224dce1b695a6622ad7936fd37ef1bfa6e24ce46ee0362c84023b16b2bd |
| ZRXUSDT_4h.json | 1199 | 2026-02-23T00:00:00+00:00 | 2026-09-10T16:00:00+00:00 | b4f42c7e494c22eee6196ddfa943bf002666ae83379b28d69f5ec8e708a7405e |

## Sensibilidade: painel recente com os três TFs

69 símbolos / 207 charts. Exclui EOS (2025) e LRC (só M15). Não muda thresholds nem usa o resultado para selecionar ativos.

| TF/lado | n h10 | ΔP h10 | Δ blocos 1/2/3/4 |
| --- | --- | --- | --- |
| 15m/EQH | 372 | 0.005 | -0.035/-0.036/-0.033/0.138 |
| 15m/EQL | 402 | 0.095 | -0.105/0.002/0.121/0.232 |
| 1h/EQH | 464 | -0.026 | -0.032/0.065/-0.144/0.020 |
| 1h/EQL | 328 | 0.032 | -0.030/0.089/-0.168/0.085 |
| 4h/EQH | 456 | -0.061 | 0.102/-0.101/-0.041/-0.108 |
| 4h/EQL | 457 | -0.057 | -0.012/-0.011/-0.125/-0.084 |

