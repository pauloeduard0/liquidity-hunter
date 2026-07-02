# Graph Report - .  (2026-07-02)

## Corpus Check
- 153 files · ~92,775 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1646 nodes · 4054 edges · 98 communities (87 shown, 11 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 177 edges (avg confidence: 0.58)
- Token cost: 169,341 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Narrative Engine|Narrative Engine]]
- [[_COMMUNITY_Dashboard Composition Root|Dashboard Composition Root]]
- [[_COMMUNITY_Behavior Divergence Analysis|Behavior Divergence Analysis]]
- [[_COMMUNITY_Internal Structure Detector|Internal Structure Detector]]
- [[_COMMUNITY_Swing Structure Detector|Swing Structure Detector]]
- [[_COMMUNITY_Manipulation Cycle Detection|Manipulation Cycle Detection]]
- [[_COMMUNITY_Candle Test Factories|Candle Test Factories]]
- [[_COMMUNITY_API TTL Cache|API TTL Cache]]
- [[_COMMUNITY_Domain Enums & Base Model|Domain Enums & Base Model]]
- [[_COMMUNITY_API Dashboard Schema|API Dashboard Schema]]
- [[_COMMUNITY_Detector Shared Helpers|Detector Shared Helpers]]
- [[_COMMUNITY_Retail Trap Psychology|Retail Trap Psychology]]
- [[_COMMUNITY_Equal Level Detectors|Equal Level Detectors]]
- [[_COMMUNITY_Futures Domain Models|Futures Domain Models]]
- [[_COMMUNITY_Main Chart Frontend|Main Chart Frontend]]
- [[_COMMUNITY_Zone Test Factories|Zone Test Factories]]
- [[_COMMUNITY_Frontend Package Config|Frontend Package Config]]
- [[_COMMUNITY_KPI Row & Dashboard Types|KPI Row & Dashboard Types]]
- [[_COMMUNITY_Data Provider Ports|Data Provider Ports]]
- [[_COMMUNITY_Leverage Liquidation Estimator|Leverage Liquidation Estimator]]
- [[_COMMUNITY_Narrative Domain Model|Narrative Domain Model]]
- [[_COMMUNITY_Binance Futures Provider|Binance Futures Provider]]
- [[_COMMUNITY_Heatmap Tests|Heatmap Tests]]
- [[_COMMUNITY_Binance Spot Provider|Binance Spot Provider]]
- [[_COMMUNITY_Candle & Zone Entities|Candle & Zone Entities]]
- [[_COMMUNITY_Zone Sweep Mitigation|Zone Sweep Mitigation]]
- [[_COMMUNITY_Liquidity Heatmap Engine|Liquidity Heatmap Engine]]
- [[_COMMUNITY_Liquidity Scoring Engine|Liquidity Scoring Engine]]
- [[_COMMUNITY_Break Detection Helpers|Break Detection Helpers]]
- [[_COMMUNITY_TypeScript Compiler Config|TypeScript Compiler Config]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]
- [[_COMMUNITY_Community 59|Community 59]]
- [[_COMMUNITY_Community 60|Community 60]]
- [[_COMMUNITY_Community 61|Community 61]]
- [[_COMMUNITY_Community 62|Community 62]]
- [[_COMMUNITY_Community 63|Community 63]]
- [[_COMMUNITY_Community 64|Community 64]]
- [[_COMMUNITY_Community 65|Community 65]]
- [[_COMMUNITY_Community 66|Community 66]]
- [[_COMMUNITY_Community 67|Community 67]]
- [[_COMMUNITY_Community 68|Community 68]]
- [[_COMMUNITY_Community 69|Community 69]]
- [[_COMMUNITY_Community 70|Community 70]]
- [[_COMMUNITY_Community 71|Community 71]]
- [[_COMMUNITY_Community 72|Community 72]]
- [[_COMMUNITY_Community 73|Community 73]]
- [[_COMMUNITY_Community 74|Community 74]]
- [[_COMMUNITY_Community 75|Community 75]]
- [[_COMMUNITY_Community 76|Community 76]]
- [[_COMMUNITY_Community 77|Community 77]]
- [[_COMMUNITY_Community 78|Community 78]]
- [[_COMMUNITY_Community 79|Community 79]]
- [[_COMMUNITY_Community 80|Community 80]]
- [[_COMMUNITY_Community 81|Community 81]]
- [[_COMMUNITY_Community 97|Community 97]]

