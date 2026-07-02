# Graph Report - .  (2026-07-02)

## Corpus Check
- 18 files · ~97,065 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1780 nodes · 4126 edges · 108 communities (88 shown, 20 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 181 edges (avg confidence: 0.58)
- Token cost: 73,131 input · 9,200 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Domain Enums & Base Model|Domain Enums & Base Model]]
- [[_COMMUNITY_Internal Structure Detector|Internal Structure Detector]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Narrative Engine|Narrative Engine]]
- [[_COMMUNITY_Behavior Divergence Analysis|Behavior Divergence Analysis]]
- [[_COMMUNITY_Manipulation Cycle Detection|Manipulation Cycle Detection]]
- [[_COMMUNITY_Swing Structure Detector|Swing Structure Detector]]
- [[_COMMUNITY_SMC Design Concepts & Rationale|SMC Design Concepts & Rationale]]
- [[_COMMUNITY_Candle & Zone Entities|Candle & Zone Entities]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_API TTL Cache|API TTL Cache]]
- [[_COMMUNITY_Narrative Engine|Narrative Engine]]
- [[_COMMUNITY_Detector Shared Helpers|Detector Shared Helpers]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Leverage Liquidation Estimator|Leverage Liquidation Estimator]]
- [[_COMMUNITY_Retail Trap Psychology|Retail Trap Psychology]]
- [[_COMMUNITY_Zone Test Factories|Zone Test Factories]]
- [[_COMMUNITY_Equal Level Detectors|Equal Level Detectors]]
- [[_COMMUNITY_Frontend Package Config|Frontend Package Config]]
- [[_COMMUNITY_Candle Test Factories|Candle Test Factories]]
- [[_COMMUNITY_Binance Futures Provider|Binance Futures Provider]]
- [[_COMMUNITY_Equal Level Detectors|Equal Level Detectors]]
- [[_COMMUNITY_Internal Structure Detector|Internal Structure Detector]]
- [[_COMMUNITY_Dashboard Composition Root|Dashboard Composition Root]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Heatmap Tests|Heatmap Tests]]
- [[_COMMUNITY_Zone Sweep Mitigation|Zone Sweep Mitigation]]
- [[_COMMUNITY_Liquidity Heatmap Engine|Liquidity Heatmap Engine]]
- [[_COMMUNITY_Main Chart Frontend|Main Chart Frontend]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Binance Spot Provider|Binance Spot Provider]]
- [[_COMMUNITY_Liquidity Scoring Engine|Liquidity Scoring Engine]]
- [[_COMMUNITY_Break Detection Helpers|Break Detection Helpers]]
- [[_COMMUNITY_KPI Row & Dashboard Types|KPI Row & Dashboard Types]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_TypeScript Compiler Config|TypeScript Compiler Config]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Narrative Domain Model|Narrative Domain Model]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Candle & Zone Entities|Candle & Zone Entities]]
- [[_COMMUNITY_Data Provider Ports|Data Provider Ports]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Futures Domain Models|Futures Domain Models]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Internal Structure Detector|Internal Structure Detector]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 59|Community 59]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Equal Level Detectors|Equal Level Detectors]]
- [[_COMMUNITY_Community 61|Community 61]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 58|Community 58]]
- [[_COMMUNITY_Community 74|Community 74]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 68|Community 68]]
- [[_COMMUNITY_Community 69|Community 69]]
- [[_COMMUNITY_Manipulation Cycle Detection|Manipulation Cycle Detection]]
- [[_COMMUNITY_Community 64|Community 64]]
- [[_COMMUNITY_Community 70|Community 70]]
- [[_COMMUNITY_Community 73|Community 73]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 67|Community 67]]
- [[_COMMUNITY_Community 75|Community 75]]
- [[_COMMUNITY_Community 76|Community 76]]
- [[_COMMUNITY_Retail Bias Estimation|Retail Bias Estimation]]
- [[_COMMUNITY_Community 77|Community 77]]
- [[_COMMUNITY_Community 78|Community 78]]
- [[_COMMUNITY_Community 79|Community 79]]
- [[_COMMUNITY_API Dashboard Schema|API Dashboard Schema]]
- [[_COMMUNITY_Community 80|Community 80]]
- [[_COMMUNITY_Community 81|Community 81]]
- [[_COMMUNITY_RetailBias Entity|RetailBias Entity]]
- [[_COMMUNITY_Candle Entity|Candle Entity]]
- [[_COMMUNITY_Liquidation Map Entity|Liquidation Map Entity]]
- [[_COMMUNITY_LiquidityZone Entity|LiquidityZone Entity]]
- [[_COMMUNITY_MarketStructure Entity|MarketStructure Entity]]
- [[_COMMUNITY_POIZone Entity|POIZone Entity]]
- [[_COMMUNITY_CCXT Exchange Stub|CCXT Exchange Stub]]
- [[_COMMUNITY_Generic TypeVar|Generic TypeVar]]
- [[_COMMUNITY_Candle Module|Candle Module]]
- [[_COMMUNITY_MarketDirection Module|MarketDirection Module]]
- [[_COMMUNITY_MarketStructure Module|MarketStructure Module]]
- [[_COMMUNITY_Project Root|Project Root]]

