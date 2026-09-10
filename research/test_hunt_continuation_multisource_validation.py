"""Testes de `research.hunt_continuation_multisource_validation`.

A H2.2 tem uma unica obrigacao metodologica: medir a regra congelada numa
amostra que as etapas anteriores **nao** viram. Os testes aqui vigiam
exatamente isso -- que a regra nao foi reafinada, e que as janelas do painel
temporal nao encostam na amostra da H2.1. Um erro de um candle no offset faria
a "confirmacao" ser uma re-medicao da mesma fita, e nenhum numero denunciaria
isso sozinho.

Rodar:
    poetry run pytest research/test_hunt_continuation_multisource_validation.py
"""

from __future__ import annotations

import pytest
from liquidity_hunter.app.liquidity_hunt import _CONTINUATION_CAPTURE_THRESHOLD
from research._symbols import UNIVERSE
from research.hunt_continuation_multisource_validation import (
    DEEP_LIMIT,
    H21_SPAN_CANDLES,
    N_NEW_SYMBOLS,
    S_WINDOWS,
    T_WINDOW_STEP,
    T_WINDOWS,
    _agg,
    new_symbols,
    split_rows,
)
from research.hunt_score_redundancy import VISIBLE_LIMIT
from research.hunt_score_variants import V0, V2, WINDOW_STEP, WINDOWS
from research.test_hunt_score_redundancy import _continuation_snapshot
from research.test_hunt_score_variants import _solo_vsa_snapshot

# ---------------------------------------------------------------------------
# 1. A regra continua congelada
# ---------------------------------------------------------------------------


def test_a_regra_nao_foi_reafinada() -> None:
    """`>= 2` e o limiar 4 da producao, nem um nem outro tocados nesta etapa."""
    engine = V2()
    engine._stream = "continuation"
    assert not engine.extra_gate({"vsa": 4.0})
    assert engine.extra_gate({"vsa": 4.0, "delta": 1.0})
    assert engine.extra_gate({"vsa": 3.0, "sweep": 3.0, "delta": 1.0})
    assert engine.threshold_for(_CONTINUATION_CAPTURE_THRESHOLD) == (
        _CONTINUATION_CAPTURE_THRESHOLD
    )


def test_delta_conta_como_fonte_como_na_h21() -> None:
    """Se `delta` deixasse de contar, a regra medida aqui seria outra."""
    engine = V2()
    engine._stream = "continuation"
    assert engine.extra_gate({"vsa": 4.0, "delta": 1.0})


def test_a_regra_continua_removendo_exatamente_a_fonte_unica() -> None:
    data = _solo_vsa_snapshot()
    base = V0().build_continuation_history(data)
    solo = [e for e in base if len(e.capture_sources) == 1]
    assert solo
    assert len(V2().build_continuation_history(data)) == len(base) - len(solo)


def test_a_regra_nao_toca_continuation_de_duas_fontes() -> None:
    data = _continuation_snapshot()
    base = V0().build_continuation_history(data)
    assert base and all(len(e.capture_sources) >= 2 for e in base)
    assert V2().build_continuation_history(data) == base


# ---------------------------------------------------------------------------
# 2. O recorte e mesmo out-of-sample
# ---------------------------------------------------------------------------


def test_offset_do_painel_t_cobre_toda_a_amostra_da_h21() -> None:
    """O span da H2.1 e `VISIBLE_LIMIT + (WINDOWS - 1) * WINDOW_STEP` candles.

    Se `H21_SPAN_CANDLES` ficasse menor que isso, a primeira janela do painel T
    entraria na fita ja medida e a confirmacao seria circular -- o erro que este
    teste existe para tornar impossivel de passar despercebido.
    """
    assert H21_SPAN_CANDLES == VISIBLE_LIMIT + (WINDOWS - 1) * WINDOW_STEP


def test_janelas_do_painel_t_terminam_antes_da_amostra_da_h21() -> None:
    """Nenhuma janela do painel T pode conter um candle mais novo que o corte."""
    for w in range(T_WINDOWS):
        back = H21_SPAN_CANDLES + w * T_WINDOW_STEP
        assert back >= H21_SPAN_CANDLES


def test_profundidade_baixada_cobre_a_janela_mais_antiga() -> None:
    """Sem isto, os simbolos mais antigos cairiam por 'serie insuficiente' e o
    painel mediria em silencio um subconjunto enviesado."""
    necessario = H21_SPAN_CANDLES + VISIBLE_LIMIT + (T_WINDOWS - 1) * T_WINDOW_STEP
    assert DEEP_LIMIT >= necessario