## God Nodes (most connected - your core abstractions)
1. `make_series()` - 100 edges
2. `NarrativeEngine` - 92 edges
3. `InternalStructureDetector` - 89 edges
4. `make_candle()` - 83 edges
5. `TimeFrame` - 75 edges
6. `Candle` - 68 edges
7. `MarketStructure` - 53 edges
8. `_minimal_data()` - 49 edges
9. `load_dashboard_data()` - 48 edges
10. `SwingStructureDetector` - 45 edges

## Surprising Connections (you probably didn't know these)
- `GET /api/dashboard route` --calls--> `load_dashboard_data()`  [EXTRACTED]
  CLAUDE.md → liquidity_hunter/app/dashboard_data.py
- `DomainModel` --references--> `pydantic`  [EXTRACTED]
  liquidity_hunter/core/domain/base.py → requirements.txt
- `LIQUIDITY_SWEEP` --semantically_similar_to--> `RTOSweepEvent`  [INFERRED] [semantically similar]
  liquidity_hunter/docs/estrutura_bos_choch.md → liquidity_hunter/core/domain/poi_zone.py
- `BinanceDataProvider` --references--> `ccxt`  [EXTRACTED]
  liquidity_hunter/data/providers/binance.py → requirements.txt
- `InternalStructureDetector` --calls--> `collect_pivots`  [EXTRACTED]
  liquidity_hunter/liquidity/detectors/internal_structure.py → CLAUDE.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **LiquidityZoneDetector port implementations** — liquidity_hunter_liquidity_detectors_base_liquidityzonedetector, liquidity_hunter_liquidity_detectors_swing_points_swinghighdetector, liquidity_hunter_liquidity_detectors_swing_points_swinglowdetector, liquidity_hunter_liquidity_detectors_equal_levels_equalhighdetector, liquidity_hunter_liquidity_detectors_equal_levels_equallowdetector [EXTRACTED 1.00]
- **load_dashboard_data composition pipeline** — liquidity_hunter_app_dashboard_data_load_dashboard_data, liquidity_hunter_liquidity_detectors_market_structure_swingstructuredetector, liquidity_hunter_liquidity_detectors_internal_structure_internalstructuredetector, liquidity_hunter_liquidity_detectors_poi_poidetector, liquidity_hunter_scoring_engine_liquidityscoringengine, liquidity_hunter_psychology_analyzers_manipulation_cycle_manipulationcycledetector, liquidity_hunter_psychology_analyzers_behavior_divergence_behaviordivergenceanalyzer, liquidity_hunter_psychology_analyzers_leverage_liquidation_leverageliquidationestimator, liquidity_hunter_app_narrative_narrativeengine [EXTRACTED 1.00]
- **BOS three-filter confirmation chain (close-break, staircase, pullback, confluence)** — liquidity_hunter_docs_estrutura_bos_choch_break_of_structure, liquidity_hunter_liquidity_detectors__common_find_close_break_index, liquidity_hunter_docs_estrutura_bos_choch_bos_staircase, liquidity_hunter_docs_estrutura_bos_choch_pullback_confirmation, liquidity_hunter_liquidity_detectors__common_bos_confluence [EXTRACTED 1.00]

## Communities (98 total, 11 thin omitted)

### Community 0 - "Narrative Engine"
Cohesion: 0.06
Nodes (77): DashboardData, A snapshot of research data for a single symbol/timeframe., NarrativeEngine, Candle, DashboardData, datetime, ManipulationCycle, MarketDirection (+69 more)

### Community 1 - "Dashboard Composition Root"
Cohesion: 0.05
Nodes (59): gt, le, get_dashboard(), TimeFrame, Return a `DashboardData` snapshot for `symbol`/`timeframe` as JSON.      Results, _drop_pre_break_reference_bos, _reanchor_bos_close_break, _build_liquidation_map() (+51 more)