## God Nodes (most connected - your core abstractions)
1. `NarrativeEngine` - 89 edges
2. `make_series()` - 86 edges
3. `make_candle()` - 80 edges
4. `TimeFrame` - 80 edges
5. `InternalStructureDetector` - 76 edges
6. `Candle` - 64 edges
7. `_minimal_data()` - 49 edges
8. `MarketDirection` - 49 edges
9. `MarketStructure` - 48 edges
10. `LiquiditySide` - 43 edges

## Surprising Connections (you probably didn't know these)
- `BinanceDataProvider` --references--> `ccxt`  [EXTRACTED]
  liquidity_hunter/data/providers/binance.py → requirements.txt
- `Liquidity Hunter Favicon (SVG)` --conceptually_related_to--> `Logo()`  [AMBIGUOUS]
  frontend/public/favicon.svg → frontend/src/components/Logo.tsx
- `InternalStructureDetector` --conceptually_related_to--> `CHOCH_FAILED`  [EXTRACTED]
  liquidity_hunter/liquidity/detectors/internal_structure.py → liquidity_hunter/docs/estrutura_bos_choch.md
- `CHOCH_FAILED` --conceptually_related_to--> `structureLineEndTime()`  [EXTRACTED]
  liquidity_hunter/docs/estrutura_bos_choch.md → frontend/src/components/MainChart.tsx
- `RetailBiasEstimate` --semantically_similar_to--> `RetailBias`  [INFERRED] [semantically similar]
  liquidity_hunter/psychology/models.py → liquidity_hunter/core/domain/retail_bias.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **CHoCH to POI Zone Pipeline** — claude_internalstructuredetector, claude_poidetector, claude_poizone, claude_rtosweepevent, claude_load_dashboard_data [EXTRACTED 1.00]
- **Unified Structure Detection Architecture** — claude_swingstructuredetector, claude_internalstructuredetector, claude_bos_staircase, claude_close_break_confirmation, claude_choch_persistence_confirmation, claude_bos_confluence [EXTRACTED 1.00]
- **Dashboard Data Flow (backend to chart)** — claude_load_dashboard_data, claude_dashboarddata, claude_dashboarddataresponse, claude_mainchart [EXTRACTED 1.00]
- **BOS three-filter confirmation chain (close-break, staircase, pullback, confluence)** — liquidity_hunter_docs_estrutura_bos_choch_break_of_structure, liquidity_hunter_liquidity_detectors__common_find_close_break_index, liquidity_hunter_docs_estrutura_bos_choch_bos_staircase, liquidity_hunter_docs_estrutura_bos_choch_pullback_confirmation, liquidity_hunter_liquidity_detectors__common_bos_confluence [EXTRACTED 1.00]

## Communities (108 total, 20 thin omitted)

