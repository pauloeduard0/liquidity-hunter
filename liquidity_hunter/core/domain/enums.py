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
    # A CHANGE_OF_CHARACTER that was invalidated before a confirming BOS: price
    # broke back through the CHoCH origin (the swing the CHoCH move launched
    # from), so the reversal failed and structure resumes in the prior
    # direction. `direction` is the direction of the CHoCH that failed.
    CHOCH_FAILED = "choch_failed"
    LIQUIDITY_SWEEP = "liquidity_sweep"


class StructureScope(str, Enum):
    """Whether a `MarketStructure` event is major (swing) or internal (minor) structure."""

    MAJOR = "major"
    INTERNAL = "internal"


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


class POIZoneStatus(str, Enum):
    """Lifecycle state of a POI (order block) zone."""

    ACTIVE = "active"
    MITIGATED = "mitigated"
    INVALIDATED = "invalidated"


class ManipulationPhase(str, Enum):
    """Current phase of an institutional manipulation cycle."""

    ACCUMULATION = "accumulation"
    MANIPULATION = "manipulation"
    EXPANSION = "expansion"


class ManipulationCycleStatus(str, Enum):
    """Resolution status of a manipulation cycle."""

    IN_PROGRESS = "in_progress"
    CONFIRMED = "confirmed"
    FAILED = "failed"


class DivergenceType(str, Enum):
    """Classification of a price/volume-delta behavioral divergence."""

    DISTRIBUTION = "distribution"
    ACCUMULATION = "accumulation"
    EXHAUSTION = "exhaustion"
    ABSORPTION = "absorption"


class OIRegime(str, Enum):
    """Joint price/open-interest regime over a recent window.

    The classic futures matrix: rising OI means *new* positions entering
    (conviction behind the move), falling OI means positions closing (the
    move is unwinding, not fresh money).
    """

    LONG_BUILDUP = "long_buildup"  # price up + OI up: new longs entering
    SHORT_COVERING = "short_covering"  # price up + OI down: shorts closing
    SHORT_BUILDUP = "short_buildup"  # price down + OI up: new shorts entering
    LONG_LIQUIDATION = "long_liquidation"  # price down + OI down: longs closing
    FLAT = "flat"  # no meaningful price or OI displacement


class OIParticipation(str, Enum):
    """Open-interest behavior around a structure event."""

    NEW_MONEY = "new_money"  # OI rising into the event: fresh positioning
    COVERING = "covering"  # OI falling: the move is position unwinding
    FLUSH = "flush"  # sharp OI drop on a sweep: leveraged positions liquidated
    FLAT = "flat"  # no meaningful OI change


class NarrativeEventType(str, Enum):
    """Classification of a narrative timeline event."""

    CONSOLIDATION = "consolidation"
    DISTRIBUTION = "distribution"
    ACCUMULATION = "accumulation"
    SWEEP = "sweep"
    EXPANSION = "expansion"
    EXHAUSTION = "exhaustion"
    ABSORPTION = "absorption"
    STRUCTURE_BREAK = "structure_break"
    ZONE_MITIGATION = "zone_mitigation"


class AnomalySeverity(str, Enum):
    """Severity of a narrative anomaly (pattern contradiction)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