### Community 2 - "Behavior Divergence Analysis"
Cohesion: 0.08
Nodes (34): BehaviorDivergence, DivergenceType, BehaviorDivergenceAnalyzer, _deduplicate(), _describe_zone_divergence(), Candle, LiquidityZone, MarketDirection (+26 more)

### Community 3 - "Internal Structure Detector"
Cohesion: 0.06
Nodes (53): LuxAlgo Smart Money Concepts indicator, Trailing active_high/active_low references, bos_confluence, find_sustained_break_index, InternalStructureDetector, Detects internal BOS/CHoCH/HL/LH from trailing swing pivot references.      Swin, _load_window_candles(), Tests for `InternalStructureDetector`. (+45 more)

### Community 4 - "Swing Structure Detector"
Cohesion: 0.06
Nodes (53): collect_pivots, find_close_break_index, find_wick_break_index, is_sustained_break, Detects BOS/CHoCH and LH/HL from major (swing) pivots.      Swing highs/lows are, SwingStructureDetector, _confirmed_main_series(), Candle (+45 more)

### Community 5 - "Manipulation Cycle Detection"
Cohesion: 0.09
Nodes (31): ManipulationCycleDetector, Candle, datetime, LiquiditySide, LiquidityZone, ManipulationCycle, MarketDirection, MarketStructure (+23 more)

### Community 6 - "Candle Test Factories"
Cohesion: 0.06
Nodes (47): make_candle(), make_series(), Candle, TimeFrame, Test helpers for building candle series with known swing points., Build a valid `Candle` with the given high/low and a midpoint open.      `close`, Build a chronological candle series from parallel high/low lists., With the flag unset the detector is byte-for-byte unchanged. (+39 more)

### Community 7 - "API TTL Cache"
Cohesion: 0.07
Nodes (29): Hashable, T, A minimal in-memory TTL cache, used to avoid redundant Binance requests., A time-based cache keyed by arbitrary hashable keys., Return the cached value for `key`, computing it via `factory` if missing/expired, TTLCache, FastAPI application entrypoint for the `api` layer.  Run locally with:      poet, GET /api/dashboard route (+21 more)

### Community 8 - "Domain Enums & Base Model"
Cohesion: 0.12
Nodes (28): Enum, Shared base class for all domain entities., BiasSource, DivergenceType, LiquiditySide, LiquidityZoneType, ManipulationCycleStatus, Enumerations shared across domain entities. (+20 more)

