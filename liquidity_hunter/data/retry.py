"""Retry helpers for transient data-provider failures."""

import logging
import time
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

logger = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")


def retry_with_backoff(
    *,
    exceptions: tuple[type[Exception], ...],
    max_attempts: int = 3,
    base_delay_seconds: float = 1.0,
    backoff_factor: float = 2.0,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Retry a function with exponential backoff when it raises `exceptions`.

    The wrapped function is called up to `max_attempts` times. After each
    failed attempt (except the last) the thread sleeps for
    `base_delay_seconds * backoff_factor ** attempt_index` seconds.
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            delay = base_delay_seconds
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    if attempt == max_attempts:
                        logger.error(
                            "%s failed after %d attempt(s): %s",
                            func.__qualname__,
                            max_attempts,
                            exc,
                        )
                        raise
                    logger.warning(
                        "%s attempt %d/%d failed: %s. Retrying in %.1fs",
                        func.__qualname__,
                        attempt,
                        max_attempts,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                    delay *= backoff_factor
            raise AssertionError("unreachable")  # pragma: no cover

        return wrapper

    return decorator