### Community 0 - "Domain Enums & Base Model"
Cohesion: 0.08
Nodes (60): DomainModel, Enum, Pydantic response models for the `api` layer.  `DashboardData` is a plain `datac, Composition root for the research dashboard.  Wires together `data`, `liquidity`, Narrative engine: synthesizes all detection layers into a coherent story.  Lives, Shared base class for all domain entities., Behavioral divergence between price action and institutional flow., Candle domain entity. (+52 more)

### Community 1 - "Internal Structure Detector"
Cohesion: 0.05
Nodes (68): LIQUIDITY_SWEEP, Trailing active_high/active_low references, InternalStructureDetector, Detects internal BOS/CHoCH/HL/LH from trailing swing pivot references.      Swin, _load_window_candles(), Tests for `InternalStructureDetector`., With the flag unset the detector is byte-for-byte unchanged., Two consecutive HH while bullish BOS is pending -> the latest is used. (+60 more)

### Community 2 - "Community 30"
Cohesion: 0.07
Nodes (59): _fmt(), main(), _print_report(), Example: backtest the leverage liquidation map point-in-time.  Reconstructs liqu, Fetch BTCUSDT candles, run the point-in-time liquidation backtest, report., _distance_buckets(), DistanceBucket, _forward_extremes() (+51 more)

### Community 3 - "Narrative Engine"
Cohesion: 0.10
Nodes (64): NarrativeEngine, Builds a :class:`MarketNarrative` from a completed :class:`DashboardData`., BehaviorDivergence, An observed divergence between price movement and volume delta.      Describes a, ManipulationCycle, An observed institutional manipulation cycle (accumulation -> sweep -> expansion, MarketStructure, A descriptive snapshot of market structure at a point in time. (+56 more)

### Community 4 - "Behavior Divergence Analysis"
Cohesion: 0.08
Nodes (34): BehaviorDivergence, DivergenceType, BehaviorDivergenceAnalyzer, _deduplicate(), _describe_zone_divergence(), Candle, LiquidityZone, MarketDirection (+26 more)

### Community 5 - "Manipulation Cycle Detection"
Cohesion: 0.09
Nodes (31): ManipulationCycleDetector, Candle, datetime, LiquiditySide, LiquidityZone, ManipulationCycle, MarketDirection, MarketStructure (+23 more)

### Community 6 - "Swing Structure Detector"
Cohesion: 0.07
Nodes (51): Detects BOS/CHoCH and LH/HL from major (swing) pivots.      Swing highs/lows are, SwingStructureDetector, make_series(), Build a chronological candle series from parallel high/low lists., _confirmed_main_series(), Candle, Tests for `SwingStructureDetector`., The LH at index 12 is a candidate, not an immediate CHoCH reference.     It only (+43 more)

### Community 7 - "SMC Design Concepts & Rationale"
Cohesion: 0.07
Nodes (51): _drop_pre_break_reference_bos, _reanchor_bos_close_break, Additive-over-State-Machine Principle, BehaviorDivergence, BehaviorDivergenceAnalyzer, BinanceDataProvider, BinanceFuturesDataProvider, BinanceFuturesOHLCVProvider (+43 more)

### Community 8 - "Candle & Zone Entities"
Cohesion: 0.07
Nodes (34): main(), RetailBiasEstimate, Example: estimate retail crowd psychology for an illustrative BTCUSDT scenario., Estimate retail bias for a higher-TF-bearish / lower-TF-bullish-CHOCH scenario., DomainModel, Base class for immutable, strictly-validated domain entities.      - `frozen=Tru, LiquiditySide, Which side of price a liquidity zone rests on. (+26 more)

### Community 9 - "Community 37"
Cohesion: 0.06
Nodes (21): gradientRgb(), HeatmapBand, HeatmapStripPaneView, HeatmapStripPrimitive, HeatmapStripRenderer, ResolvedBand, LiquidationBandInput, LiquidationBandsPaneView (+13 more)

### Community 10 - "API TTL Cache"
Cohesion: 0.07
Nodes (28): Hashable, T, A minimal in-memory TTL cache, used to avoid redundant Binance requests., A time-based cache keyed by arbitrary hashable keys., Return the cached value for `key`, computing it via `factory` if missing/expired, TTLCache, FastAPI application entrypoint for the `api` layer.  Run locally with:      poet, health() (+20 more)

### Community 11 - "Narrative Engine"
Cohesion: 0.12
Nodes (11): Candle, DashboardData, datetime, ManipulationCycle, MarketDirection, MarketStructure, RTOSweepEvent, ManipulationPhase (+3 more)

### Community 12 - "Detector Shared Helpers"
Cohesion: 0.11
Nodes (28): MarketStructureDetector, Shared interfaces for liquidity zone and market structure detectors., Detects `MarketStructure` events from a series of candles., bos_confluence(), collect_pivots(), find_close_break_index(), find_fvg(), Pivot (+20 more)

### Community 13 - "Community 34"
Cohesion: 0.11
Nodes (23): Exchange, BinanceFuturesDataProvider, Any, FundingRate, LongShortRatio, OpenInterestPoint, TimeFrame, The CCXT unified swap symbol (e.g. "BTC/USDT:USDT") for `symbol`. (+15 more)

### Community 14 - "Leverage Liquidation Estimator"
Cohesion: 0.09
Nodes (30): _bucket_select(), _clamp(), _Entry, _entry_anchors(), LeverageLiquidationEstimator, _liquidation_hit_time(), _open_interest_change_pct(), _positioning_score() (+22 more)