### Community 9 - "API Dashboard Schema"
Cohesion: 0.11
Nodes (29): Dashboard data endpoint., DashboardDataResponse, Pydantic response models for the `api` layer.  `DashboardData` is a plain `datac, JSON representation of `app.dashboard_data.DashboardData`., Composition root for the research dashboard.  Wires together `data`, `liquidity`, Behavioral divergence between price action and institutional flow., MarketDirection, POIZoneStatus (+21 more)

### Community 10 - "Detector Shared Helpers"
Cohesion: 0.11
Nodes (28): bos_confluence(), collect_pivots(), find_close_break_index(), find_fvg(), find_sustained_break_index(), Pivot, Candle, datetime (+20 more)

### Community 11 - "Retail Trap Psychology"
Cohesion: 0.10
Nodes (27): Wyckoff/SMC accumulation-manipulation-expansion pattern, Retail trap setup, LiquidityZone, MarketDirection, MarketStructure, RetailBiasEstimate, RetailPositioning, Estimates retail crowd psychology from trend, structure, and liquidity. (+19 more)

### Community 12 - "Equal Level Detectors"
Cohesion: 0.12
Nodes (24): Project levels from as-of-`past` state, keeping only unreached ones., LiquidityZoneDetector, MarketStructureDetector, Shared interfaces for liquidity zone and market structure detectors., Detects `MarketStructure` events from a series of candles., Detects `LiquidityZone` instances from a series of candles., EqualHighDetector, _EqualLevelDetector (+16 more)

### Community 13 - "Futures Domain Models"
Cohesion: 0.10
Nodes (24): FundingRate, LongShortRatio, OpenInterestPoint, A single open-interest sample for a perpetual contract.      ``open_interest`` i, A funding-rate sample for a perpetual contract.      ``funding_rate`` is the sig, A crowd long/short account-ratio sample for a perpetual contract.      ``long_ac, DataProviderConnectionError, Raised when a data provider cannot be reached after exhausting retries. (+16 more)

### Community 14 - "Main Chart Frontend"
Cohesion: 0.10
Nodes (26): LiquidationBandInput, ResolvedBand, balancedTake(), buildManipulationBoxes(), detectDivergences(), Divergence, DIVERGENCE_MARKER_SHAPES, failedChochTime() (+18 more)

### Community 15 - "Zone Test Factories"
Cohesion: 0.15
Nodes (30): make_zone(), LiquiditySide, LiquidityZone, LiquidityZoneType, Build a `LiquidityZone` at `price` (or `[price_low, price]` if given)., _candle(), _estimate(), _funding() (+22 more)

### Community 16 - "Frontend Package Config"
Cohesion: 0.07
Nodes (28): dependencies, lightweight-charts, react, react-dom, devDependencies, eslint, @eslint/js, eslint-plugin-react-hooks (+20 more)

### Community 17 - "KPI Row & Dashboard Types"
Cohesion: 0.07
Nodes (26): BIAS_CONFIG, DIRECTION_CONFIG, KpiCardProps, KpiRow(), KpiRowProps, AnomalySeverity, Candle, DivergenceType (+18 more)

### Community 18 - "Data Provider Ports"
Cohesion: 0.15
Nodes (21): ABC, Data layer: market data acquisition, repositories, and persistence adapters.  Re, FuturesDataProvider, OHLCVProvider, Abstract interfaces (ports) for market data providers., A source of historical OHLCV candle data.      Concrete implementations are resp, A source of perpetual-futures market-state data.      Concrete implementations t, FallbackOHLCVProvider (+13 more)

### Community 19 - "Leverage Liquidation Estimator"
Cohesion: 0.10
Nodes (25): _bucket_select(), _clamp(), _Entry, _entry_anchors(), _liquidation_hit_time(), _open_interest_change_pct(), _positioning_score(), Candle (+17 more)

### Community 20 - "Narrative Domain Model"
Cohesion: 0.16
Nodes (24): Narrative engine: synthesizes all detection layers into a coherent story.  Lives, AnomalySeverity, ManipulationPhase, NarrativeEventType, Classification of a narrative timeline event., Severity of a narrative anomaly (pattern contradiction)., Current phase of an institutional manipulation cycle., MarketNarrative (+16 more)

### Community 21 - "Binance Futures Provider"
Cohesion: 0.13
Nodes (18): DataProviderError, DataProviderRequestError, Exceptions raised by the data layer., Raised when a data provider rejects a request (e.g. invalid symbol/timeframe)., Base exception for all data provider failures., BinanceFuturesOHLCVProvider, Any, Candle (+10 more)

### Community 22 - "Heatmap Tests"
Cohesion: 0.20
Nodes (23): HeatmapBucket, _build(), _engine(), _hot_bucket(), _make_cycle(), _make_poi(), LiquidityHeatmap, ManipulationCycle (+15 more)

### Community 23 - "Binance Spot Provider"
Cohesion: 0.14
Nodes (19): BinanceDataProvider, klines_row_to_candle(), Any, Candle, Exchange, TimeFrame, Binance OHLCV data provider backed by CCXT., Convert a concatenated symbol (e.g. "BTCUSDT") to CCXT's unified form ("BTC/USDT (+11 more)

### Community 24 - "Candle & Zone Entities"
Cohesion: 0.11
Nodes (18): Candle, Self, A single OHLCV price bar for a symbol and timeframe., LiquidityZone, Self, A price region identified as holding resting liquidity., A measurement of retail market participant sentiment or positioning., RetailBias (+10 more)

### Community 25 - "Zone Sweep Mitigation"
Cohesion: 0.19
Nodes (12): _check_zone(), mark_swept_zones(), Candle, LiquidityZone, Post-detection sweep check for liquidity zones.  Scans candles after each zone's, Return a new list with swept zones marked as mitigated., _eqh(), _eql() (+4 more)

### Community 26 - "Liquidity Heatmap Engine"
Cohesion: 0.10
Nodes (20): _bucket_side(), _gaussian_smooth(), LiquidityHeatmapEngine, _overlapping_buckets(), Candle, LiquidityHeatmap, LiquiditySide, LiquidityZone (+12 more)

### Community 27 - "Liquidity Scoring Engine"
Cohesion: 0.16
Nodes (20): Liquidity target scoring methodology, LiquidityScoringEngine, TimeFrame, Ranks `LiquidityZone` objects by their relevance as liquidity targets.      The, DEFAULT_TIMEFRAME_WEIGHTS, make_zone(), LiquiditySide, LiquidityZone (+12 more)

### Community 28 - "Break Detection Helpers"
Cohesion: 0.21
Nodes (19): find_wick_break_index(), is_sustained_break(), Whether the break of `active_price` at `candles[pivot_index]` holds.      True i, The first index in `candles[start_index:end_index + 1]` whose wick     crosses `, _candles(), _candles_hl(), Candle, Tests for `liquidity_hunter.liquidity.detectors._common`. (+11 more)

