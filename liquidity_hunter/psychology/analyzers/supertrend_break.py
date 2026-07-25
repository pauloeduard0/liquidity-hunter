"""Qualification of Supertrend flips: fresh money, or a stop run.

The Supertrend band is a *public* level — mechanical, widely watched, and the
place where everyone who entered on the previous flip keeps their stop. So a
break of it is a liquidity event as much as a trend event, and the useful
question is not "did it flip?" but *who paid for the flip*.

This analyzer answers that by crossing the flip with the participation layers
the project already computes: the market-control reading (CVD aggression × open
interest) at the flip candle, the OI participation and VSA anatomy around it,
and whether structure confirmed the move with a break of its own. A flip that
broke the band, ran the stops on no new money and handed price back inside a
few candles later is the classic false break — named here as a ``STOP_RUN``.

Descriptive throughout: an observation about a break's fuel, never a signal.
"""

from collections.abc import Sequence
from datetime import datetime

from liquidity_hunter.core.domain import (
    Candle,
    MarketControlSide,
    MarketControlState,
    MarketDirection,
    MarketStructure,
    OIAnalysis,
    OIParticipation,
    StructureEvent,
    SupertrendBreak,
    SupertrendBreakQuality,
    SupertrendPoint,
    VolumeSpreadSignal,
    VSAPattern,
)

# How many candles after the flip price may close back inside the broken band
# and still count as a reclaim (the "they gave it back" window).
DEFAULT_RECLAIM_CANDLES = 5
# How many candles after the flip a same-direction break of structure may
# appear and still count as confirming it.
DEFAULT_CONFIRM_CANDLES = 10
# How far beyond the broken band price must travel before a give-back counts
# as a stop run, in mean-true-range units. Without it the reading is dominated
# by the indicator's own whipsaw (a flip that reverses next bar without leaving
# the level), and a label that fires on most flips discriminates nothing.
DEFAULT_MIN_EXCURSION_ATR = 1.0

_CONFIRMING_EVENTS = frozenset(
    {StructureEvent.BREAK_OF_STRUCTURE, StructureEvent.CHANGE_OF_CHARACTER}
)

# VSA patterns keyed by the *raided* side, the mirror of VSA's own implied
# direction: an upward flip pokes through a ceiling and rejects the high, a
# downward flip rejects the low.
_VSA_UPWARD_RAID: frozenset[VSAPattern] = frozenset(
    {VSAPattern.UP_THRUST, VSAPattern.BUYING_CLIMAX}
)
_VSA_DOWNWARD_RAID: frozenset[VSAPattern] = frozenset(
    {VSAPattern.DOWN_THRUST, VSAPattern.SELLING_CLIMAX}
)


def _mean_true_range(candles: Sequence[Candle]) -> float:
    """Mean true range of the series, the unit the excursion gate is measured in.

    Computed here rather than imported so the psychology layer keeps depending
    only on plain domain types.
    """
    total = 0.0
    for index, candle in enumerate(candles):
        spread = candle.high - candle.low
        if index == 0:
            total += spread
            continue
        prev_close = candles[index - 1].close
        total += max(
            spread, abs(candle.high - prev_close), abs(candle.low - prev_close)
        )
    return total / len(candles) if candles else 0.0


