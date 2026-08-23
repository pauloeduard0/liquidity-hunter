"""Exceptions raised by the data layer."""


class DataProviderError(Exception):
    """Base exception for all data provider failures."""


class DataProviderConnectionError(DataProviderError):
    """Raised when a data provider cannot be reached after exhausting retries."""


class DataProviderRequestError(DataProviderError):
    """Raised when a data provider rejects a request (e.g. invalid symbol/timeframe).

    Not retried, since retrying an invalid request would fail identically.
    """


class DataProviderBannedError(DataProviderError):
    """Raised when the venue has rate-limited or banned this IP (HTTP 418/429).

    Deliberately **not** retried, and deliberately fatal to a whole scan: a
    ban names an expiry, and every further request during it both fails and
    extends the offence. Retrying it is how a rate limit becomes a ban --
    measured the hard way, 2026-08-23, when a universe scan retried 284 jobs
    into a live ban. Back off, wait for the expiry, and reduce the request
    budget rather than trying harder.
    """