### Community 29 - "TypeScript Compiler Config"
Cohesion: 0.11
Nodes (18): compilerOptions, allowImportingTsExtensions, erasableSyntaxOnly, jsx, lib, module, moduleDetection, moduleResolution (+10 more)

### Community 30 - "Community 30"
Cohesion: 0.19
Nodes (18): LiquidationBacktester, _near_any(), Backtests projected liquidation levels against forward price behavior., _candles(), Candle, Tests for `liquidity_hunter.app.liquidation_backtest`., test_near_any_within_eps(), test_reach_index_buy_side_hits_on_high_cross() (+10 more)

### Community 31 - "Community 31"
Cohesion: 0.19
Nodes (12): POIDetector, Candle, datetime, MarketStructure, POIZone, RTOSweepEvent, TimeFrame, Detects institutional order block zones from structure events + candles.      Pa (+4 more)

### Community 32 - "Community 32"
Cohesion: 0.18
Nodes (14): CaptureFixture, _fmt(), main(), _print_report(), Example: backtest the leverage liquidation map point-in-time.  Reconstructs liqu, Fetch BTCUSDT candles, run the point-in-time liquidation backtest, report., LiquidationBacktestResult, Aggregated point-in-time backtest metrics for the liquidation map. (+6 more)

### Community 33 - "Community 33"
Cohesion: 0.11
Nodes (17): compilerOptions, allowImportingTsExtensions, erasableSyntaxOnly, lib, module, moduleDetection, moduleResolution, noEmit (+9 more)

### Community 34 - "Community 34"
Cohesion: 0.21
Nodes (8): BinanceFuturesDataProvider, Any, Exchange, T, TimeFrame, Fetches perpetual-futures market state from Binance USDT-M via CCXT., The CCXT unified swap symbol (e.g. "BTC/USDT:USDT") for `symbol`., Run `fetch` with retry/backoff and translate ccxt errors.

### Community 35 - "Community 35"
Cohesion: 0.22
Nodes (14): Exception, Exception, T, Retry helpers for transient data-provider failures., Retry a function with exponential backoff when it raises `exceptions`.      The, retry_with_backoff(), _PermanentError, Tests for the retry-with-backoff decorator. (+6 more)

### Community 36 - "Community 36"
Cohesion: 0.17
Nodes (9): DashboardQuery, fetchDashboardData(), TIMEFRAME_OPTIONS, Logo(), MainChart(), MainChartProps, ManipulationCyclesPanel(), DashboardData (+1 more)

### Community 37 - "Community 37"
Cohesion: 0.13
Nodes (7): gradientRgb(), HeatmapBand, HeatmapStripPaneView, HeatmapStripPrimitive, HeatmapStripRenderer, ResolvedBand, HEATMAP_GRADIENT

### Community 38 - "Community 38"
Cohesion: 0.17
Nodes (12): detect_zones(), main(), Candle, LiquidityZone, Example: detect liquidity zones in BTCUSDT 1h candles.  Run with:      poetry ru, Run all liquidity detectors over `candles`., Fetch BTCUSDT 1h candles, detect liquidity zones, and print a summary., _FakeProvider (+4 more)