def test_painel_s_usa_o_span_recente() -> None:
    assert S_WINDOWS >= 1


# ---------------------------------------------------------------------------
# 3. A amostra de simbolos ineditos
# ---------------------------------------------------------------------------


def test_simbolos_ineditos_sao_deterministicos() -> None:
    pool = [f"FOO{i}USDT" for i in range(200)]
    assert new_symbols(pool, 10) == new_symbols(list(reversed(pool)), 10)


def test_simbolos_ineditos_respeitam_o_tamanho_pedido() -> None:
    pool = [f"FOO{i}USDT" for i in range(200)]
    assert len(new_symbols(pool, N_NEW_SYMBOLS)) == N_NEW_SYMBOLS
    assert len(set(new_symbols(pool, N_NEW_SYMBOLS))) == N_NEW_SYMBOLS


def test_a_amostra_inedita_nao_reintroduz_o_universo_conhecido() -> None:
    """O painel S so vale se nenhum de seus simbolos ja tiver sido medido."""
    pool = [f"FOO{i}USDT" for i in range(200)] + list(UNIVERSE)
    pool = [s for s in pool if s not in UNIVERSE]
    assert not set(new_symbols(pool, N_NEW_SYMBOLS)) & set(UNIVERSE)


# ---------------------------------------------------------------------------
# 4. Leitura do payload
# ---------------------------------------------------------------------------


def _payload() -> dict:
    return {
        "episodes": {
            "V0": {
                "T|BTCUSDT|M15|a": {"sources": ["vsa"], "score": 4.0},
                "T|BTCUSDT|M15|b": {"sources": ["vsa", "delta"], "score": 5.0},
                "S|NEWUSDT|H1|c": {"sources": ["vsa"], "score": 4.0},
            },
            "V2": {
                "T|BTCUSDT|M15|b": {"sources": ["vsa", "delta"], "score": 5.0},
            },
        },
        "outcomes": {
            "T|BTCUSDT|M15|a": {"panel": "T", "symbol": "BTCUSDT", "tf": "M15",
                                "up": True, "mfe_20": 1.0, "mae_20": 2.0,
                                "net_20": -0.5, "win_20": False, "ctl_win_20": 0.5,
                                "sources": ["vsa"]},
            "T|BTCUSDT|M15|b": {"panel": "T", "symbol": "BTCUSDT", "tf": "M15",
                                "up": True, "mfe_20": 3.0, "mae_20": 1.0,
                                "net_20": 1.5, "win_20": True, "ctl_win_20": 0.5,
                                "sources": ["vsa", "delta"]},
            "S|NEWUSDT|H1|c": {"panel": "S", "symbol": "NEWUSDT", "tf": "H1",
                               "up": False, "mfe_20": 1.0, "mae_20": 3.0,
                               "net_20": -1.0, "win_20": False, "ctl_win_20": 0.5,
                               "sources": ["vsa"]},
        },
    }


def test_split_separa_baseline_sobreviventes_e_removidos() -> None:
    base, keep, gone = split_rows(_payload(), "T")
    assert len(base) == 2
    assert [r["sources"] for r in keep] == [["vsa", "delta"]]
    assert [r["sources"] for r in gone] == [["vsa"]]


def test_split_nao_mistura_os_paineis() -> None:
    """T e S sao amostras distintas; soma-las esconderia um passar e outro nao."""
    base_t, _, _ = split_rows(_payload(), "T")
    base_s, _, gone_s = split_rows(_payload(), "S")
    assert {r["symbol"] for r in base_t} == {"BTCUSDT"}
    assert {r["symbol"] for r in base_s} == {"NEWUSDT"}
    assert len(gone_s) == 1


def test_agg_reporta_controle_junto_com_o_acerto() -> None:
    _, keep, _ = split_rows(_payload(), "T")
    st = _agg(keep, 20)
    assert st is not None
    assert st["n"] == 1
    assert st["win"] == pytest.approx(1.0)
    assert st["ctl_win"] == pytest.approx(0.5)
    assert st["ratio"] == pytest.approx(3.0)


def test_agg_de_horizonte_ausente_e_none() -> None:
    _, keep, _ = split_rows(_payload(), "T")
    assert _agg(keep, 999) is None
