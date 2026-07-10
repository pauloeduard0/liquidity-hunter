"""Point-in-time backtest of the leverage liquidation map.

Walks forward through historical candles. At each evaluation candle it
reconstructs the liquidity/order-block state *using only candles up to that
point* (no lookahead), projects the futures-independent liquidation levels
(`LeverageLiquidationEstimator.project_levels`), keeps the **top-N strongest /
nearest** live levels (the ones the chart actually shows — a sparse, testable
prediction rather than a dense grid), and evaluates the following
`forward_horizon` candles.

These levels are treated as **targets / magnets** (price seeks the liquidity,
then may continue — not necessarily reverse), so the headline metric is
**clustering precision**: of the local price extremes that form forward (where
moves terminate), what fraction land *on* a model level (within `precision_eps`)
vs on distance-matched random-price levels. A secondary **magnet** signal is
time-to-reach (model vs baseline). Reach and reaction rates are kept as
sanity/legacy comparisons. All outputs are descriptive measurements — no trade
signals, consistent with the project's research-only mandate.
"""

import random
import statistics
from dataclasses import dataclass

from liquidity_hunter.core.domain import Candle, LiquiditySide, TimeFrame
from liquidity_hunter.liquidity import (
    EqualHighDetector,
    EqualLowDetector,
    POIDetector,
    SwingHighDetector,
    SwingLowDetector,
    mark_swept_zones,
)
from liquidity_hunter.psychology import LeverageLiquidationEstimator, ProjectedLevel

DEFAULT_SWING_LOOKBACK = 10

# Distance-from-current-price bucket edges (fractions), for the distance-controlled
# reaction comparison (legacy/secondary view).
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
    n_levels: int  # selected (top-N) levels evaluated across all points
    # --- target / magnet (headline) ---
    n_forward_extremes: int
    precision_model: float  # forward extremes landing on a model level (within eps)
    precision_baseline: float
    precision_lift: float  # model / baseline precision
    median_bars_to_reach_model: float | None
    median_bars_to_reach_baseline: float | None
    # --- reach + reaction (sanity + legacy comparison) ---
    reach_rate: float
    reaction_rate: float  # reacted / reached (model)
    baseline_reaction_rate: float
    reaction_lift: float
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
        max_levels: int = 12,
        price_window: float = 0.08,
        precision_eps: float = 0.0025,
        extreme_lookback: int = 3,
        reaction_pct: float = 0.01,
        reaction_window: int = 12,
        step: int = 6,
        min_history: int = 250,
        swing_lookback: int = DEFAULT_SWING_LOOKBACK,
        seed: int = 0,
    ) -> LiquidationBacktestResult:
        """Evaluate the liquidation map point-in-time over `candles`."""
        rng = random.Random(seed)
        params = {
            "forward_horizon": forward_horizon,
            "max_levels": max_levels,
            "price_window": price_window,
            "precision_eps": precision_eps,
            "extreme_lookback": extreme_lookback,
            "reaction_pct": reaction_pct,
            "reaction_window": reaction_window,
            "step": step,
            "min_history": min_history,
        }

        model: list[_Outcome] = []
        baseline: list[_Outcome] = []
        extremes_total = 0
        extremes_near_model = 0
        extremes_near_baseline = 0
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

            live = self._live_levels(past)
            selected = _select_levels(live, current_price, max_levels, price_window)
            if not selected:
                continue
            n_eval_points += 1

            model.extend(
                self._evaluate(level, current_price, future, reaction_pct, reaction_window)
                for level in selected
            )
            control_prices, control_outcomes = self._baseline(
                selected, current_price, future, reaction_pct, reaction_window, rng
            )
            baseline.extend(control_outcomes)

            # Clustering precision: do forward turning points land on the levels?
            model_prices = [lvl.price for lvl in selected]
            for price in _forward_extremes(future, extreme_lookback):
                extremes_total += 1
                if _near_any(price, model_prices, precision_eps):
                    extremes_near_model += 1
                if _near_any(price, control_prices, precision_eps):
                    extremes_near_baseline += 1

        return self._aggregate(
            symbol,
            timeframe,
            n_eval_points,
            model,
            baseline,
            extremes_total,
            extremes_near_model,
            extremes_near_baseline,
            params,
        )

    def _live_levels(self, past: list[Candle]) -> list[ProjectedLevel]:
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
        poi_zones = POIDetector().detect(past)

        levels = self._estimator.project_levels(zones, poi_zones)
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
    def _baseline(
        selected: list[ProjectedLevel],
        current_price: float,
        future: list[Candle],
        reaction_pct: float,
        reaction_window: int,
        rng: random.Random,
    ) -> tuple[list[float], list[_Outcome]]:
        """Distance-matched random-price control levels for the same point.

        Same count as the selected model levels, each at an arbitrary price
        `current x (1 +- d)` with `d` drawn from the point's real-level distance
        pool, so reach is distance-comparable and any precision/reaction gap
        reflects the anchoring rather than distance.
        """
        distances = [abs(lvl.price - current_price) / current_price for lvl in selected]
        prices: list[float] = []
        outcomes: list[_Outcome] = []
        for lvl in selected:
            d = rng.choice(distances)
            side = rng.choice((LiquiditySide.SELL_SIDE, LiquiditySide.BUY_SIDE))
            price = (
                current_price * (1 - d)
                if side is LiquiditySide.SELL_SIDE
                else current_price * (1 + d)
            )
            prices.append(price)
            idx = _reach_index(future, price, side)
            reacted = idx is not None and _reacted(
                future, idx, price, side, reaction_pct, reaction_window
            )
            outcomes.append(
                _Outcome(
                    leverage=lvl.leverage,
                    distance=d,
                    base_weight=lvl.base_weight,
                    reached=idx is not None,
                    reacted=reacted,
                    bars_to_reach=(idx + 1) if idx is not None else None,
                )
            )
        return prices, outcomes

    def _aggregate(
        self,
        symbol: str,
        timeframe: TimeFrame,
        n_eval_points: int,
        model: list[_Outcome],
        baseline: list[_Outcome],
        extremes_total: int,
        extremes_near_model: int,
        extremes_near_baseline: int,
        params: dict[str, float],
    ) -> LiquidationBacktestResult:
        n_levels = len(model)
        reached = [o for o in model if o.reached]
        n_reached = len(reached)
        reach_rate = n_reached / n_levels if n_levels else 0.0
        reaction_rate = _reaction_rate(model)
        baseline_rate = _reaction_rate(baseline)

        precision_model = extremes_near_model / extremes_total if extremes_total else 0.0
        precision_baseline = extremes_near_baseline / extremes_total if extremes_total else 0.0

        by_leverage: dict[int, tuple[float, float]] = {}
        for lev in sorted({o.leverage for o in model}):
            group = [o for o in model if o.leverage == lev]
            by_leverage[lev] = (_reach_rate(group), _reaction_rate(group))

        return LiquidationBacktestResult(
            symbol=symbol,
            timeframe=timeframe,
            n_eval_points=n_eval_points,
            n_levels=n_levels,
            n_forward_extremes=extremes_total,
            precision_model=precision_model,
            precision_baseline=precision_baseline,
            precision_lift=(precision_model / precision_baseline)
            if precision_baseline > 0
            else float("nan"),
            median_bars_to_reach_model=_median_bars(model),
            median_bars_to_reach_baseline=_median_bars(baseline),
            reach_rate=reach_rate,
            reaction_rate=reaction_rate,
            baseline_reaction_rate=baseline_rate,
            reaction_lift=(reaction_rate / baseline_rate) if baseline_rate > 0 else float("nan"),
            by_leverage=by_leverage,
            by_distance_bucket=_distance_buckets(model, baseline),
            by_intensity_quartile=_intensity_quartiles(model),
            params=params,
        )