### Community 39 - "Community 39"
Cohesion: 0.25
Nodes (14): _distance_buckets(), DistanceBucket, _intensity_quartiles(), _median_bars(), _Outcome, TimeFrame, Point-in-time backtest of the leverage liquidation map.  Walks forward through h, Reaction rate per `base_weight` quartile (1 = weakest, 4 = strongest). (+6 more)

### Community 40 - "Community 40"
Cohesion: 0.17
Nodes (10): BaseModel, DomainModel, Base class for immutable, strictly-validated domain entities.      - `frozen=Tru, Candle domain entity., Perpetual-futures market-state domain entities.  These describe *observations* a, Abstract port for retail crowd-psychology estimation., Rule-based retail crowd-psychology estimator.  `RetailTrapAnalyzer` estimates wh, Output models for the psychology layer. (+2 more)

### Community 41 - "Community 41"
Cohesion: 0.13
Nodes (5): LineLabel, LineLabelsPaneView, LineLabelsPrimitive, LineLabelsRenderer, PositionedLabel

### Community 42 - "Community 42"
Cohesion: 0.22
Nodes (11): main(), Candle, Example: fetch 500 BTCUSDT 1h candles from Binance and print the first five.  Ru, Fetch candles and print the first five. Returns the full list., _FakeProvider, _make_candles(), Candle, TimeFrame (+3 more)

### Community 43 - "Community 43"
Cohesion: 0.17
Nodes (10): _forward_extremes(), Prices of the local swing highs/lows in the forward window (move turning points), Detects swing highs: local maxima of `Candle.high`.      Swing highs mark restin, SwingHighDetector, Tests for `SwingHighDetector` and `SwingLowDetector`., test_swing_detector_rejects_empty_candles(), test_swing_detector_rejects_invalid_lookback(), test_swing_detector_returns_empty_for_short_series() (+2 more)

### Community 44 - "Community 44"
Cohesion: 0.23
Nodes (12): Indicators layer: derived numerical series computed from `Candle` data.  Houses, Candle, Volume delta: per-candle taker buy/sell aggression imbalance., `volume_delta` for each candle in `candles`, in the same order., Net taker aggression for `candle`.      `2 * taker_buy_volume - volume` is posit, volume_delta(), volume_delta_series(), Tests for `liquidity_hunter.indicators.volume_delta`. (+4 more)

### Community 45 - "Community 45"
Cohesion: 0.15
Nodes (6): price_range(), The full high/low range spanned by `candles`, used to normalize strength scores., Candle, LiquidityZone, Base class for fractal-style swing point detection., _SwingPointDetector

### Community 46 - "Community 46"
Cohesion: 0.14
Nodes (5): POIBox, POIBoxesPaneView, POIBoxesPrimitive, POIBoxesRenderer, ResolvedBox

### Community 47 - "Community 47"
Cohesion: 0.18
Nodes (10): CollapsibleSection(), CollapsibleSectionProps, CycleCard(), formatPrice(), formatTimestamp(), ManipulationCyclesPanelProps, PHASE_STYLES, STATUS_STYLES (+2 more)

### Community 48 - "Community 48"
Cohesion: 0.26
Nodes (12): Counter, _load_fixture_candles(), main(), _print_comparison(), Candle, MarketStructure, StructureEvent, Diagnostic: compare `InternalStructureDetector` re-anchor modes.  Runs the inter (+4 more)

### Community 49 - "Community 49"
Cohesion: 0.18
Nodes (11): AnomalyCallout(), EVENT_TYPE_STYLES, formatTimestamp(), NarrativePanel(), NarrativePanelProps, PHASE_LABELS, SEVERITY_STYLES, TimelineEvent() (+3 more)

### Community 50 - "Community 50"
Cohesion: 0.21
Nodes (9): main(), ScoredLiquidityZone, Example: score and rank BTCUSDT liquidity zones by relevance.  Run with:      po, Fetch BTCUSDT 1h candles, score detected liquidity zones, and print them ranked., _FakeProvider, Candle, TimeFrame, Tests for the BTCUSDT liquidity scoring example script. (+1 more)

### Community 51 - "Community 51"
Cohesion: 0.15
Nodes (13): _leg_origin_series(), _persistence_test_series(), Candle, With `bos_leg_origin_choch_ref`, the confirmed BOS promotes the low its     leg, Same series with the flag off: no continuation ever promotes a     validated ref, With persistence_candles=2, the break of validated_choch_low (95)     holds for, The pivot's close (65) clears validated_choch_low (95), but the second     follo, The CHoCH-candidate pivot is too close to the end: there aren't     `persistence (+5 more)

