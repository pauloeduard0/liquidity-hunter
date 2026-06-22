"""Point-in-time backtest of the leverage liquidation map.

Walks forward through historical candles. At each evaluation candle it
reconstructs the liquidity/order-block state *using only candles up to that
point* (no lookahead), projects the futures-independent liquidation levels
(`LeverageLiquidationEstimator.project_levels`), and measures over the
following `forward_horizon` candles whether price **reached** each still-live
level and, conditional on reaching, whether it **reacted** (swept and reversed
by `reaction_pct`).

The headline question is reaction *conditional on reach*, compared to a
distance-matched random-price baseline: whether a level is merely reached is
dominated by distance alone, so only a reaction edge over random levels at
comparable distances shows the anchoring (liquidity zones + order blocks) and
intensity carry signal. All outputs are descriptive measurements — no trade
signals, consistent with the project's research-only mandate.
"""

import random
import statistics
from dataclasses import dataclass

from liquidity_hunter.core.domain import Candle, LiquiditySide, TimeFrame
from liquidity_hunter.liquidity import (
    EqualHighDetector,
    EqualLowDetector,
    InternalStructureDetector,
    POIDetector,
    SwingHighDetector,
    SwingLowDetector,
    mark_swept_zones,
)
from liquidity_hunter.psychology import LeverageLiquidationEstimator, ProjectedLevel

DEFAULT_SWING_LOOKBACK = 10
DEFAULT_INTERNAL_SWING_LOOKBACK = 2

# Distance-from-current-price bucket edges (fractions), for distance-controlled
# model-vs-baseline comparison.
_DISTANCE_BUCKET_EDGES: tuple[float, ...] = (0.0, 0.01, 0.02, 0.04, 0.08, float("inf"))


@dataclass(frozen=True)
class DistanceBucket:
    """Model-vs-baseline reaction comparison within one distance band."""

    label: str
    model_reached: int
    model_reaction_rate: float
    baseline_reached: int
    baseline_reaction_rate: float
    lift: float  # model / baseline reaction rate (nan if baseline has no reaches)


@dataclass(frozen=True)
class LiquidationBacktestResult:
    """Aggregated point-in-time backtest metrics for the liquidation map."""

    symbol: str
    timeframe: TimeFrame
    n_eval_points: int
    n_levels: int
    n_reached: int
    n_reacted: int
    reach_rate: float
    reaction_rate: float  # reacted / reached
    baseline_reaction_rate: float
    lift: float  # overall model / baseline reaction rate
    median_bars_to_reach: float | None
    by_leverage: dict[int, tuple[float, float]]  # leverage -> (reach_rate, reaction_rate)
    by_distance_bucket: list[DistanceBucket]
    # quartile 1-4 -> (n_reached, reaction_rate)
    by_intensity_quartile: dict[int, tuple[int, float]]
    params: dict[str, float]


@dataclass
class _Outcome:
    """Per-level forward outcome at one evaluation point."""

    leverage: int
    distance: float
    base_weight: float
    reached: bool
    reacted: bool
    bars_to_reach: int | None


