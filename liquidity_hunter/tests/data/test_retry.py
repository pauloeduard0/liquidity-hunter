"""Tests for the retry-with-backoff decorator."""

from unittest.mock import patch

import pytest

from liquidity_hunter.data.retry import retry_with_backoff


class _TransientError(Exception):
    pass


class _PermanentError(Exception):
    pass


@patch("liquidity_hunter.data.retry.time.sleep")
def test_succeeds_without_retry(mock_sleep) -> None:
    @retry_with_backoff(exceptions=(_TransientError,), max_attempts=3)
    def succeed() -> str:
        return "ok"

    assert succeed() == "ok"
    mock_sleep.assert_not_called()


@patch("liquidity_hunter.data.retry.time.sleep")
def test_retries_then_succeeds(mock_sleep) -> None:
    calls = {"count": 0}

    @retry_with_backoff(
        exceptions=(_TransientError,),
        max_attempts=3,
        base_delay_seconds=0.1,
        backoff_factor=2.0,
    )
    def flaky() -> str:
        calls["count"] += 1
        if calls["count"] < 3:
            raise _TransientError("temporary")
        return "ok"

    assert flaky() == "ok"
    assert calls["count"] == 3
    assert mock_sleep.call_count == 2
    mock_sleep.assert_any_call(0.1)
    mock_sleep.assert_any_call(0.2)


@patch("liquidity_hunter.data.retry.time.sleep")
def test_raises_after_max_attempts(mock_sleep) -> None:
    calls = {"count": 0}

    @retry_with_backoff(exceptions=(_TransientError,), max_attempts=3, base_delay_seconds=0.1)
    def always_fails() -> str:
        calls["count"] += 1
        raise _TransientError("permanent failure")

    with pytest.raises(_TransientError):
        always_fails()

    assert calls["count"] == 3
    assert mock_sleep.call_count == 2


@patch("liquidity_hunter.data.retry.time.sleep")
def test_does_not_catch_other_exceptions(mock_sleep) -> None:
    @retry_with_backoff(exceptions=(_TransientError,), max_attempts=3)
    def wrong_error() -> str:
        raise _PermanentError("not retryable")

    with pytest.raises(_PermanentError):
        wrong_error()

    mock_sleep.assert_not_called()