### Community 15 - "Retail Trap Psychology"
Cohesion: 0.11
Nodes (26): Retail trap setup, LiquidityZone, MarketDirection, MarketStructure, RetailBiasEstimate, RetailPositioning, Estimates retail crowd psychology from trend, structure, and liquidity., _reference_price() (+18 more)

### Community 16 - "Zone Test Factories"
Cohesion: 0.14
Nodes (33): make_zone(), LiquiditySide, LiquidityZone, LiquidityZoneType, Build a `LiquidityZone` at `price` (or `[price_low, price]` if given)., _candle(), _estimate(), make_poi() (+25 more)

### Community 17 - "Equal Level Detectors"
Cohesion: 0.10
Nodes (18): price_range(), The full high/low range spanned by `candles`, used to normalize strength scores., Candle, LiquidityZone, Swing high / swing low liquidity zone detectors.  A swing point is a fractal-sty, Base class for fractal-style swing point detection., Detects swing highs: local maxima of `Candle.high`.      Swing highs mark restin, Detects swing lows: local minima of `Candle.low`.      Swing lows mark resting s (+10 more)

### Community 18 - "Frontend Package Config"
Cohesion: 0.07
Nodes (28): dependencies, lightweight-charts, react, react-dom, devDependencies, eslint, @eslint/js, eslint-plugin-react-hooks (+20 more)

### Community 19 - "Candle Test Factories"
Cohesion: 0.13
Nodes (19): Candle, MarketStructure, Re-anchor each continuation BOS to the first *close* beyond the level it broke., _reanchor_bos_close_break(), _describe_event(), _oi_at(), OIRegimeAnalyzer, datetime (+11 more)

### Community 20 - "Binance Futures Provider"
Cohesion: 0.13
Nodes (21): DataProviderConnectionError, DataProviderError, DataProviderRequestError, Exceptions raised by the data layer., Raised when a data provider rejects a request (e.g. invalid symbol/timeframe)., Base exception for all data provider failures., Raised when a data provider cannot be reached after exhausting retries., BinanceFuturesOHLCVProvider (+13 more)

### Community 21 - "Equal Level Detectors"
Cohesion: 0.11
Nodes (23): detect_zones(), Candle, LiquidityZone, Run all liquidity detectors over `candles`., main(), ScoredLiquidityZone, Example: score and rank BTCUSDT liquidity zones by relevance.  Run with:      po, Fetch BTCUSDT 1h candles, score detected liquidity zones, and print them ranked. (+15 more)

### Community 22 - "Internal Structure Detector"
Cohesion: 0.08
Nodes (26): make_candle(), Candle, TimeFrame, Build a valid `Candle` with the given high/low and a midpoint open.      `close`, A wick-only break does not advance the state (no trend leak) and freezes     the, Verify all fields on a confirmed BOS event: timestamp, price_level,     referenc, A clean bearish impulse of consecutive lower-low pivots with no     intervening, Bearish BOS events need LH pullbacks. The CHoCH break is validated     against v (+18 more)

### Community 23 - "Dashboard Composition Root"
Cohesion: 0.16
Nodes (24): load_dashboard_data(), datetime, OHLCVProvider, Index in ``candles`` where internal-structure detection should start.      Retur, Fetch candles and assemble liquidity, ranking, and retail bias data., _structural_anchor_index(), _FakeProvider, Tests for `liquidity_hunter.app.dashboard_data`. (+16 more)

### Community 24 - "Community 43"
Cohesion: 0.12
Nodes (16): ABC, Data layer: market data acquisition, repositories, and persistence adapters.  Re, FuturesDataProvider, OHLCVProvider, Candle, TimeFrame, Abstract interfaces (ports) for market data providers., A source of historical OHLCV candle data.      Concrete implementations are resp (+8 more)

### Community 25 - "Heatmap Tests"
Cohesion: 0.20
Nodes (23): HeatmapBucket, _build(), _engine(), _hot_bucket(), _make_cycle(), _make_poi(), LiquidityHeatmap, ManipulationCycle (+15 more)

### Community 26 - "Zone Sweep Mitigation"
Cohesion: 0.19
Nodes (12): _check_zone(), mark_swept_zones(), Candle, LiquidityZone, Post-detection sweep check for liquidity zones.  Scans candles after each zone's, Return a new list with swept zones marked as mitigated., _eqh(), _eql() (+4 more)

