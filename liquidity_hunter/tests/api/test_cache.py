"""Tests for `liquidity_hunter.api.cache`."""

from liquidity_hunter.api.cache import TTLCache


def test_get_or_set_caches_within_ttl() -> None:
    cache: TTLCache[int] = TTLCache(ttl_seconds=60.0)
    calls = 0

    def factory() -> int:
        nonlocal calls
        calls += 1
        return calls

    assert cache.get_or_set("key", factory) == 1
    assert cache.get_or_set("key", factory) == 1
    assert calls == 1


def test_get_or_set_per_call_ttl_overrides_default() -> None:
    cache: TTLCache[int] = TTLCache(ttl_seconds=60.0)
    calls = 0

    def factory() -> int:
        nonlocal calls
        calls += 1
        return calls

    # A zero TTL expires immediately, even though the cache default would
    # have kept the entry for a minute.
    assert cache.get_or_set("key", factory, ttl_seconds=0.0) == 1
    assert cache.get_or_set("key", factory, ttl_seconds=0.0) == 2
    assert calls == 2


def test_get_or_set_keys_are_independent() -> None:
    cache: TTLCache[str] = TTLCache(ttl_seconds=60.0)

    assert cache.get_or_set("a", lambda: "first") == "first"
    assert cache.get_or_set("b", lambda: "second") == "second"
    assert cache.get_or_set("a", lambda: "changed") == "first"
