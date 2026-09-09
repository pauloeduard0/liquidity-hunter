"""The environment contract of `config.Settings`.

Only the structural-stall switch is covered: it is the first setting anything
actually reads (`app.dashboard_data._STRUCTURAL_STALL_ENABLED`), and turning
the reading off in a deployment depends on this behaviour being what the
comment says it is.

`_env_file=None` on every construction: without it these assertions would pass
or fail according to whether the machine running them happens to have a `.env`.
"""

import pytest

from liquidity_hunter.config import Settings


def test_the_structural_stall_reading_is_on_by_default() -> None:
    assert Settings(_env_file=None).structural_stall is True


@pytest.mark.parametrize("value", ["0", "false", "False", "no", "off"])
def test_the_environment_can_switch_it_off(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("LIQUIDITY_HUNTER_STRUCTURAL_STALL", value)
    assert Settings(_env_file=None).structural_stall is False


@pytest.mark.parametrize("value", ["1", "true", "True", "yes", "on"])
def test_the_environment_can_switch_it_on(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("LIQUIDITY_HUNTER_STRUCTURAL_STALL", value)
    assert Settings(_env_file=None).structural_stall is True


def test_an_unrelated_variable_does_not_reach_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prefix is load-bearing: a bare `STRUCTURAL_STALL` is not this knob."""
    monkeypatch.setenv("STRUCTURAL_STALL", "0")
    assert Settings(_env_file=None).structural_stall is True
