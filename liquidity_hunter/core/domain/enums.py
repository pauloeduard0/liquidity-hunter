"""Enumerations shared across domain entities."""

from enum import Enum


class TimeFrame(str, Enum):
    """Candle aggregation period."""

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"


class MarketDirection(str, Enum):
    """Generic directional bias of structure or sentiment."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class LiquiditySide(str, Enum):
    """Which side of price a liquidity zone rests on."""

    BUY_SIDE = "buy_side"
    SELL_SIDE = "sell_side"


class LiquidityZoneType(str, Enum):
    """Classification of a detected liquidity zone."""

    EQUAL_HIGHS = "equal_highs"
    EQUAL_LOWS = "equal_lows"
    SWING_HIGH = "swing_high"
    SWING_LOW = "swing_low"
    ORDER_BLOCK = "order_block"
    FAIR_VALUE_GAP = "fair_value_gap"
    LIQUIDITY_POOL = "liquidity_pool"


class StructureEvent(str, Enum):
    """Discrete market structure observations."""

    HIGHER_HIGH = "higher_high"
    HIGHER_LOW = "higher_low"
    LOWER_HIGH = "lower_high"
    LOWER_LOW = "lower_low"
    BREAK_OF_STRUCTURE = "break_of_structure"
    CHANGE_OF_CHARACTER = "change_of_character"
    LIQUIDITY_SWEEP = "liquidity_sweep"


class BiasSource(str, Enum):
    """Origin of a retail psychology / sentiment observation."""

    COT_REPORT = "cot_report"
    RETAIL_POSITIONING = "retail_positioning"
    SOCIAL_SENTIMENT = "social_sentiment"
    SURVEY = "survey"
    OTHER = "other"


class RetailPositioning(str, Enum):
    """Estimated dominant position side held by retail market participants."""

    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"
