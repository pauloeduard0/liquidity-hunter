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
    INVALIDATED = "invalidated"


class POIZoneKind(str, Enum):
    """Which MSB-anchored zone a `POIZone` is.

    ORDER_BLOCK: last opposite-direction candle of the impulse-origin leg.
    BREAKER_BLOCK: last same-direction candle of the leg that formed the
      broken pivot, when the impulse-origin extreme swept the prior one
      (bullish: `l0 < l1`; bearish: `h0 > h1`).
    MITIGATION_BLOCK: the same zone when the prior extreme was not swept.
    """

    ORDER_BLOCK = "order_block"
    BREAKER_BLOCK = "breaker_block"
    MITIGATION_BLOCK = "mitigation_block"


class ConsolidationStatus(str, Enum):
    """Lifecycle state of a `ConsolidationRange`.

    ACTIVE: price is still trading inside the range at the end of the series.
    RESOLVED: price broke out of the range (sustained closes beyond a
      boundary) or a structure advance ended it.
    """

    ACTIVE = "active"
    RESOLVED = "resolved"


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


class MarketControlSide(str, Enum):
    """Which side is initiating the tape with conviction, from CVD × OI.

    Combines taker aggression (Cumulative Volume Delta slope) with open
    interest to read *who is in control right now*. Control is only asserted
    when fresh positions back the aggression (OI rising = new money); when OI
    is falling the aggression is position-closing (covering/liquidation), so
    no side is credited with conviction-backed control (``BALANCED``). An
    observation about participation, not a signal.
    """

    BUYERS = "buyers"  # buy aggression + OI rising: new longs, buyers in control
    SELLERS = "sellers"  # sell aggression + OI rising: new shorts, sellers in control
    BALANCED = "balanced"  # no conviction-backed control (flat, or aggression is unwinding)


class LiquidityHuntPhase(str, Enum):
    """Progress of a counter-trend liquidity hunt.

    When the current timeframe's structure turns against the higher-timeframe
    trend, the traders entering with that counter-move become the resting
    liquidity the larger trend feeds on. These phases describe how far the
    capture of the nearby opposing pools has progressed — an observation
    about liquidity, not a recommendation.
    """

    NONE = "none"  # structure aligned with the higher timeframe (or unknown)
    COUNTER_TREND = "counter_trend"  # counter-move active; opposing pools intact
    HUNT_IN_PROGRESS = "hunt_in_progress"  # pools being consumed / OI unwinding
    CAPTURED = "captured"  # mapped nearby pools consumed, OI no longer unwinding


class LiquidityHuntTargetKind(str, Enum):
    """What kind of resting-liquidity pool a hunt target is."""

    EQUAL_LEVEL = "equal_level"  # equal highs/lows zone (clustered stops)
    LIQUIDATION_BAND = "liquidation_band"  # projected leveraged-liquidation band


class HuntCaptureQuality(str, Enum):
    """Quality of a liquidity-hunt capture, from CVD-aggression x OI control.

    Cross-references the hunt's capture direction with the current
    ``MarketControlState``: was the grab backed by *fresh money* taking the
    capture side (a genuine break that also cleared liquidity), or by no new
    money — pure short-covering / stop-hunting on an exhausting move, which
    often precedes a reversal back the other way? An observation about the
    grab's fuel, not a signal.
    """

    UNKNOWN = "unknown"  # no market-control reading (spot / no OI coverage)
    GENUINE_BREAK = "genuine_break"  # new money backs the capture direction
    EXHAUSTION_GRAB = "exhaustion_grab"  # grab with no new money — reversal-prone


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


class ConfluenceFactor(str, Enum):
    """An independent observation that confirms a structural break.

    Each factor is a distinct evidence layer agreeing with a BOS/CHoCH's
    direction near the break — the basis for a descriptive *confluence* tally
    (how many orthogonal reads support the structure), not a signal.
    """

    HTF_ALIGNMENT = "htf_alignment"  # the break agrees with the higher-TF trend
    HTF_ORDER_BLOCK = "htf_order_block"  # the break reacted at a higher-TF OB
    VSA_VOLUME = "vsa_volume"  # a VSA volume-spread signal confirms the break
    ORDER_BLOCK = "order_block"  # the break launched from / reacted at an OB
    OI_PARTICIPATION = "oi_participation"  # new money entered the break (OI)
    VOLUME_DELTA = "volume_delta"  # net taker aggression aligned with the break
    LIQUIDITY_SWEEP = "liquidity_sweep"  # a stop-hunt preceded the break


class VSAPattern(str, Enum):
    """Volume-Spread-Analysis pattern read from a single candle's anatomy.

    Classic VSA ("effort vs result") patterns derived from the relationship
    between a candle's spread (high-low range), the position of its close
    within that range, its wick rejection, and its *raw* volume relative to
    recent candles.  Each is an *observation* about who is (or is not) present
    in the tape, not a trade recommendation.
    """

    NO_SUPPLY = "no_supply"  # narrow down-bar on low volume — sellers absent
    NO_DEMAND = "no_demand"  # narrow up-bar on low volume — buyers absent
    SELLING_CLIMAX = "selling_climax"  # wide down-bar, extreme volume, lower wick
    BUYING_CLIMAX = "buying_climax"  # wide up-bar, extreme volume, upper wick
    # Down thrust (video's bullish pin bar): lower-wick rejection, close high,
    # above-average volume — demand overwhelmed supply at the low. Bullish.
    DOWN_THRUST = "down_thrust"
    # Up thrust (classic VSA): upper-wick rejection, close low, above-average
    # volume — supply overwhelmed demand at the high. Bearish.
    UP_THRUST = "up_thrust"