class LiquidationBacktester:
    """Backtests projected liquidation levels against forward price behavior."""

    def __init__(self) -> None:
        self._estimator = LeverageLiquidationEstimator()

    def run(
        self,
        candles: list[Candle],
        *,
        symbol: str = "BTCUSDT",
        timeframe: TimeFrame = TimeFrame.H1,
        forward_horizon: int = 72,
        reaction_pct: float = 0.01,
        reaction_window: int = 12,
        step: int = 6,
        min_history: int = 250,
        swing_lookback: int = DEFAULT_SWING_LOOKBACK,
        internal_swing_lookback: int = DEFAULT_INTERNAL_SWING_LOOKBACK,
        confluence_filter: bool = False,
        seed: int = 0,
    ) -> LiquidationBacktestResult:
        """Evaluate the liquidation map point-in-time over `candles`."""
        rng = random.Random(seed)
        params = {
            "forward_horizon": forward_horizon,
            "reaction_pct": reaction_pct,
            "reaction_window": reaction_window,
            "step": step,
            "min_history": min_history,
        }

        model: list[_Outcome] = []
        baseline: list[_Outcome] = []
        n_eval_points = 0

        last_start = len(candles) - forward_horizon
        for t in range(min_history, last_start, step):
            past = candles[: t + 1]
            future = candles[t + 1 : t + 1 + forward_horizon]
            if len(future) < forward_horizon:
                break
            current_price = past[-1].close
            if current_price <= 0:
                continue

            levels = self._live_levels(
                past, swing_lookback, internal_swing_lookback, confluence_filter
            )
            if not levels:
                continue
            n_eval_points += 1

            point_outcomes = [
                self._evaluate(level, current_price, future, reaction_pct, reaction_window)
                for level in levels
            ]
            model.extend(point_outcomes)
            baseline.extend(
                self._baseline_outcomes(
                    point_outcomes, current_price, future, reaction_pct, reaction_window, rng
                )
            )

        return self._aggregate(symbol, timeframe, n_eval_points, model, baseline, params)

    def _live_levels(
        self,
        past: list[Candle],
        swing_lookback: int,
        internal_swing_lookback: int,
        confluence_filter: bool,
    ) -> list[ProjectedLevel]:
        """Project levels from as-of-`past` state, keeping only unreached ones."""
        zones = mark_swept_zones(
            [
                *SwingHighDetector().detect(past),
                *SwingLowDetector().detect(past),
                *EqualHighDetector().detect(past),
                *EqualLowDetector().detect(past),
            ],
            past,
        )
        internal_events = InternalStructureDetector(
            swing_lookback=internal_swing_lookback, confluence_filter=confluence_filter
        ).detect(past)
        poi_zones = POIDetector().detect(past, internal_events).zones

        levels = self._estimator.project_levels(zones, poi_zones)
        # Keep only levels not already reached between their entry's formation
        # and now -- those are the still-live pools as of this evaluation point.
        live = []
        for level in levels:
            after = [c for c in past if c.timestamp >= level.start_time]
            if _reach_index(after, level.price, level.side) is None:
                live.append(level)
        return live

    @staticmethod
    def _evaluate(
        level: ProjectedLevel,
        current_price: float,
        future: list[Candle],
        reaction_pct: float,
        reaction_window: int,
    ) -> _Outcome:
        distance = abs(level.price - current_price) / current_price
        idx = _reach_index(future, level.price, level.side)
        reacted = idx is not None and _reacted(
            future, idx, level.price, level.side, reaction_pct, reaction_window
        )
        return _Outcome(
            leverage=level.leverage,
            distance=distance,
            base_weight=level.base_weight,
            reached=idx is not None,
            reacted=reacted,
            bars_to_reach=(idx + 1) if idx is not None else None,
        )

    @staticmethod
    def _baseline_outcomes(
        model_outcomes: list[_Outcome],
        current_price: float,
        future: list[Candle],
        reaction_pct: float,
        reaction_window: int,
        rng: random.Random,
    ) -> list[_Outcome]:
        """Distance-matched random-price control levels for the same point.

        Each control keeps a real level's side but takes a distance drawn from
        the point's real-level distance pool, placed at an arbitrary price
        (`current x (1 +- d)`), so reach is distance-comparable and any reaction
        gap reflects the anchoring rather than distance.
        """
        distances = [o.distance for o in model_outcomes]
        controls: list[_Outcome] = []
        for o in model_outcomes:
            d = rng.choice(distances)
            side = rng.choice((LiquiditySide.SELL_SIDE, LiquiditySide.BUY_SIDE))
            price = (
                current_price * (1 - d)
                if side is LiquiditySide.SELL_SIDE
                else current_price * (1 + d)
            )
            idx = _reach_index(future, price, side)
            reacted = idx is not None and _reacted(
                future, idx, price, side, reaction_pct, reaction_window
            )
            controls.append(
                _Outcome(
                    leverage=o.leverage,
                    distance=d,
                    base_weight=o.base_weight,
                    reached=idx is not None,
                    reacted=reacted,
                    bars_to_reach=(idx + 1) if idx is not None else None,
                )
            )
        return controls

    def _aggregate(
        self,
        symbol: str,
        timeframe: TimeFrame,
        n_eval_points: int,
        model: list[_Outcome],
        baseline: list[_Outcome],
        params: dict[str, float],
    ) -> LiquidationBacktestResult:
        n_levels = len(model)
        reached = [o for o in model if o.reached]
        n_reached = len(reached)
        n_reacted = sum(1 for o in reached if o.reacted)
        reach_rate = n_reached / n_levels if n_levels else 0.0
        reaction_rate = n_reacted / n_reached if n_reached else 0.0
        baseline_rate = _reaction_rate(baseline)
        median_btr = (
            statistics.median([o.bars_to_reach for o in reached if o.bars_to_reach is not None])
            if reached
            else None
        )

        by_leverage: dict[int, tuple[float, float]] = {}
        for lev in sorted({o.leverage for o in model}):
            group = [o for o in model if o.leverage == lev]
            by_leverage[lev] = (_reach_rate(group), _reaction_rate(group))

        return LiquidationBacktestResult(
            symbol=symbol,
            timeframe=timeframe,
            n_eval_points=n_eval_points,
            n_levels=n_levels,
            n_reached=n_reached,
            n_reacted=n_reacted,
            reach_rate=reach_rate,
            reaction_rate=reaction_rate,
            baseline_reaction_rate=baseline_rate,
            lift=(reaction_rate / baseline_rate) if baseline_rate > 0 else float("nan"),
            median_bars_to_reach=median_btr,
            by_leverage=by_leverage,
            by_distance_bucket=_distance_buckets(model, baseline),
            by_intensity_quartile=_intensity_quartiles(model),
            params=params,
        )