### Community 27 - "Liquidity Heatmap Engine"
Cohesion: 0.10
Nodes (20): _bucket_side(), _gaussian_smooth(), LiquidityHeatmapEngine, _overlapping_buckets(), Candle, LiquidityHeatmap, LiquiditySide, LiquidityZone (+12 more)

### Community 28 - "Main Chart Frontend"
Cohesion: 0.13
Nodes (17): balancedTake(), buildManipulationBoxes(), detectDivergences(), Divergence, DIVERGENCE_MARKER_SHAPES, failedChochTime(), findPivots(), isFailedChoch() (+9 more)

### Community 29 - "Community 32"
Cohesion: 0.13
Nodes (12): FuturesDataProvider, _FakeFuturesProvider, _PerTimeframeFakeProvider, FundingRate, LongShortRatio, OpenInterestPoint, TimeFrame, _RaisingFuturesProvider (+4 more)

### Community 30 - "Binance Spot Provider"
Cohesion: 0.14
Nodes (18): BinanceDataProvider, klines_row_to_candle(), Any, Candle, Exchange, TimeFrame, Convert a concatenated symbol (e.g. "BTCUSDT") to CCXT's unified form ("BTC/USDT, Map one raw Binance kline row (12 columns) onto a `Candle`.      Shared by the s (+10 more)

### Community 31 - "Liquidity Scoring Engine"
Cohesion: 0.16
Nodes (20): Liquidity target scoring methodology, LiquidityScoringEngine, TimeFrame, Ranks `LiquidityZone` objects by their relevance as liquidity targets.      The, DEFAULT_TIMEFRAME_WEIGHTS, make_zone(), LiquiditySide, LiquidityZone (+12 more)

