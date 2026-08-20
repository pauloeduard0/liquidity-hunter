"""Tests for `GeckoTerminalDataProvider` (no network: `_get` is stubbed)."""

from datetime import UTC, datetime
from typing import Any

import pytest

from liquidity_hunter.core.domain import TimeFrame
from liquidity_hunter.data.exceptions import DataProviderRequestError
from liquidity_hunter.data.providers.geckoterminal import (
    GeckoTerminalDataProvider,
    PriceDenomination,
    clear_caches,
)


@pytest.fixture(autouse=True)
def _isolate_caches() -> Any:
    """Response and metadata caches are module scoped (they outlive a provider)."""
    clear_caches()
    yield
    clear_caches()


TOKEN = "Ge87EtsjwRQbHaqQmKRno69RFTwh9bfSsm99XNxTpump"
POOL = "5PGhKctym6odbHGo2tKMST2AjmJsb2uZBQrKkn4ZuFT5"
SYMBOL = f"solana:{TOKEN}"

# Newest-first, the order the API returns.
OHLCV_ROWS = [
    [1_700_007_200, 4.0, 4.5, 3.5, 4.2, 200.0],
    [1_700_003_600, 3.0, 4.0, 2.5, 4.0, 100.0],
    [1_700_000_000, 2.0, 3.0, 1.5, 3.0, 50.0],
]


