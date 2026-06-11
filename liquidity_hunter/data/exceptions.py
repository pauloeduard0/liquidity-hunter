"""Exceptions raised by the data layer."""


class DataProviderError(Exception):
    """Base exception for all data provider failures."""


class DataProviderConnectionError(DataProviderError):
    """Raised when a data provider cannot be reached after exhausting retries."""


class DataProviderRequestError(DataProviderError):
    """Raised when a data provider rejects a request (e.g. invalid symbol/timeframe).

    Not retried, since retrying an invalid request would fail identically.
    """