### Community 32 - "Break Detection Helpers"
Cohesion: 0.19
Nodes (21): find_sustained_break_index(), find_wick_break_index(), is_sustained_break(), The first index in `candles[start_index:end_index + 1]` at which a     sustained, Whether the break of `active_price` at `candles[pivot_index]` holds.      True i, The first index in `candles[start_index:end_index + 1]` whose wick     crosses `, _candles(), _candles_hl() (+13 more)

### Community 33 - "KPI Row & Dashboard Types"
Cohesion: 0.10
Nodes (20): AnomalySeverity, DivergenceType, HeatmapBucket, LeverageLiquidationMap, LiquidityHeatmap, LiquiditySide, LiquidityZone, LiquidityZoneType (+12 more)

### Community 34 - "Community 39"
Cohesion: 0.28
Nodes (20): _analyzer(), _candles(), _oi(), OIRegime, OpenInterestPoint, Tests for `liquidity_hunter.psychology.analyzers.oi_regime`., The flush lands on the sample *after* the sweep candle and must be seen., test_bos_with_falling_oi_is_covering() (+12 more)

### Community 35 - "Community 52"
Cohesion: 0.13
Nodes (13): BaseModel, Dashboard data endpoint., DashboardDataResponse, JSON representation of `app.dashboard_data.DashboardData`., DashboardData, A snapshot of research data for a single symbol/timeframe., Application layer: composition root, orchestration, and entry points.  Wires tog, Liquidity zone scoring engine. (+5 more)

### Community 36 - "TypeScript Compiler Config"
Cohesion: 0.11
Nodes (18): compilerOptions, allowImportingTsExtensions, erasableSyntaxOnly, jsx, lib, module, moduleDetection, moduleResolution (+10 more)

### Community 37 - "Community 55"
Cohesion: 0.17
Nodes (16): LeverageLiquidationMap, LiquidationBand, Self, A price band where positions at one leverage tier would be liquidated.      Anch, Estimated leveraged-liquidation bands for a symbol/timeframe snapshot.      ``do, _band(), LiquidationBand, Tests for leverage-liquidation domain entities. (+8 more)

### Community 38 - "Community 31"
Cohesion: 0.19
Nodes (12): POIDetector, Candle, datetime, MarketStructure, POIZone, RTOSweepEvent, TimeFrame, Detects institutional order block zones from structure events + candles.      Pa (+4 more)

### Community 39 - "Community 33"
Cohesion: 0.11
Nodes (17): compilerOptions, allowImportingTsExtensions, erasableSyntaxOnly, lib, module, moduleDetection, moduleResolution, noEmit (+9 more)

### Community 40 - "Narrative Domain Model"
Cohesion: 0.18
Nodes (16): MarketNarrative, NarrativeAnomaly, NarrativeEvent, A single event in the narrative timeline.      Represents a significant market o, A contradiction between an expected pattern and what actually happened.      Sur, Synthesized institutional narrative for a symbol/timeframe snapshot.      Connec, Construction and validation tests for narrative domain entities., test_market_narrative_is_frozen() (+8 more)

### Community 41 - "Community 35"
Cohesion: 0.22
Nodes (14): Exception, Exception, T, Retry helpers for transient data-provider failures., Retry a function with exponential backoff when it raises `exceptions`.      The, retry_with_backoff(), _PermanentError, Tests for the retry-with-backoff decorator. (+6 more)

### Community 42 - "Community 41"
Cohesion: 0.13
Nodes (5): LineLabel, LineLabelsPaneView, LineLabelsPrimitive, LineLabelsRenderer, PositionedLabel

### Community 43 - "Community 42"
Cohesion: 0.22
Nodes (11): main(), Candle, Example: fetch 500 BTCUSDT 1h candles from Binance and print the first five.  Ru, Fetch candles and print the first five. Returns the full list., _FakeProvider, _make_candles(), Candle, TimeFrame (+3 more)

### Community 44 - "Candle & Zone Entities"
Cohesion: 0.17
Nodes (12): Candle, Self, A single OHLCV price bar for a symbol and timeframe., Construction and validation tests for core domain entities., test_candle_rejects_inconsistent_high(), test_candle_rejects_taker_buy_volume_exceeding_volume(), test_candle_valid_construction(), test_liquidity_zone_rejects_inverted_range() (+4 more)

### Community 45 - "Data Provider Ports"
Cohesion: 0.26
Nodes (12): FallbackOHLCVProvider, Candle, TimeFrame, Tries `primary`, falling back to `secondary` when the symbol is rejected.      U, _candle(), _provider(), Tests for `FallbackOHLCVProvider`., test_caps_fallback_limit_to_secondary_max() (+4 more)

### Community 46 - "Community 44"
Cohesion: 0.23
Nodes (12): Indicators layer: derived numerical series computed from `Candle` data.  Houses, Candle, Volume delta: per-candle taker buy/sell aggression imbalance., `volume_delta` for each candle in `candles`, in the same order., Net taker aggression for `candle`.      `2 * taker_buy_volume - volume` is posit, volume_delta(), volume_delta_series(), Tests for `liquidity_hunter.indicators.volume_delta`. (+4 more)

### Community 47 - "Community 46"
Cohesion: 0.14
Nodes (5): POIBox, POIBoxesPaneView, POIBoxesPrimitive, POIBoxesRenderer, ResolvedBox

### Community 48 - "Futures Domain Models"
Cohesion: 0.21
Nodes (13): LongShortRatio, OpenInterestPoint, A single open-interest sample for a perpetual contract.      ``open_interest`` i, A crowd long/short account-ratio sample for a perpetual contract.      ``long_ac, Tests for futures market-state domain entities., test_long_short_ratio_rejects_non_positive_ratio(), test_long_short_ratio_rejects_out_of_range_pct(), test_long_short_ratio_valid() (+5 more)

### Community 49 - "Community 48"
Cohesion: 0.26
Nodes (12): Counter, _load_fixture_candles(), main(), _print_comparison(), Candle, MarketStructure, StructureEvent, Diagnostic: compare `InternalStructureDetector` re-anchor modes.  Runs the inter (+4 more)

### Community 50 - "Community 36"
Cohesion: 0.18
Nodes (6): DashboardQuery, TIMEFRAME_OPTIONS, MainChart(), MainChartProps, DashboardData, TimeFrame

### Community 51 - "Community 57"
Cohesion: 0.19
Nodes (8): BehaviorDivergencePanelProps, DivergenceCard(), formatPrice(), formatTimestamp(), CollapsibleSection(), CollapsibleSectionProps, DIVERGENCE_STYLES, BehaviorDivergence

### Community 52 - "Community 49"
Cohesion: 0.18
Nodes (10): AnomalyCallout(), EVENT_TYPE_STYLES, formatTimestamp(), NarrativePanelProps, PHASE_LABELS, SEVERITY_STYLES, TimelineEvent(), MarketNarrative (+2 more)

### Community 53 - "Internal Structure Detector"
Cohesion: 0.15
Nodes (13): _leg_origin_series(), _persistence_test_series(), Candle, With `bos_leg_origin_choch_ref`, the confirmed BOS promotes the low its     leg, Same series with the flag off: no continuation ever promotes a     validated ref, With persistence_candles=2, the break of validated_choch_low (95)     holds for, The pivot's close (65) clears validated_choch_low (95), but the second     follo, The CHoCH-candidate pivot is too close to the end: there aren't     `persistence (+5 more)