def _select_levels(
    levels: list[ProjectedLevel], current_price: float, max_levels: int, price_window: float
) -> list[ProjectedLevel]:
    """Top-N live levels by proximity-weighted relevance, balanced both sides.

    Mirrors the chart's `selectVisibleLiquidationBands`: a sparse, actionable
    set near current price rather than the full dense grid.
    """
    in_window = [
        lvl for lvl in levels if abs(lvl.price - current_price) / current_price <= price_window
    ]
    if not in_window:
        return []
    max_weight = max(lvl.base_weight for lvl in in_window) or 1.0

    def relevance(lvl: ProjectedLevel) -> float:
        proximity = max(0.0, 1.0 - abs(lvl.price - current_price) / current_price / price_window)
        return 0.6 * proximity + 0.4 * (lvl.base_weight / max_weight)

    above = sorted(
        (lvl for lvl in in_window if lvl.price >= current_price), key=relevance, reverse=True
    )
    below = sorted(
        (lvl for lvl in in_window if lvl.price < current_price), key=relevance, reverse=True
    )
    selected: list[ProjectedLevel] = []
    i = 0
    while len(selected) < max_levels and (i < len(above) or i < len(below)):
        if i < len(below):
            selected.append(below[i])
        if len(selected) < max_levels and i < len(above):
            selected.append(above[i])
        i += 1
    return selected


def _forward_extremes(future: list[Candle], lookback: int) -> list[float]:
    """Prices of the local swing highs/lows in the forward window (move turning points)."""
    highs = SwingHighDetector(lookback=lookback).detect(future)
    lows = SwingLowDetector(lookback=lookback).detect(future)
    return [z.price_high for z in highs] + [z.price_low for z in lows]


def _near_any(price: float, level_prices: list[float], eps: float) -> bool:
    return any(abs(price - lp) / price <= eps for lp in level_prices)


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


def _median_bars(outcomes: list[_Outcome]) -> float | None:
    bars = [o.bars_to_reach for o in outcomes if o.bars_to_reach is not None]
    return statistics.median(bars) if bars else None


def _intensity_quartiles(model: list[_Outcome]) -> dict[int, tuple[int, float]]:
    """Reaction rate per `base_weight` quartile (1 = weakest, 4 = strongest)."""
    weights = sorted(o.base_weight for o in model)
    if len(weights) < 4:
        return {}
    cuts = statistics.quantiles(weights, n=4)

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


def _distance_buckets(model: list[_Outcome], baseline: list[_Outcome]) -> list[DistanceBucket]:
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