class FakeApi:
    """Stands in for GeckoTerminal, recording the paths requested."""

    def __init__(self, *, pools: list[dict[str, Any]] | None = None, supply: float | None = None):
        self._pools = (
            pools
            if pools is not None
            else [
                {"attributes": {"address": "shallow-pool", "reserve_in_usd": "1000"}},
                {"attributes": {"address": POOL, "reserve_in_usd": "500000"}},
            ]
        )
        self._supply = supply
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    def __call__(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        self.calls.append((path, params))
        if path.endswith("/pools"):
            return {"data": self._pools}
        if path.endswith(f"/tokens/{TOKEN}"):
            return {"data": {"attributes": {"normalized_total_supply": self._supply}}}
        if "/ohlcv/" in path:
            return {"data": {"attributes": {"ohlcv_list": OHLCV_ROWS}}}
        raise AssertionError(f"unexpected path {path}")


def _provider(api: FakeApi, **kwargs: Any) -> GeckoTerminalDataProvider:
    provider = GeckoTerminalDataProvider(**kwargs)
    provider._get = api  # type: ignore[method-assign]
    return provider


def test_resolves_token_to_its_deepest_pool() -> None:
    api = FakeApi()
    provider = _provider(api, denomination=PriceDenomination.USD)

    provider.get_ohlcv(SYMBOL, TimeFrame.H1, 500)

    ohlcv_path = next(path for path, _ in api.calls if "/ohlcv/" in path)
    assert POOL in ohlcv_path


def test_candles_are_returned_oldest_first() -> None:
    provider = _provider(FakeApi(), denomination=PriceDenomination.USD)

    candles = provider.get_ohlcv(SYMBOL, TimeFrame.H1, 500)

    assert [c.timestamp for c in candles] == [
        datetime.fromtimestamp(row[0], tz=UTC) for row in reversed(OHLCV_ROWS)
    ]
    assert candles[0].open == 2.0
    assert candles[-1].close == 4.2


def test_volume_is_split_evenly_so_delta_reads_zero() -> None:
    """On-chain OHLCV carries no taker split; half each way beats a guess."""
    provider = _provider(FakeApi(), denomination=PriceDenomination.USD)

    candles = provider.get_ohlcv(SYMBOL, TimeFrame.H1, 500)

    assert all(2 * c.taker_buy_volume - c.volume == 0 for c in candles)


def test_market_cap_denomination_scales_price_by_supply() -> None:
    api = FakeApi(supply=1_000_000_000.0)
    provider = _provider(api, denomination=PriceDenomination.MARKET_CAP)

    candles = provider.get_ohlcv(SYMBOL, TimeFrame.H1, 500)

    assert candles[0].open == 2.0 * 1_000_000_000
    assert candles[0].volume == 50.0  # volume stays in USD


def test_market_cap_falls_back_to_price_without_a_known_supply() -> None:
    provider = _provider(FakeApi(supply=None), denomination=PriceDenomination.MARKET_CAP)

    candles = provider.get_ohlcv(SYMBOL, TimeFrame.H1, 500)

    assert candles[0].open == 2.0


def test_pool_and_supply_are_resolved_once_per_symbol() -> None:
    """Metadata survives the provider: the API layer rebuilds one per request."""
    api = FakeApi(supply=1_000_000_000.0)

    _provider(api).get_ohlcv(SYMBOL, TimeFrame.H1, 500)
    _provider(api).get_ohlcv(SYMBOL, TimeFrame.H4, 500)

    assert sum(1 for path, _ in api.calls if path.endswith("/pools")) == 1
    assert sum(1 for path, _ in api.calls if path.endswith(f"/tokens/{TOKEN}")) == 1


def test_bare_address_uses_the_default_network() -> None:
    api = FakeApi()
    provider = _provider(api, default_network="base", denomination=PriceDenomination.USD)

    provider.get_ohlcv(TOKEN, TimeFrame.H1, 500)

    assert all(path.startswith("/networks/base/") for path, _ in api.calls)


def test_unknown_token_treats_the_address_as_a_pool() -> None:
    api = FakeApi(pools=[])
    provider = _provider(api, denomination=PriceDenomination.USD)

    provider.get_ohlcv(f"solana:{POOL}", TimeFrame.H1, 500)

    ohlcv_path = next(path for path, _ in api.calls if "/ohlcv/" in path)
    assert ohlcv_path == f"/networks/solana/pools/{POOL}/ohlcv/hour"


def test_quote_denomination_prices_in_the_pool_token() -> None:
    api = FakeApi()
    provider = _provider(api, denomination=PriceDenomination.QUOTE)

    provider.get_ohlcv(SYMBOL, TimeFrame.H1, 500)

    params = next(params for path, params in api.calls if "/ohlcv/" in path)
    assert params is not None
    assert params["currency"] == "token"


def test_limit_is_capped_at_the_api_maximum() -> None:
    api = FakeApi()
    provider = _provider(api, denomination=PriceDenomination.USD)

    provider.get_ohlcv(SYMBOL, TimeFrame.H1, 5000)

    params = next(params for path, params in api.calls if "/ohlcv/" in path)
    assert params is not None
    assert params["limit"] == str(provider.max_fetch_limit)


def test_m30_is_resampled_from_m15_pairs() -> None:
    api = FakeApi()
    provider = _provider(api, denomination=PriceDenomination.USD)

    candles = provider.get_ohlcv(SYMBOL, TimeFrame.M30, 500)

    params = next(params for path, params in api.calls if "/ohlcv/" in path)
    assert params is not None and params["aggregate"] == "15"
    # 1_700_000_000 and 1_700_003_600 are an hour apart, so with 30m buckets
    # each source row lands in its own bar here.
    assert [c.timeframe for c in candles] == [TimeFrame.M30] * 3


def test_weekly_buckets_start_on_monday() -> None:
    api = FakeApi()
    provider = _provider(api, denomination=PriceDenomination.USD)

    candles = provider.get_ohlcv(SYMBOL, TimeFrame.W1, 10)

    assert all(c.timestamp.weekday() == 0 for c in candles)
    assert all(c.timestamp.hour == 0 for c in candles)


def test_resampled_bar_merges_open_extremes_close_and_volume() -> None:
    rows = [
        [1_700_003_600 + 86_400, 5.0, 9.0, 4.0, 6.0, 10.0],  # same week, later
        [1_700_003_600, 3.0, 4.0, 1.0, 4.0, 30.0],
    ]

    class WeeklyApi(FakeApi):
        def __call__(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
            if "/ohlcv/" in path:
                self.calls.append((path, params))
                return {"data": {"attributes": {"ohlcv_list": rows}}}
            return super().__call__(path, params)

    provider = _provider(WeeklyApi(), denomination=PriceDenomination.USD)

    (candle,) = provider.get_ohlcv(SYMBOL, TimeFrame.W1, 10)

    assert (candle.open, candle.high, candle.low, candle.close) == (3.0, 9.0, 1.0, 6.0)
    assert candle.volume == 40.0


def test_every_dashboard_timeframe_is_servable() -> None:
    """No ladder timeframe falls through to the rejection branch."""
    from liquidity_hunter.data.providers.geckoterminal import _OHLCV_PERIOD, _RESAMPLED_FROM

    assert all(tf in _OHLCV_PERIOD or tf in _RESAMPLED_FROM for tf in TimeFrame)


def test_a_timeframe_with_no_resolution_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from liquidity_hunter.data.providers import geckoterminal

    monkeypatch.setitem(geckoterminal._OHLCV_PERIOD, TimeFrame.H4, None)  # type: ignore[arg-type]
    monkeypatch.delitem(geckoterminal._OHLCV_PERIOD, TimeFrame.H4)
    provider = _provider(FakeApi())

    with pytest.raises(DataProviderRequestError, match="no OHLCV resolution"):
        provider.get_ohlcv(SYMBOL, TimeFrame.H4, 500)