### Community 54 - "Community 30"
Cohesion: 0.23
Nodes (8): CaptureFixture, _FakeProvider, Candle, TimeFrame, Tests for the liquidation backtest example script., test_main_runs_backtest_and_reports(), _zigzag(), Test helpers for building candle series with known swing points.

### Community 55 - "Community 59"
Cohesion: 0.18
Nodes (10): BIAS_CONFIG, DIRECTION_CONFIG, fmtPct(), KpiCardProps, KpiRow(), KpiRowProps, OI_REGIME_CONFIG, MarketDirection (+2 more)

### Community 56 - "Community 47"
Cohesion: 0.20
Nodes (8): CycleCard(), formatPrice(), formatTimestamp(), ManipulationCyclesPanelProps, PHASE_STYLES, STATUS_STYLES, ZONE_LABELS, ManipulationCycle

### Community 57 - "Community 38"
Cohesion: 0.23
Nodes (8): main(), Example: detect liquidity zones in BTCUSDT 1h candles.  Run with:      poetry ru, Fetch BTCUSDT 1h candles, detect liquidity zones, and print a summary., _FakeProvider, Candle, TimeFrame, Tests for the BTCUSDT liquidity detection example script., test_main_detects_swing_and_equal_zones()

### Community 58 - "Equal Level Detectors"
Cohesion: 0.23
Nodes (6): LiquidityZoneDetector, Detects `LiquidityZone` instances from a series of candles., _EqualLevelDetector, Candle, LiquidityZone, Base class that groups nearby swing points into equal-level zones.

### Community 59 - "Community 61"
Cohesion: 0.27
Nodes (11): _drop_pre_break_reference_bos(), Drop continuation BOS whose reference formed before the prior BOS broke.      A, StructureEvent, _structure_event(), test_drop_pre_break_reference_bos_choch_starts_new_leg(), test_drop_pre_break_reference_bos_drops_wick_attempt_reference(), test_drop_pre_break_reference_bos_keeps_post_break_reference(), test_drop_pre_break_reference_bos_keeps_unresolved_reference() (+3 more)

### Community 60 - "Community 56"
Cohesion: 0.18
Nodes (11): Clean architecture layering (dependencies flow inward), SOLID design notes, liquidity-hunter research platform, ccxt, mypy, pytest, ruff, numpy (+3 more)

### Community 61 - "Community 58"
Cohesion: 0.31
Nodes (7): BaseSettings, Configuration layer: application settings and environment management., get_settings(), Application settings, loaded from environment variables or a `.env` file., Return a cached `Settings` instance., Top-level application configuration., Settings

### Community 62 - "Community 74"
Cohesion: 0.21
Nodes (8): BOS Staircase, BOS (Break of Structure), CHoCH (Change of Character), Impulse BOS staging, Leg-origin CHoCH reference, Persistence-based CHoCH confirmation, Pullback confirmation + wick filter, Re-anchors (chain / staleness / min-gap)

### Community 63 - "Community 50"
Cohesion: 0.32
Nodes (5): _FakeProvider, Candle, TimeFrame, Tests for the BTCUSDT liquidity scoring example script., test_main_scores_and_ranks_zones()

### Community 64 - "Community 68"
Cohesion: 0.33
Nodes (6): Brand Accent Blue #2962ff, Crosshair / Hunter's Scope Motif, TradingView-style Dark UI Theme (#0a0d14 background), Liquidity Hunter Favicon (SVG), Liquidity Hunter Dashboard Branding, Logo()

### Community 65 - "Community 69"
Cohesion: 0.29
Nodes (7): _fetch_futures_state(), FundingRate, FuturesDataProvider, LongShortRatio, OpenInterestPoint, TimeFrame, Fetch perpetual-futures market state (OI history, funding, long/short).      Deg