class SupertrendBreakAnalyzer:
    """Classifies each Supertrend flip as genuine, a stop run, or unqualified.

    Constructor parameters
    ----------------------
    reclaim_candles:
        Window in which a close back inside the broken band counts as a
        reclaim. Longer windows catch slower give-backs at the cost of calling
        an ordinary pullback a stop run.
    confirm_candles:
        Window in which a same-direction confirmed BOS/CHoCH counts as
        structure agreeing with the flip.
    min_excursion_atr:
        How far beyond the band price must travel before a give-back counts as
        a stop run, in mean-true-range units.
    """

    def __init__(
        self,
        *,
        reclaim_candles: int = DEFAULT_RECLAIM_CANDLES,
        confirm_candles: int = DEFAULT_CONFIRM_CANDLES,
        min_excursion_atr: float = DEFAULT_MIN_EXCURSION_ATR,
    ) -> None:
        if reclaim_candles <= 0 or confirm_candles <= 0:
            raise ValueError("reclaim_candles and confirm_candles must be positive")
        if min_excursion_atr < 0:
            raise ValueError("min_excursion_atr must not be negative")
        self._reclaim_candles = reclaim_candles
        self._confirm_candles = confirm_candles
        self._min_excursion_atr = min_excursion_atr

    def analyze(
        self,
        *,
        candles: Sequence[Candle],
        points: Sequence[SupertrendPoint],
        structure_events: Sequence[MarketStructure] = (),
        market_control: MarketControlState | None = None,
        oi_analysis: OIAnalysis | None = None,
        volume_spread_signals: Sequence[VolumeSpreadSignal] = (),
    ) -> list[SupertrendBreak]:
        """One :class:`SupertrendBreak` per flip in ``points``.

        Every input beyond ``candles``/``points`` is optional context: without
        it the verdict degrades to ``UNKNOWN`` rather than guessing, so a
        spot symbol (no open interest) still gets the flips listed.
        """
        if len(candles) < 2 or len(points) < 2:
            return []

        index_of = {candle.timestamp: i for i, candle in enumerate(candles)}
        min_excursion = self._min_excursion_atr * _mean_true_range(candles)
        breaks: list[SupertrendBreak] = []

        for position, point in enumerate(points):
            if not point.flip or position == 0:
                continue
            broken_level = points[position - 1].value
            if broken_level <= 0:
                continue
            candle_index = index_of.get(point.timestamp)
            if candle_index is None:
                continue

            reclaim_ts, reclaim_n = self._find_reclaim(
                candles, candle_index, point.direction, broken_level, min_excursion
            )
            controller = self._controller_at(market_control, point.timestamp)
            structure_confirmed = self._structure_confirmed(
                candles, candle_index, point.direction, structure_events
            )
            exhaustion = self._exhaustion_signature(
                candles,
                candle_index,
                point.direction,
                oi_analysis,
                volume_spread_signals,
            )

            quality, evidence = self._classify(
                direction=point.direction,
                controller=controller,
                structure_confirmed=structure_confirmed,
                reclaim_candles=reclaim_n,
                exhaustion=exhaustion,
            )
            breaks.append(
                SupertrendBreak(
                    timestamp=point.timestamp,
                    direction=point.direction,
                    broken_level=broken_level,
                    quality=quality,
                    reclaim_timestamp=reclaim_ts,
                    reclaim_candles=reclaim_n,
                    controller=controller,
                    structure_confirmed=structure_confirmed,
                    evidence=evidence,
                    description=self._describe(
                        point.direction, quality, broken_level, reclaim_n
                    ),
                )
            )
        return breaks

    # ------------------------------------------------------------------
    # Verdict
    # ------------------------------------------------------------------

    def _classify(
        self,
        *,
        direction: MarketDirection,
        controller: MarketControlSide | None,
        structure_confirmed: bool,
        reclaim_candles: int | None,
        exhaustion: str | None,
    ) -> tuple[SupertrendBreakQuality, list[str]]:
        """Name the break's fuel, or leave it unqualified.

        Fresh money on the flip's side *and* structure agreeing is a genuine
        break. A reclaim is the positive evidence a stop run needs — absence of
        new money alone is not enough to accuse a break (the same discipline
        that keeps ``HuntCaptureQuality`` precise), so a reclaim additionally
        requires either no credited controller behind the flip or an exhaustion
        fingerprint at it. Anything else stays ``UNKNOWN``, including a fresh
        flip whose reclaim window has not elapsed yet.
        """
        backing = (
            MarketControlSide.BUYERS
            if direction is MarketDirection.BULLISH
            else MarketControlSide.SELLERS
        )
        new_money = controller is backing
        evidence: list[str] = []
        if new_money:
            evidence.append("new-money")
        elif controller is not None:
            evidence.append("no-new-money")
        if structure_confirmed:
            evidence.append("bos")
        if exhaustion is not None:
            evidence.append(exhaustion)

        if new_money and structure_confirmed:
            return SupertrendBreakQuality.GENUINE, evidence
        if reclaim_candles is not None and (not new_money or exhaustion is not None):
            evidence.insert(0, f"reclaim:{reclaim_candles}c")
            return SupertrendBreakQuality.STOP_RUN, evidence
        if reclaim_candles is not None:
            evidence.insert(0, f"reclaim:{reclaim_candles}c")
        return SupertrendBreakQuality.UNKNOWN, evidence

    # ------------------------------------------------------------------
    # Components
    # ------------------------------------------------------------------

    def _find_reclaim(
        self,
        candles: Sequence[Candle],
        flip_index: int,
        direction: MarketDirection,
        broken_level: float,
        min_excursion: float,
    ) -> tuple[datetime | None, int | None]:
        """First close back inside the broken band, within the reclaim window.

        A wick back inside is not a reclaim: the break is only given back once
        a candle *closes* on the other side of the level again.

        The break must also have *gone somewhere* first — price has to travel
        ``min_excursion`` beyond the band before returning. Without that gate
        the reading is dominated by the indicator's own whipsaw: a flip that
        reverses on the very next bar without ever leaving the level never
        attracted anyone into the break, so calling it a trap says nothing.
        """
        last = min(flip_index + self._reclaim_candles, len(candles) - 1)
        bullish = direction is MarketDirection.BULLISH
        excursion = 0.0
        for index in range(flip_index, last + 1):
            candle = candles[index]
            reach = (
                candle.high - broken_level if bullish else broken_level - candle.low
            )
            excursion = max(excursion, reach)
            if index == flip_index:
                continue
            reclaimed = (
                candle.close < broken_level if bullish else candle.close > broken_level
            )
            if reclaimed:
                if excursion < min_excursion:
                    return None, None
                return candle.timestamp, index - flip_index
        return None, None

    @staticmethod
    def _controller_at(
        market_control: MarketControlState | None, timestamp: datetime
    ) -> MarketControlSide | None:
        """Credited side at (or last before) ``timestamp``; ``None`` uncovered."""
        if market_control is None or not market_control.series:
            return None
        controller: MarketControlSide | None = None
        for point in market_control.series:
            if point.timestamp <= timestamp:
                controller = point.controller
            else:
                break
        return controller

    def _structure_confirmed(
        self,
        candles: Sequence[Candle],
        flip_index: int,
        direction: MarketDirection,
        structure_events: Sequence[MarketStructure],
    ) -> bool:
        """A confirmed same-direction BOS/CHoCH within the confirmation window."""
        if not structure_events:
            return False
        start = candles[flip_index].timestamp
        end_index = min(flip_index + self._confirm_candles, len(candles) - 1)
        end = candles[end_index].timestamp
        return any(
            event.event in _CONFIRMING_EVENTS
            and event.direction is direction
            and not event.provisional
            and start <= event.timestamp <= end
            for event in structure_events
        )

    def _exhaustion_signature(
        self,
        candles: Sequence[Candle],
        flip_index: int,
        direction: MarketDirection,
        oi_analysis: OIAnalysis | None,
        volume_spread_signals: Sequence[VolumeSpreadSignal],
    ) -> str | None:
        """A capitulation fingerprint at the flip: OI flush or a VSA climax.

        Scanned over the flip candle ± one candle, since the participation
        layers sample on their own cadence and can land a bar either side of
        the break.
        """
        low = max(flip_index - 1, 0)
        high = min(flip_index + 1, len(candles) - 1)
        start = candles[low].timestamp
        end = candles[high].timestamp

        if oi_analysis is not None:
            for event in oi_analysis.qualified_events:
                if (
                    event.direction is direction
                    and event.participation is OIParticipation.FLUSH
                    and start <= event.event_timestamp <= end
                ):
                    return "oi-flush"

        patterns = (
            _VSA_UPWARD_RAID
            if direction is MarketDirection.BULLISH
            else _VSA_DOWNWARD_RAID
        )
        for signal in volume_spread_signals:
            if signal.pattern in patterns and start <= signal.timestamp <= end:
                return "vsa-climax"
        return None

    # ------------------------------------------------------------------
    # Description
    # ------------------------------------------------------------------

    @staticmethod
    def _describe(
        direction: MarketDirection,
        quality: SupertrendBreakQuality,
        broken_level: float,
        reclaim_candles: int | None,
    ) -> str:
        side = "up" if direction is MarketDirection.BULLISH else "down"
        level = f"{broken_level:g}"
        if quality is SupertrendBreakQuality.GENUINE:
            return (
                f"Supertrend flipped {side} through {level} with fresh money "
                "behind it, and structure broke the same way shortly after."
            )
        if quality is SupertrendBreakQuality.STOP_RUN:
            back = f" and price closed back inside {reclaim_candles} candles later"
            return (
                f"Supertrend flipped {side} through {level} without fresh money"
                f"{back} — the band's stops were taken, not a trend change."
            )
        return (
            f"Supertrend flipped {side} through {level}; participation data does "
            "not qualify the break either way."
        )