### Community 52 - "Community 52"
Cohesion: 0.24
Nodes (7): Application layer: composition root, orchestration, and entry points.  Wires tog, Liquidity zone scoring engine., Scoring layer: composite, descriptive scoring of market conditions.  Combines ou, Output models for the scoring layer., A `LiquidityZone` paired with its computed relevance score.      `score` is a we, ScoredLiquidityZone, Default timeframe weights used by `LiquidityScoringEngine`.  Higher timeframes r

### Community 53 - "Community 53"
Cohesion: 0.29
Nodes (9): Candle, LiquiditySide, Evaluate the liquidation map point-in-time over `candles`., Distance-matched random-price control levels for the same point.          Same c, First candle index whose wick crosses `price` (sell: low<=; buy: high>=)., Whether price reversed off the level by `reaction_pct` within `window`., _reach_index(), _reacted() (+1 more)

### Community 54 - "Community 54"
Cohesion: 0.18
Nodes (9): LiquidityZone, MarketDirection, MarketStructure, RetailBiasEstimate, Estimates retail trader crowd psychology from market context.      Implementatio, Estimate the dominant retail positioning and its rationale.          `market_str, RetailBiasEstimator, Retail crowd-psychology estimators. (+1 more)

### Community 55 - "Community 55"
Cohesion: 0.27
Nodes (11): _band(), LiquidationBand, Tests for leverage-liquidation domain entities., test_leverage_liquidation_map_rejects_out_of_range_intensity(), test_leverage_liquidation_map_valid(), test_liquidation_band_accepts_end_after_start(), test_liquidation_band_rejects_end_before_start(), test_liquidation_band_rejects_inverted_range() (+3 more)

### Community 56 - "Community 56"
Cohesion: 0.18
Nodes (11): Clean architecture layering (dependencies flow inward), SOLID design notes, liquidity-hunter research platform, ccxt, mypy, pytest, ruff, numpy (+3 more)

### Community 57 - "Community 57"
Cohesion: 0.24
Nodes (7): BehaviorDivergencePanel(), BehaviorDivergencePanelProps, DivergenceCard(), formatPrice(), formatTimestamp(), DIVERGENCE_STYLES, BehaviorDivergence

### Community 58 - "Community 58"
Cohesion: 0.31
Nodes (7): BaseSettings, Configuration layer: application settings and environment management., get_settings(), Application settings, loaded from environment variables or a `.env` file., Return a cached `Settings` instance., Top-level application configuration., Settings

### Community 59 - "Community 59"
Cohesion: 0.25
Nodes (6): LeverageLiquidationMap, LiquidationBand, Self, A price band where positions at one leverage tier would be liquidated.      Anch, Estimated leveraged-liquidation bands for a symbol/timeframe snapshot.      ``do, Leverage liquidation estimator.  Builds a `LeverageLiquidationMap` — a descripti

### Community 60 - "Community 60"
Cohesion: 0.32
Nodes (6): main(), RetailBiasEstimate, Example: estimate retail crowd psychology for an illustrative BTCUSDT scenario., Estimate retail bias for a higher-TF-bearish / lower-TF-bullish-CHOCH scenario., Tests for the BTCUSDT retail bias estimation example script., test_main_estimates_long_bias_against_higher_timeframe_trend()

### Community 61 - "Community 61"
Cohesion: 0.32
Nodes (8): Top-N live levels by proximity-weighted relevance, balanced both sides.      Mir, _select_levels(), ProjectedLevel, A candidate liquidation level, independent of futures positioning.      Produced, _level(), LiquiditySide, test_select_levels_caps_and_balances_sides(), test_select_levels_filters_outside_window()

### Community 62 - "Community 62"
Cohesion: 0.25
Nodes (5): Candle, TimeFrame, Return up to `limit` most recent candles for `symbol`/`timeframe`.          Cand, Return up to `limit` recent open-interest samples for `symbol`., Return up to `limit` recent long/short account-ratio samples.

### Community 63 - "Community 63"
Cohesion: 0.43
Nodes (7): _provider(), Tests for `BinanceFuturesDataProvider`., test_exchange_error_translated_to_request_error(), test_funding_rate_history_maps_rows(), test_long_short_ratio_maps_rows(), test_network_error_translated_to_connection_error(), test_open_interest_history_maps_rows()

### Community 64 - "Community 64"
Cohesion: 0.29
Nodes (5): Candle, LiquidityZone, MarketStructure, Return liquidity zones detected in `candles`.          `candles` must be in chro, Return market structure events detected in `candles`.          `candles` must be

### Community 65 - "Community 65"
Cohesion: 0.33
Nodes (5): LeverageLiquidationEstimator, Estimates leveraged-liquidation bands from futures market state., test_empty_inputs_return_map_without_bands(), test_project_levels_is_futures_independent(), test_rejects_non_positive_current_price()

### Community 66 - "Community 66"
Cohesion: 0.33
Nodes (4): HeatmapBucket, Self, A single price band of the liquidity heatmap.      ``heat`` is the normalized co, Liquidity heatmap engine.  Aggregates estimated resting-liquidity concentration

### Community 67 - "Community 67"
Cohesion: 0.47
Nodes (3): LiquidityZone, ScoredLiquidityZone, Score `zones` relative to `current_price`.          Returns the zones as `Scored