### Community 66 - "Manipulation Cycle Detection"
Cohesion: 0.43
Nodes (6): Institutional liquidity capture + return-to-origin on a POI zone.      Fires whe, RTOSweepEvent, _Accumulation, _Expansion, Institutional manipulation cycle detector.  Connects existing observations (liqu, _SweepTrigger

### Community 67 - "Community 64"
Cohesion: 0.29
Nodes (5): Candle, LiquidityZone, MarketStructure, Return liquidity zones detected in `candles`.          `candles` must be in chro, Return market structure events detected in `candles`.          `candles` must be

### Community 68 - "Community 70"
Cohesion: 0.33
Nodes (6): gt, le, get_dashboard(), TimeFrame, Return a `DashboardData` snapshot for `symbol`/`timeframe` as JSON.      Results, Query

### Community 69 - "Community 73"
Cohesion: 0.33
Nodes (5): FundingRate, A funding-rate sample for a perpetual contract.      ``funding_rate`` is the sig, Return up to `limit` recent funding-rate samples for `symbol`., test_funding_rate_allows_negative(), _funding()

### Community 70 - "Community 54"
Cohesion: 0.33
Nodes (5): LiquidityZone, MarketDirection, MarketStructure, RetailBiasEstimate, Estimate the dominant retail positioning and its rationale.          `market_str

### Community 71 - "Community 67"
Cohesion: 0.47
Nodes (3): LiquidityZone, ScoredLiquidityZone, Score `zones` relative to `current_price`.          Returns the zones as `Scored

### Community 73 - "Community 76"
Cohesion: 0.67
Nodes (3): _make_bias(), RetailBiasEstimate, RetailPositioning

## Ambiguous Edges - Review These
- `Logo()` → `Liquidity Hunter Favicon (SVG)`  [AMBIGUOUS]
  frontend/public/favicon.svg · relation: conceptually_related_to

## Knowledge Gaps
- **136 isolated node(s):** `name`, `private`, `version`, `type`, `dev` (+131 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **20 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `Logo()` and `Liquidity Hunter Favicon (SVG)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **Why does `TimeFrame` connect `Domain Enums & Base Model` to `Internal Structure Detector`, `Community 30`, `Narrative Engine`, `Behavior Divergence Analysis`, `Manipulation Cycle Detection`, `Candle & Zone Entities`, `API TTL Cache`, `Community 34`, `Leverage Liquidation Estimator`, `Zone Test Factories`, `Equal Level Detectors`, `Binance Futures Provider`, `Equal Level Detectors`, `Dashboard Composition Root`, `Community 43`, `Heatmap Tests`, `Zone Sweep Mitigation`, `Binance Spot Provider`, `Liquidity Scoring Engine`, `Community 52`, `Community 55`, `Narrative Domain Model`, `Community 42`, `Candle & Zone Entities`, `Data Provider Ports`, `Community 48`, `Community 30`, `Community 38`, `Community 50`, `Manipulation Cycle Detection`?**
  _High betweenness centrality (0.139) - this node is a cross-community bridge._
- **Why does `Candle` connect `Candle & Zone Entities` to `Domain Enums & Base Model`, `Internal Structure Detector`, `Community 30`, `Narrative Engine`, `Behavior Divergence Analysis`, `Manipulation Cycle Detection`, `Swing Structure Detector`, `Candle & Zone Entities`, `API TTL Cache`, `Detector Shared Helpers`, `Leverage Liquidation Estimator`, `Zone Test Factories`, `Equal Level Detectors`, `Binance Futures Provider`, `Equal Level Detectors`, `Internal Structure Detector`, `Community 43`, `Zone Sweep Mitigation`, `Binance Spot Provider`, `Break Detection Helpers`, `Community 42`, `Data Provider Ports`, `Community 44`, `Community 48`, `Community 30`, `Community 38`, `Community 50`, `Manipulation Cycle Detection`?**
  _High betweenness centrality (0.106) - this node is a cross-community bridge._
- **Why does `MarketDirection` connect `Domain Enums & Base Model` to `Internal Structure Detector`, `Manipulation Cycle Detection`, `Narrative Engine`, `Behavior Divergence Analysis`, `Manipulation Cycle Detection`, `Swing Structure Detector`, `Community 39`, `Candle & Zone Entities`, `Narrative Domain Model`, `Detector Shared Helpers`, `Candle & Zone Entities`, `Retail Trap Psychology`, `Zone Test Factories`, `Dashboard Composition Root`, `Heatmap Tests`?**
  _High betweenness centrality (0.056) - this node is a cross-community bridge._
- **Are the 15 inferred relationships involving `NarrativeEngine` (e.g. with `DashboardData` and `Candle`) actually correct?**
  _`NarrativeEngine` has 15 INFERRED edges - model-reasoned connections that need verification._
- **Are the 17 inferred relationships involving `TimeFrame` (e.g. with `BehaviorDivergence` and `Candle`) actually correct?**
  _`TimeFrame` has 17 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `InternalStructureDetector` (e.g. with `Pivot` and `SwingHighDetector`) actually correct?**
  _`InternalStructureDetector` has 3 INFERRED edges - model-reasoned connections that need verification._