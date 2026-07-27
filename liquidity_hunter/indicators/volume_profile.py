"""Volume-at-price: where a window's volume changed hands, not when.

Builds a `VolumeProfile` from a candle series by partitioning the window's
price range into equal-width buckets and attributing each candle's volume to
the buckets its high-low range overlaps, in proportion to how much of the
range falls in each.

See `core.domain.volume_profile` for the fidelity this achieves against a
trade-level (aggTrades) profile, and why the buy/sell split is the weaker half
of the reading.
"""

from collections.abc import Sequence

from liquidity_hunter.core.domain import (
    Candle,
    TimeFrame,
    VolumeNode,
    VolumeProfile,
    VolumeProfileBucket,
)

#: Buckets the window's price range is divided into, before the tick floor.
DEFAULT_BUCKET_COUNT = 100

#: Share of total volume the value area holds (the Market Profile convention).
DEFAULT_VALUE_AREA_PCT = 0.70

#: A bucket is a high-volume node at this multiple of the mean traded bucket,
#: a low-volume node at or below the other. Calibrated to mark roughly the
#: shelves and gaps a reader would point at, not every ripple.
DEFAULT_HVN_FACTOR = 1.5
DEFAULT_LVN_FACTOR = 0.35

#: Ceiling on bucket count, so a huge range over a tiny tick cannot explode.
_MAX_BUCKETS = 1000

#: Decimal places probed when inferring an instrument's tick from its prices.
_MAX_TICK_DECIMALS = 8


def infer_tick_size(candles: Sequence[Candle]) -> float:
    """Smallest price increment the series' prices are all multiples of.

    Exchange prices are exact multiples of the instrument's tick, so the
    finest decimal precision that appears across the window's OHLC values is
    that tick. Used as a floor on bucket width: a bucket narrower than the
    tick cannot be reached by every price inside it, which turns the profile
    into a comb of spikes separated by structurally empty bands (a real
    artifact — measured on NEARUSDT, whose ~0.036 two-hour range over 120
    buckets put every bucket below its 0.001 tick).
    """
    decimals = 0
    for candle in candles:
        for price in (candle.open, candle.high, candle.low, candle.close):
            for places in range(decimals, _MAX_TICK_DECIMALS + 1):
                if abs(round(price, places) - price) < 1e-9 * max(abs(price), 1.0):
                    decimals = places
                    break
            else:
                decimals = _MAX_TICK_DECIMALS
        if decimals == _MAX_TICK_DECIMALS:
            break
    return 10.0**-decimals


def volume_profile(
    candles: Sequence[Candle],
    *,
    symbol: str,
    timeframe: TimeFrame,
    bucket_count: int = DEFAULT_BUCKET_COUNT,
    value_area_pct: float = DEFAULT_VALUE_AREA_PCT,
    tick_size: float | None = None,
    hvn_factor: float = DEFAULT_HVN_FACTOR,
    lvn_factor: float = DEFAULT_LVN_FACTOR,
) -> VolumeProfile | None:
    """Volume-at-price over `candles`, or `None` if the window cannot form one.

    Returns `None` for an empty series or one with no price range at all (every
    candle printed at a single price), rather than raising: the composition
    root treats an unavailable profile the same way it treats a missing
    liquidation map.
    """
    if not candles:
        return None
    if bucket_count < 1:
        raise ValueError("bucket_count must be >= 1")
    if not 0.0 < value_area_pct <= 1.0:
        raise ValueError("value_area_pct must be in (0, 1]")

    price_low = min(c.low for c in candles)
    price_high = max(c.high for c in candles)
    if price_high <= price_low or price_low <= 0:
        return None

    tick = tick_size if tick_size is not None else infer_tick_size(candles)
    if tick <= 0:
        raise ValueError("tick_size must be > 0")

    span = price_high - price_low
    bucket_size = max(span / bucket_count, tick)
    count = min(max(int(round(span / bucket_size)), 1), _MAX_BUCKETS)
    bucket_size = span / count

    volumes = [0.0] * count
    buys = [0.0] * count

    def index_of(price: float) -> int:
        return min(max(int((price - price_low) / bucket_size), 0), count - 1)

    for candle in candles:
        buy_share = candle.taker_buy_volume / candle.volume if candle.volume > 0 else 0.0
        lo_i, hi_i = index_of(candle.low), index_of(candle.high)
        if lo_i == hi_i:
            volumes[lo_i] += candle.volume
            buys[lo_i] += candle.volume * buy_share
            continue
        # Spread the candle's volume across the buckets its range covers, each
        # taking the fraction of the range that falls inside it.
        candle_span = candle.high - candle.low
        for i in range(lo_i, hi_i + 1):
            band_low = price_low + i * bucket_size
            band_high = band_low + bucket_size
            covered = min(candle.high, band_high) - max(candle.low, band_low)
            if covered <= 0:
                continue
            share = covered / candle_span
            volumes[i] += candle.volume * share
            buys[i] += candle.volume * buy_share * share

    total_volume = sum(volumes)
    if total_volume <= 0:
        return None

    poc_index = max(range(count), key=lambda i: volumes[i])
    va_low_i, va_high_i = _value_area(volumes, poc_index, value_area_pct)

    traded = [v for v in volumes if v > 0]
    mean_traded = sum(traded) / len(traded) if traded else 0.0

    buckets: list[VolumeProfileBucket] = []
    for i, volume in enumerate(volumes):
        band_low = price_low + i * bucket_size
        if mean_traded > 0 and volume >= hvn_factor * mean_traded:
            node = VolumeNode.HIGH_VOLUME
        elif mean_traded > 0 and volume <= lvn_factor * mean_traded:
            node = VolumeNode.LOW_VOLUME
        else:
            node = VolumeNode.NORMAL
        buy = min(buys[i], volume)
        buckets.append(
            VolumeProfileBucket(
                price_low=band_low,
                price_high=band_low + bucket_size,
                volume=volume,
                buy_volume=buy,
                sell_volume=volume - buy,
                node=node,
                in_value_area=va_low_i <= i <= va_high_i,
                is_poc=i == poc_index,
            )
        )

    return VolumeProfile(
        symbol=symbol,
        timeframe=timeframe,
        start_timestamp=candles[0].timestamp,
        end_timestamp=candles[-1].timestamp,
        price_low=price_low,
        price_high=price_high,
        bucket_size=bucket_size,
        buckets=buckets,
        poc_price=price_low + (poc_index + 0.5) * bucket_size,
        value_area_low=price_low + va_low_i * bucket_size,
        value_area_high=price_low + (va_high_i + 1) * bucket_size,
        value_area_pct=value_area_pct,
        total_volume=total_volume,
        delta_estimated=True,
    )


def _value_area(volumes: Sequence[float], poc_index: int, target_pct: float) -> tuple[int, int]:
    """Grow outward from the POC until `target_pct` of volume is enclosed.

    At each step the side holding more volume is taken, the standard Market
    Profile expansion. Returns inclusive bucket indices.
    """
    total = sum(volumes)
    low_i = high_i = poc_index
    accumulated = volumes[poc_index]
    target = target_pct * total
    while accumulated < target and (low_i > 0 or high_i < len(volumes) - 1):
        below = volumes[low_i - 1] if low_i > 0 else -1.0
        above = volumes[high_i + 1] if high_i < len(volumes) - 1 else -1.0
        if above >= below:
            high_i += 1
            accumulated += volumes[high_i]
        else:
            low_i -= 1
            accumulated += volumes[low_i]
    return low_i, high_i