### Community 68 - "Community 68"
Cohesion: 0.50
Nodes (5): Brand Accent Blue #2962ff, Crosshair / Hunter's Scope Motif, TradingView-style Dark UI Theme (#0a0d14 background), Liquidity Hunter Favicon (SVG), Liquidity Hunter Dashboard Branding

### Community 70 - "Community 70"
Cohesion: 0.40
Nodes (5): make_poi(), POIZone, POIZoneStatus, test_invalidated_poi_zones_skipped(), test_poi_order_blocks_anchor_liquidation_bands()

### Community 71 - "Community 71"
Cohesion: 0.50
Nodes (4): CHoCH (Change of Character), Leg-origin CHoCH reference, Persistence-based CHoCH confirmation, Re-anchors (chain / staleness / min-gap)

### Community 76 - "Community 76"
Cohesion: 0.67
Nodes (3): _make_bias(), RetailBiasEstimate, RetailPositioning

## Ambiguous Edges - Review These
- `Logo()` → `Liquidity Hunter Favicon (SVG)`  [AMBIGUOUS]
  frontend/public/favicon.svg · relation: conceptually_related_to

## Knowledge Gaps
- **112 isolated node(s):** `name`, `private`, `version`, `type`, `dev` (+107 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **11 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `Logo()` and `Liquidity Hunter Favicon (SVG)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **Why does `MainChart()` connect `Community 36` to `Community 46`, `API Dashboard Schema`, `Community 69`, `Main Chart Frontend`?**
  _High betweenness centrality (0.153) - this node is a cross-community bridge._
- **Why does `DashboardDataResponse` connect `API Dashboard Schema` to `Community 40`, `Dashboard Composition Root`, `Community 36`, `Narrative Engine`?**
  _High betweenness centrality (0.151) - this node is a cross-community bridge._
- **Why does `InternalStructureDetector` connect `Internal Structure Detector` to `Dashboard Composition Root`, `Swing Structure Detector`, `Candle Test Factories`, `Community 39`, `Community 71`, `API Dashboard Schema`, `Detector Shared Helpers`, `Community 43`, `Equal Level Detectors`, `Main Chart Frontend`, `Community 48`, `Community 51`, `Community 31`?**
  _High betweenness centrality (0.111) - this node is a cross-community bridge._
- **Are the 15 inferred relationships involving `NarrativeEngine` (e.g. with `DashboardData` and `Candle`) actually correct?**
  _`NarrativeEngine` has 15 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `InternalStructureDetector` (e.g. with `Pivot` and `SwingHighDetector`) actually correct?**
  _`InternalStructureDetector` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Are the 14 inferred relationships involving `TimeFrame` (e.g. with `BehaviorDivergence` and `Candle`) actually correct?**
  _`TimeFrame` has 14 INFERRED edges - model-reasoned connections that need verification._