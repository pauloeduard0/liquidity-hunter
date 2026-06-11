"""A minimal in-memory TTL cache, used to avoid redundant Binance requests."""

import time
from collections.abc import Callable, Hashable
from typing import Generic, TypeVar

T = TypeVar("T")

DEFAULT_TTL_SECONDS = 300.0


class TTLCache(Generic[T]):
    """A time-based cache keyed by arbitrary hashable keys."""

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        self._ttl_seconds = ttl_seconds
        self._store: dict[Hashable, tuple[float, T]] = {}

    def get_or_set(self, key: Hashable, factory: Callable[[], T]) -> T:
        """Return the cached value for `key`, computing it via `factory` if missing/expired."""
        now = time.monotonic()
        cached = self._store.get(key)
        if cached is not None:
            expires_at, value = cached
            if now < expires_at:
                return value
        value = factory()
        self._store[key] = (now + self._ttl_seconds, value)
        return value
