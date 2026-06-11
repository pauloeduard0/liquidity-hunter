"""Rule-based retail crowd-psychology estimator.

`RetailTrapAnalyzer` estimates what retail traders are likely thinking and
doing by combining the higher timeframe trend, the most recent lower
timeframe market structure event, and nearby liquidity zones. It is a
*description* of likely crowd behavior -- not a price prediction or a
trading signal.

It implements `RetailBiasEstimator`, so it can be replaced by a
machine-learning-based estimator that consumes the same inputs and
returns the same `RetailBiasEstimate` shape.
"""

from datetime import UTC, datetime

from liquidity_hunter.core.domain import (
    LiquiditySide,
    LiquidityZone,
    MarketDirection,
    MarketStructure,
    RetailPositioning,
    StructureEvent,
)
from liquidity_hunter.psychology.analyzers.base import RetailBiasEstimator
from liquidity_hunter.psychology.models import RetailBiasEstimate

# Base confidence (0-100) contributed by the most recent structure event,
# reflecting how strongly that event type is likely to catch retail
# attention. A change of character looks like a fresh reversal and draws
# the most attention; HH/HL/LH/LL prints draw the least.
_EVENT_BASE_CONFIDENCE: dict[StructureEvent, float] = {
    StructureEvent.CHANGE_OF_CHARACTER: 60.0,
    StructureEvent.BREAK_OF_STRUCTURE: 50.0,
    StructureEvent.HIGHER_HIGH: 35.0,
    StructureEvent.HIGHER_LOW: 35.0,
    StructureEvent.LOWER_HIGH: 35.0,
    StructureEvent.LOWER_LOW: 35.0,
}
_NO_EVENT_CONFIDENCE = 20.0

# Added when the lower timeframe signal runs counter to the higher
# timeframe trend -- the classic setup where retail fades the dominant
# trend at the first sign of a reversal.
_COUNTER_TREND_BONUS = 20.0

# Maximum confidence contributed by a nearby liquidity zone that
# reinforces the narrative (e.g. equal lows just below price reinforcing a
# "perceived bottom"), scaled by the zone's `strength`.
_MAX_LIQUIDITY_BONUS = 20.0


def _reference_price(zone: LiquidityZone) -> float:
    return (zone.price_high + zone.price_low) / 2


class RetailTrapAnalyzer(RetailBiasEstimator):
    """Estimates retail crowd psychology from trend, structure, and liquidity."""

    def analyze(
        self,
        symbol: str,
        higher_timeframe_direction: MarketDirection,
        market_structure_events: list[MarketStructure],
        liquidity_zones: list[LiquidityZone],
        current_price: float,
    ) -> RetailBiasEstimate:
        if current_price <= 0:
            raise ValueError("current_price must be > 0")

        latest_event = self._latest_event(market_structure_events)
        local_direction = latest_event.direction if latest_event else MarketDirection.NEUTRAL
        dominant_side = self._dominant_side(local_direction, higher_timeframe_direction)
        is_counter_trend = self._is_counter_trend(local_direction, higher_timeframe_direction)
        supporting_zone = self._supporting_zone(dominant_side, liquidity_zones, current_price)

        confidence = self._confidence(latest_event, is_counter_trend, supporting_zone)
        explanation = self._explanation(
            dominant_side, latest_event, is_counter_trend, supporting_zone
        )
        generated_at = latest_event.timestamp if latest_event else datetime.now(UTC)

        return RetailBiasEstimate(
            symbol=symbol,
            generated_at=generated_at,
            dominant_side=dominant_side,
            confidence=confidence,
            explanation=explanation,
        )

    @staticmethod
    def _latest_event(events: list[MarketStructure]) -> MarketStructure | None:
        if not events:
            return None
        return max(events, key=lambda event: event.timestamp)

    @staticmethod
    def _dominant_side(
        local_direction: MarketDirection,
        higher_timeframe_direction: MarketDirection,
    ) -> RetailPositioning:
        direction = (
            local_direction
            if local_direction != MarketDirection.NEUTRAL
            else higher_timeframe_direction
        )
        if direction == MarketDirection.BULLISH:
            return RetailPositioning.LONG
        if direction == MarketDirection.BEARISH:
            return RetailPositioning.SHORT
        return RetailPositioning.NEUTRAL

    @staticmethod
    def _is_counter_trend(
        local_direction: MarketDirection,
        higher_timeframe_direction: MarketDirection,
    ) -> bool:
        return (
            local_direction != MarketDirection.NEUTRAL
            and higher_timeframe_direction != MarketDirection.NEUTRAL
            and local_direction != higher_timeframe_direction
        )

    @staticmethod
    def _supporting_zone(
        dominant_side: RetailPositioning,
        liquidity_zones: list[LiquidityZone],
        current_price: float,
    ) -> LiquidityZone | None:
        if dominant_side == RetailPositioning.LONG:
            target_side = LiquiditySide.SELL_SIDE
        elif dominant_side == RetailPositioning.SHORT:
            target_side = LiquiditySide.BUY_SIDE
        else:
            return None

        candidates = [zone for zone in liquidity_zones if zone.side == target_side]
        if not candidates:
            return None
        return min(candidates, key=lambda zone: abs(_reference_price(zone) - current_price))

    @staticmethod
    def _confidence(
        latest_event: MarketStructure | None,
        is_counter_trend: bool,
        supporting_zone: LiquidityZone | None,
    ) -> float:
        confidence = (
            _EVENT_BASE_CONFIDENCE.get(latest_event.event, _NO_EVENT_CONFIDENCE)
            if latest_event is not None
            else _NO_EVENT_CONFIDENCE
        )
        if is_counter_trend:
            confidence += _COUNTER_TREND_BONUS
        if supporting_zone is not None:
            confidence += supporting_zone.strength * _MAX_LIQUIDITY_BONUS
        return min(100.0, max(0.0, confidence))

    @staticmethod
    def _explanation(
        dominant_side: RetailPositioning,
        latest_event: MarketStructure | None,
        is_counter_trend: bool,
        supporting_zone: LiquidityZone | None,
    ) -> str:
        if dominant_side == RetailPositioning.NEUTRAL:
            return (
                "No clear directional signal from trend or recent structure; "
                "retail positioning is likely mixed or flat."
            )

        action = "buy" if dominant_side == RetailPositioning.LONG else "sell"
        perceived_level = "bottom" if dominant_side == RetailPositioning.LONG else "top"
        relation = "against" if is_counter_trend else "with"

        sentence = (
            f"Retail traders are likely attempting to {action} a perceived "
            f"{perceived_level} {relation} the higher timeframe trend"
        )

        if latest_event is not None:
            event_label = latest_event.event.value.replace("_", " ")
            sentence += f", following a {event_label} on the lower timeframe"

        if supporting_zone is not None:
            zone_label = supporting_zone.zone_type.value.replace("_", " ")
            reinforcement = "support" if dominant_side == RetailPositioning.LONG else "resistance"
            sentence += (
                f", reinforced by a nearby {zone_label} zone acting as perceived "
                f"{reinforcement}"
            )

        return sentence + "."
