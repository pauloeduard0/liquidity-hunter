"""Testes de `research.hunt_pool_lifecycle`.

Dois riscos dominam esta medicao, e os dois sao silenciosos.

O primeiro e **ler o futuro**: `zone.invalidated_at` e calculado sobre a janela
inteira, entao classificar um raid com ele daria uma resposta plausivel e
contaminada -- exatamente o erro que a H1 encontrou no join HTF. Daí o teste de
truncamento ser obrigatorio aqui: a classificacao de um raid nao pode mudar
quando a serie e cortada logo depois dele.

O segundo e **chamar re-raid de bug**. Um nivel pago e devolvido continua sendo
um nivel; um nivel atravessado por fechamento nao. Se os tres estados
colapsarem em dois, a prevalencia de zumbis sobe sozinha.

Rodar:
    poetry run pytest research/test_hunt_pool_lifecycle.py
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from liquidity_hunter.app.liquidity_hunt import LiquidityHuntEngine
from liquidity_hunter.core.domain import Candle, LiquidityZoneType, TimeFrame
from research.hunt_pool_lifecycle import (
    INTACT,
    PARTIAL,
    SPENT,
    PoolId,
    ZombieFilterEngine,
    classify_raid,
    pool_state_at,
    pools_of,
)
from research.test_hunt_score_redundancy import BASE, _data

LEVEL = 101.0


def _c(i: int, high: float, low: float, close: float) -> Candle:
    return Candle(
        symbol="TESTUSDT",
        timeframe=TimeFrame.M15,
        timestamp=BASE + timedelta(minutes=15 * i),
        open=100.0,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        taker_buy_volume=50.0,
    )


def _flat(i: int) -> Candle:
    """Um candle que nao encosta no nivel 101: nada acontece nele."""
    return _c(i, 100.4, 99.6, 100.0)


# ---------------------------------------------------------------------------
# 1. Os tres estados
# ---------------------------------------------------------------------------


def test_pool_sem_pavio_anterior_esta_intacto() -> None:
    candles = [_flat(i) for i in range(10)]
    st = pool_state_at(candles, LEVEL, BASE, candles[-1].timestamp, True)
    assert st.state == INTACT
    assert st.prior_wicks == 0
    assert st.first_touch is None


def test_pavio_que_atravessa_e_volta_deixa_o_pool_PARTIAL() -> None:
    """Pago e devolvido: o nivel foi tocado, mas ninguem fechou alem dele."""
    candles = [_flat(0), _c(1, 101.5, 100.0, 100.5), _flat(2), _flat(3)]
    st = pool_state_at(candles, LEVEL, BASE, candles[-1].timestamp, True)
    assert st.state == PARTIAL
    assert st.prior_wicks == 1
    assert st.first_touch == candles[1].timestamp.isoformat()
    assert st.breached_at is None


def test_fechamento_alem_do_nivel_deixa_o_pool_SPENT() -> None:
    candles = [_flat(0), _c(1, 101.9, 100.0, 101.5), _flat(2)]
    st = pool_state_at(candles, LEVEL, BASE, candles[-1].timestamp, True)
    assert st.state == SPENT
    assert st.breached_at == candles[1].timestamp.isoformat()


def test_o_estado_e_espelhado_para_o_lado_de_baixo() -> None:
    """Um pool de EQL e consumido por baixo; a regra tem de inverter inteira."""
    candles = [_flat(0), _c(1, 100.4, 98.0, 98.5), _flat(2)]
    st = pool_state_at(candles, 99.0, BASE, candles[-1].timestamp, False)
    assert st.state == SPENT


# ---------------------------------------------------------------------------
# 2. Causalidade
# ---------------------------------------------------------------------------


def test_o_candle_do_raid_nao_conta_como_consumo_dele_mesmo() -> None:
    """O corte e estrito: senao todo raid se classificaria como ja consumido."""
    candles = [_flat(0), _flat(1), _c(2, 101.5, 100.0, 100.5)]
    st = pool_state_at(candles, LEVEL, BASE, candles[2].timestamp, True)
    assert st.state == INTACT


def test_pavio_anterior_ao_nascimento_do_pool_nao_conta() -> None:
    """Nao havia ordens ali: atravessar o preco antes do nivel existir nao consome."""
    candles = [_c(0, 101.9, 100.0, 101.5), _flat(1), _flat(2)]
    nasceu = candles[1].timestamp
    st = pool_state_at(candles, LEVEL, nasceu, candles[-1].timestamp, True)
    assert st.state == INTACT


def test_truncamento_nao_muda_a_classificacao() -> None:
    """O teste obrigatorio do item 3.

    A serie completa contem candles depois do raid, inclusive um que rompe o
    nivel por fechamento. Se a classificacao usasse o estado final da zona, o
    raid apareceria como zumbi na serie inteira e como intacto na truncada.
    """
    candles = [
        _flat(0),
        _c(1, 101.5, 100.0, 100.5),   # pavio anterior -> PARTIAL
        _flat(2),
        _c(3, 101.6, 100.0, 100.4),   # o raid
        _c(4, 102.5, 100.0, 102.0),   # rompe DEPOIS do raid
        _flat(5),
    ]
    raid_ts = candles[3].timestamp
    completo = pool_state_at(candles, LEVEL, BASE, raid_ts, True)
    truncado = pool_state_at(candles[:4], LEVEL, BASE, raid_ts, True)
    assert completo == truncado
    assert completo.state == PARTIAL


def test_classificacao_do_raid_sobrevive_ao_truncamento() -> None:
    candles = [
        _flat(0),
        _c(1, 101.9, 100.0, 101.5),   # rompe por fechamento -> SPENT
        _flat(2),
        _c(3, 101.6, 100.0, 100.4),   # o raid, sobre um pool ja gasto
        _c(4, 103.0, 100.0, 102.5),
    ]
    pools = [(PoolId("equal_highs", BASE.isoformat(), LEVEL), LEVEL, BASE)]
    raid_ts = candles[3].timestamp
    inteiro = classify_raid(candles, pools, raid_ts, True)
    cortado = classify_raid(candles[:4], pools, raid_ts, True)
    assert inteiro is not None and cortado is not None
    assert inteiro["state"] == cortado["state"] == SPENT


# ---------------------------------------------------------------------------
# 3. Identidade de pool e agregacao do candle
# ---------------------------------------------------------------------------


def test_pool_novo_no_mesmo_preco_e_outro_pool() -> None:
    """Caso (D): mesmo nivel, nascimento posterior -> intacto, nao re-raid."""
    candles = [_flat(0), _c(1, 101.9, 100.0, 101.5), _flat(2), _flat(3)]
    velho = pool_state_at(candles, LEVEL, BASE, candles[-1].timestamp, True)
    novo = pool_state_at(candles, LEVEL, candles[2].timestamp,
                         candles[-1].timestamp, True)
    assert velho.state == SPENT
    assert novo.state == INTACT


def test_raid_sobre_varios_pools_usa_o_mais_vivo() -> None:
    """Conservador: um pool intacto no mesmo pavio impede o rotulo de zumbi."""
    candles = [
        _flat(0),
        _c(1, 101.9, 100.0, 101.5),   # gasta o pool de 101
        _flat(2),
        _c(3, 102.6, 100.0, 100.4),   # atravessa 101 (gasto) e 102 (intacto)
    ]
    pools = [
        (PoolId("equal_highs", BASE.isoformat(), LEVEL), LEVEL, BASE),
        (PoolId("equal_highs", BASE.isoformat(), 102.0), 102.0, BASE),
    ]
    info = classify_raid(candles, pools, candles[3].timestamp, True)
    assert info is not None
    assert info["state"] == INTACT
    assert info["n_pools"] == 2
    assert info["estados"] == {SPENT: 1, INTACT: 1}


def test_candle_que_nao_atravessa_pool_nenhum_nao_e_raid() -> None:
    candles = [_flat(0), _flat(1)]
    pools = [(PoolId("equal_highs", BASE.isoformat(), LEVEL), LEVEL, BASE)]
    assert classify_raid(candles, pools, candles[1].timestamp, True) is None


def test_fechamento_alem_do_nivel_nao_e_raid() -> None:
    """A producao exige o fechamento de VOLTA; sem isso e rompimento, nao grab."""
    candles = [_flat(0), _c(1, 101.9, 100.0, 101.5)]
    pools = [(PoolId("equal_highs", BASE.isoformat(), LEVEL), LEVEL, BASE)]
    assert classify_raid(candles, pools, candles[1].timestamp, True) is None


# ---------------------------------------------------------------------------
# 4. A variante
# ---------------------------------------------------------------------------


def test_o_filtro_muda_somente_o_raid() -> None:
    """`ZombieFilterEngine` sobrescreve um metodo, e so ele."""
    definidos = {
        name for name in vars(ZombieFilterEngine) if not name.startswith("__")
    }
    assert definidos == {"_raid_signals", "ALIVE"}


def test_o_filtro_preserva_raid_sobre_pool_intacto() -> None:
    candles = [_flat(0), _flat(1), _c(2, 101.6, 100.0, 100.4), _flat(3)]
    data = _data(candles=candles)
    signals = ZombieFilterEngine._raid_signals(
        data, True, [(LEVEL, BASE)], candles[0].timestamp, candles[-1].timestamp
    )
    assert [s[0] for s in signals] == [candles[2].timestamp]


def test_o_filtro_preserva_re_raid_sobre_pool_PARTIAL() -> None:
    """Pago e devolvido continua pontuando -- o filtro nao e binario ingenuo."""
    candles = [
        _flat(0),
        _c(1, 101.5, 100.0, 100.5),
        _flat(2),
        _c(3, 101.6, 100.0, 100.4),
        _flat(4),
    ]
    data = _data(candles=candles)
    signals = ZombieFilterEngine._raid_signals(
        data, True, [(LEVEL, BASE)], candles[0].timestamp, candles[-1].timestamp
    )
    assert candles[3].timestamp in [s[0] for s in signals]


def test_o_filtro_remove_raid_sobre_pool_SPENT() -> None:
    candles = [
        _flat(0),
        _c(1, 101.9, 100.0, 101.5),   # fechamento alem -> gasto
        _flat(2),
        _c(3, 101.6, 100.0, 100.4),   # o raid zumbi
        _flat(4),
    ]
    data = _data(candles=candles)
    producao = LiquidityHuntEngine._raid_signals(
        data, True, [(LEVEL, BASE)], candles[0].timestamp, candles[-1].timestamp
    )
    filtrado = ZombieFilterEngine._raid_signals(
        data, True, [(LEVEL, BASE)], candles[0].timestamp, candles[-1].timestamp
    )
    assert candles[3].timestamp in [s[0] for s in producao]
    assert filtrado == []


def test_o_filtro_mantem_peso_e_rotulo_da_producao() -> None:
    candles = [_flat(0), _flat(1), _c(2, 101.6, 100.0, 100.4)]
    data = _data(candles=candles)
    prod = LiquidityHuntEngine._raid_signals(
        data, True, [(LEVEL, BASE)], candles[0].timestamp, candles[-1].timestamp
    )
    filt = ZombieFilterEngine._raid_signals(
        data, True, [(LEVEL, BASE)], candles[0].timestamp, candles[-1].timestamp
    )
    assert prod == filt


def test_o_filtro_emite_um_sinal_por_candle_como_a_producao() -> None:
    """Varios pools atravessados pelo mesmo pavio continuam sendo um grab so."""
    candles = [_flat(0), _c(1, 103.0, 100.0, 100.4)]
    data = _data(candles=candles)
    signals = ZombieFilterEngine._raid_signals(
        data, True, [(LEVEL, BASE), (102.0, BASE)],
        candles[0].timestamp, candles[-1].timestamp,
    )
    assert len(signals) == 1


# ---------------------------------------------------------------------------
# 5. Selecao de pools
# ---------------------------------------------------------------------------


def test_pools_of_segue_o_lado_cacado() -> None:
    """Shorts cacados leem EQH pelo topo; longs cacados leem EQL pelo fundo."""
    from liquidity_hunter.core.domain import LiquiditySide, LiquidityZone

    zonas = [
        LiquidityZone(
            symbol="TESTUSDT", zone_type=LiquidityZoneType.EQUAL_HIGHS,
            price_low=100.9, price_high=101.1, timeframe=TimeFrame.M15,
            side=LiquiditySide.BUY_SIDE, formed_at=BASE,
        ),
        LiquidityZone(
            symbol="TESTUSDT", zone_type=LiquidityZoneType.EQUAL_LOWS,
            price_low=98.9, price_high=99.1, timeframe=TimeFrame.M15,
            side=LiquiditySide.SELL_SIDE, formed_at=BASE,
        ),
    ]
    data = _data(liquidity_zones=zonas)
    altos = pools_of(data, True)
    baixos = pools_of(data, False)
    assert [lvl for _pid, lvl, _f in altos] == [pytest.approx(101.1)]
    assert [lvl for _pid, lvl, _f in baixos] == [pytest.approx(98.9)]


def test_pools_of_inclui_zona_ja_mitigada() -> None:
    """E o ponto da pesquisa: a producao nao filtra por consumo, e nem aqui --
    a exclusao e decidida pelo estado causal, nao pela selecao."""
    from liquidity_hunter.core.domain import LiquiditySide, LiquidityZone

    zona = LiquidityZone(
        symbol="TESTUSDT", zone_type=LiquidityZoneType.EQUAL_HIGHS,
        price_low=100.9, price_high=101.1, timeframe=TimeFrame.M15,
        side=LiquiditySide.BUY_SIDE, formed_at=BASE,
        is_mitigated=True, invalidated_at=BASE + timedelta(hours=1),
    )
    assert len(pools_of(_data(liquidity_zones=[zona]), True)) == 1