def _reach_index(candles: list[Candle], price: float, side: LiquiditySide) -> int | None:
    """First candle index whose wick crosses `price` (sell: low<=; buy: high>=)."""
    for i, candle in enumerate(candles):
        if side is LiquiditySide.SELL_SIDE and candle.low <= price:
            return i
        if side is LiquiditySide.BUY_SIDE and candle.high >= price:
            return i
    return None


def _reacted(
    candles: list[Candle],
    reach_index: int,
    price: float,
    side: LiquiditySide,
    reaction_pct: float,
    window: int,
) -> bool:
    """Whether price reversed off the level by `reaction_pct` within `window`."""
    segment = candles[reach_index : reach_index + window]
    if not segment:
        return False
    if side is LiquiditySide.SELL_SIDE:  # reached from above -> bounce up
        peak = max(c.high for c in segment)
        return (peak - price) / price >= reaction_pct
    trough = min(c.low for c in segment)  # buy side: reached from below -> drop
    return (price - trough) / price >= reaction_pct


def _reach_rate(outcomes: list[_Outcome]) -> float:
    return sum(1 for o in outcomes if o.reached) / len(outcomes) if outcomes else 0.0


def _reaction_rate(outcomes: list[_Outcome]) -> float:
    reached = [o for o in outcomes if o.reached]
    return sum(1 for o in reached if o.reacted) / len(reached) if reached else 0.0


def _distance_buckets(
    model: list[_Outcome], baseline: list[_Outcome]
) -> list[DistanceBucket]:
    buckets: list[DistanceBucket] = []
    edges = _DISTANCE_BUCKET_EDGES
    for lo, hi in zip(edges, edges[1:], strict=False):
        m = [o for o in model if lo <= o.distance < hi]
        b = [o for o in baseline if lo <= o.distance < hi]
        m_rate = _reaction_rate(m)
        b_rate = _reaction_rate(b)
        label = f"{lo * 100:.0f}-{hi * 100:.0f}%" if hi != float("inf") else f">{lo * 100:.0f}%"
        buckets.append(
            DistanceBucket(
                label=label,
                model_reached=sum(1 for o in m if o.reached),
                model_reaction_rate=m_rate,
                baseline_reached=sum(1 for o in b if o.reached),
                baseline_reaction_rate=b_rate,
                lift=(m_rate / b_rate) if b_rate > 0 else float("nan"),
            )
        )
    return buckets


def _intensity_quartiles(model: list[_Outcome]) -> dict[int, tuple[int, float]]:
    """Reaction rate per `base_weight` quartile (1 = weakest, 4 = strongest)."""
    weights = sorted(o.base_weight for o in model)
    if len(weights) < 4:
        return {}
    cuts = statistics.quantiles(weights, n=4)  # 3 cut points -> 4 groups

    def quartile(w: float) -> int:
        if w <= cuts[0]:
            return 1
        if w <= cuts[1]:
            return 2
        if w <= cuts[2]:
            return 3
        return 4

    result: dict[int, tuple[int, float]] = {}
    for q in (1, 2, 3, 4):
        group = [o for o in model if quartile(o.base_weight) == q]
        reached = [o for o in group if o.reached]
        result[q] = (len(reached), _reaction_rate(group))
    return result
