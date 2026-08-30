# Composition root (app/dashboard_data.py)

Extracted from `CLAUDE.md` (2026-08-29) to keep that file under its size limit.

### Composition root (`liquidity_hunter/app/dashboard_data.py`)

- **`DashboardData`** — a frozen dataclass snapshot combining `candles`,
  `higher_timeframe_direction`, `liquidity_zones`, `ranked_zones`,
  `market_structure_events`, `internal_structure_events`, `retail_bias`,
  `poi_zones` (`list[POIZone]`),
  `manipulation_cycles` (`list[ManipulationCycle]`),
  `behavior_divergences` (`list[BehaviorDivergence]`),
  `liquidity_heatmap` (`LiquidityHeatmap | None`),
  `liquidation_map` (`LeverageLiquidationMap | None`),
  `narrative` (`MarketNarrative | None`), `oi_analysis`
  (`OIAnalysis | None`), `market_control` (`MarketControlState | None` — who
  controls the tape from CVD×OI, `None` for spot; see `MarketControlAnalyzer`),
  `supertrend_breaks` (`list[SupertrendBreak]` — each Supertrend flip
  qualified as `genuine`/`stop_run`/`unknown`; runs after the futures block
  since it reads the participation layers built there),
  `volume_profile` (`VolumeProfile | None` — volume-at-price over the
  **recent** `_VOLUME_PROFILE_LOOKBACK` = 200 candles of the visible window
  (`_VOLUME_PROFILE_BUCKETS` = 200 bands), built from `candles` alone by
  `indicators.volume_profile`; a lookback rather than the whole series
  because the reading is about where the market is trading *now* — a
  1200-candle H1 profile spans ~50 days and buries the current balance;
  `None` when the window has no price range),
  `vwap` (`VWAPSeries | None` — the periodic VWAP over the visible window;
  the restart period is per timeframe (`_VWAP_ANCHOR_PERIOD`: the UTC day
  intraday, `WEEK` on H4, `MONTH` on D1/W1 — measured 2026-07-27, a
  day-anchored H4 gives 6-candle segments and D1 exactly one, so the average
  would report the candle itself),
  `anchored_vwaps` (`list[VWAPSeries]` — VWAPs anchored to the events that drew
  the current population in, from `_build_anchored_vwaps`: the last confirmed
  trend flip (`CHANGE_OF_CHARACTER`/`CHOCH_FAILED`) and the last
  `LIQUIDITY_SWEEP` of the internal stream, each read as that crowd's
  break-even. Provisional marks are skipped — a live-edge anchor can vanish on
  the next refresh, and a line that re-bases itself is not a break-even — as
  are anchors with fewer than `_VWAP_MIN_ANCHOR_CANDLES` = 3 candles behind
  them),
  `liquidity_hunt` (`LiquidityHuntState | None`),
  `higher_timeframe` (`TimeFrame | None` — the `_HIGHER_TIMEFRAME_MAP` anchor
  pair `higher_timeframe_direction` was measured on, `None` for the top
  timeframe; lets the frontend label readings "vs 4H" instead of a generic
  "HTF"), and `consolidation_ranges` (`list[ConsolidationRange]` — confirmed
  lateral ranges overlapping the visible window, from the
  `_detect_consolidations` post-pass inside `_run_internal_structure`:
  `detect_consolidation_ranges` over the *surviving* non-provisional
  BOS/CHoCH/`CHOCH_FAILED` boundaries, height cap
  min(`_CONSOLIDATION_MAX_HEIGHT_ATR` = 8 × mean TR%,
  `_CONSOLIDATION_MAX_HEIGHT_ABS[timeframe]` — an absolute per-TF ceiling, e.g.
  H1 7%, added 2026-07-19 because a high-vol asset's ATR unit degenerates and
  let an 11.7% rally-and-dump rotation confirm as a "range" on HYPE H1),
  `_CONSOLIDATION_MIN_CANDLES`
  = 60, resolve persistence 4 — calibrated 2026-07-14, see
  `docs/structure_decisions.md`) for one symbol/timeframe. Under
  `_CONSOLIDATION_STAGE_BREAKOUT_EVENTS` (default `True`), range breakouts
  also stage additive events into `internal_structure_events` via
  `stage_breakout_events` (deduped within
  `_CONSOLIDATION_STAGE_DEDUP_CANDLES` = 12 of a real same-direction
  BOS/CHoCH; merged timestamp-sorted).
- **`load_dashboard_data(provider=..., symbol=..., timeframe=..., limit=1200, swing_lookback=..., confluence_filter=False, futures_provider=...)`**
  — fetches a single buffered candle series (`buffered_candles`) and derives the
  visible `candles` from its tail (`buffered_candles[-limit:]`; no separate fetch
  for the visible window — its second fetch would be redundant and could race a
  freshly-printed candle). It then runs all liquidity detectors and scores the
  zones via `LiquidityScoringEngine`, runs `SwingStructureDetector` on the
  buffered series and
  `InternalStructureDetector` on a **structurally anchored** slice of it to
  populate `market_structure_events` and `internal_structure_events`
  respectively, both filtered to the visible window. The internal detector's
  base `swing_lookback`/`persistence_candles` are resolved **per timeframe** from
  `_INTERNAL_STRUCTURE_PARAMS` (currently a uniform `(5, 2)` for every timeframe
  M5→W1, matching `_DEFAULT_INTERNAL_PARAMS = (5, 2)` — fast flips, compensated
  by the confirmed-trend barrier below; the per-TF dict is kept so
  timeframes can diverge again without touching the wiring) — so the constructor
  defaults (`swing_lookback=2`/`persistence_candles=5`) apply only to a
  directly-built detector, not the production wiring. The internal detector's
  output is passed through **`_reanchor_bos_close_break`** before the visible
  filter: each `BREAK_OF_STRUCTURE` is
  re-timed to the first candle that *closes* beyond the formed level it broke
  (`reference_price_level`), within the window the BOS stays active (up to the
  next same-direction BOS or opposite-direction CHoCH); any BOS whose leg only
  *wicked* past that level (never closed) is **dropped** — a conservative
  close-break confirmation matching the macro SMC cycle. Under
  `_RESCUE_LEG_LAUNCH_BOS` (wired `True`), a **leg-launch** BOS (the first of its
  leg, referencing the CHoCH-seeded launch level) that finds no close in its own
  window gets an extended search through the *next* same-direction BOS's window
  before being dropped — a leg that retests the CHoCH can close through its
  launch level a few candles into the successor's territory — and the shallower
  continuations it passes over are suppressed, so the leg reads
  `CHoCH → BOS at the launch fundo/topo → …`. The one-continuation bound is
  load-bearing (unbounded, an AAVE D1 launch BOS scanned 7 months and ate the
  real staircase); see `docs/structure_decisions.md`. The pass also sets each
  BOS's `reference_timestamp` to the candle that *formed* the broken level (the
  prior swing extreme, found by scanning back for a matching low/high), so the
  frontend can start the line at the level's origin. The same pass runs on the
  major detector's events too (`all_major_events`), keeping the two consistent.
  A second pass, **`_drop_pre_break_reference_bos`** (both streams, after the
  re-anchor), drops any continuation BOS whose `reference_timestamp` predates
  the confirming close of the previous same-direction BOS in the same leg: a
  wick that poked beyond the still-unbroken prior BOS level ratchets the
  detector's staircase extreme, so the next continuation would report that
  pre-break wick as the formed level it broke — but a reference may only come
  from price action *after* the prior break confirms. A CHoCH resets the
  constraint for its direction (the first BOS of a leg references the
  CHoCH-seeded level, formed before the flip); a non-provisional `CHOCH_FAILED`
  likewise resets the *opposite* direction (the leg it flips into); BOS without
  a resolved `reference_timestamp` are kept. Same-timestamp BOS are judged
  earlier-formed-reference first (the earlier structural break). The
  `reference_timestamp` itself (the line's start anchor, purely cosmetic) is
  resolved by `_common.resolve_break_origin_timestamp` — own-side exact →
  opposite-side exact (a first-BOS floor is the reversal's opposite-polarity
  extreme) → range-straddle — used both here (to fill a `None` the detector's
  own-side scan left) and by the provisional-BOS path in the detector.
  A pass **`_repolarize_weak_failure_bos`** (internal stream only, after the BOS
  passes) re-points a BOS whose `reference_price_level` equals a preceding
  non-provisional opposite-direction `CHOCH_FAILED`'s level — the weak-level
  failure re-seeds the resumed staircase at the reclaimed level, which has the
  *opposite* polarity and already carries the ✕'s own line — onto the last
  formed `LOWER_HIGH`/`HIGHER_LOW` since the failure's reference that the break
  candle actually **closes beyond**. Cosmetic and strictly non-subtractive (the
  in-detector variant was measured and rejected: it drops six BOS across the
  live matrix). See `docs/structure_decisions.md`.
  A third pass, **`_drop_resumed_fizzle_markers`** (internal stream only, after
  the BOS passes), drops a fast-fizzle `CHOCH_FAILED` marker followed by a
  chart-surviving same-direction BOS **or by a candle closing beyond the marked
  CHoCH's own extreme** (`price_level`) — either way the reclaim was a deep
  pullback the reversal recovered from, not a fizzle (see the fizzle-marker
  status block; the close rule covers a resumed leg whose BOS hasn't confirmed
  a pullback yet, the SOL M15 2026-07-16 case).
  `confluence_filter` is exposed for tests that exercise state-machine logic
  without needing emission-quality filters. `higher_timeframe_direction` (as of
  2026-07-06) is the **state-machine trend** (`final_trend`) of the **internal**
  detector run on the **higher** timeframe (mapped via `_HIGHER_TIMEFRAME_MAP`)
  with that timeframe's own production wiring — built by
  **`_build_internal_detector(timeframe, confluence_filter=...)`**, the single
  construction point shared with the current-TF internal run (per-TF
  params + all flags), fed the HTF series fetched at the same `buffered_limit`
  and sliced from its own `_structural_anchor_index` — i.e. **exactly the run
  the HTF view renders**, so the reported HTF direction always matches the
  structure the user sees when opening that timeframe, and the liquidity hunt's
  "counter-trend?" comparison uses the same trend semantics on both sides of
  the pair. (The previous source — `SwingStructureDetector` on a 100-candle
  window, `_HIGHER_TIMEFRAME_CANDLE_LIMIT`, now removed — used a different
  methodology on a window too short for its lookback: measured 2026-07-06
  across BTC/ETH/SOL/AAVE × 5m..1d, 11/24 combos changed — AAVE intraday and
  BTC 1h/4h read a bootstrap `NEUTRAL` (hunt card invisible) and BTC intraday
  read H1 `bullish` against an H1 chart showing a bearish CHoCH; SOL, the live
  hunt scenario, was unchanged.) For the top timeframe (no higher TF) it falls
  back to the current run's `internal_detector.final_trend`, so downstream
  comparisons read "aligned". Using the detector's `final_trend` rather than
  the last event's `direction` avoids spurious flips from descriptive
  HH/HL/LH/LL pivots or `LIQUIDITY_SWEEP` events (whose `direction` is the
  pivot/wick side, not the standing trend); `InternalStructureDetector` now
  exposes `final_trend` mirroring the major's (provisional marks never mutate
  it, `CHOCH_FAILED` reverts it).

  `buffered_candles` is fetched with an extra
  `_INTERNAL_STRUCTURE_BOOTSTRAP_BUFFER = 300` candles of history prepended
  beyond `limit` (`buffered_limit = min(limit + _INTERNAL_STRUCTURE_BOOTSTRAP_BUFFER,
  provider.max_fetch_limit)` — the cap comes from the provider: 1000 for spot,
  1500 for the futures default). The **major** detector runs on the full
  `buffered_candles`; the **internal** detector (and `POIDetector`, which runs
  on the same slice) instead start at a **structural anchor** —
  `_structural_anchor_index(buffered_candles, visible_start)`, the index of the
  most recent *major extreme* (lowest low / highest high, whichever is more
  recent) within `_STRUCTURAL_ANCHOR_REGION = 300` candles before the visible
  window. A fixed candle offset would land the `NEUTRAL`→first-break bootstrap on
  whatever pivot sits there, inheriting a stale far-back regime (e.g. a
  months-old downtrend carried into a window that has since clearly reversed) and
  producing a late, wrong-direction first CHoCH; anchoring at the move's
  structural origin seeds the trend from the price action actually entering the
  window, while staying stable across refreshes (a major extreme is a fixed price
  point, not a sliding offset). The anchor falls back to `0` when the provider
  returns no pre-visible buffer. Both `market_structure_events`,
  `internal_structure_events`, and `poi_zones` are filtered
  to the calendar range `[candles[0].timestamp, candles[-1].timestamp]` after
  detection (`poi_zones` by `created_at`). `candles` (the visible window, the
  trailing `limit` of `buffered_candles`) is unaffected.

  After all detectors run, `ManipulationCycleDetector().detect(candles,
  all_structure, liquidity_zones, volume_delta_series(candles))`
  populates `manipulation_cycles`.

  `BehaviorDivergenceAnalyzer().analyze(candles, vd, liquidity_zones,
  all_structure)` populates `behavior_divergences`.

  `LiquidityHeatmapEngine().build(...)` populates `liquidity_heatmap`. A
  separate `futures_provider` arg (`FuturesDataProvider | None`, defaults to
  `BinanceFuturesDataProvider()`) fetches open interest / funding /
  long-short ratio **once** (`_fetch_futures_state`, OI requested with
  `limit=limit` so the paginated history spans the visible window, capped by
  Binance's ~30-day OI retention). The state feeds both
  `LeverageLiquidationEstimator().estimate(...)` (→ `liquidation_map`; it
  receives only the tail `_LIQUIDATION_OI_POINTS = 500` OI points so its
  `open_interest_change_pct` horizon is unchanged) and
  `OIRegimeAnalyzer().analyze(candles, open_interest,
  internal_structure_events)` (→ `oi_analysis` — the internal events are the
  ones the chart renders, so the qualified events match the drawn labels).
  The fetch is wrapped in try/except `DataProviderError`: a symbol with no
  perpetual contract, or an unreachable venue, degrades to
  `liquidation_map=None` **and** `oi_analysis=None` rather than failing the
  whole snapshot. Tests must inject a fake `futures_provider` to avoid
  network.

  Finally, `NarrativeEngine().build(data)` synthesizes all outputs into a
  `MarketNarrative` (timeline, anomalies, phase-dependent summary,
  confluence count), and `LiquidityHuntEngine().build(data)` synthesizes the
  `LiquidityHuntState`. Both run last via `dataclasses.replace` since they
  depend on the fully assembled `DashboardData`.

- **`app/narrative.py`** — `NarrativeEngine`: composition-level synthesizer
  that builds a `MarketNarrative` from a completed `DashboardData`. Lives in
  `app/` (not `psychology/`) because it depends on outputs from every layer.
  `build(data) -> MarketNarrative` produces:
  - **Timeline**: chronological `list[NarrativeEvent]` mapped from structure
    events (major + internal BOS/CHoCH/SWEEP), manipulation cycle phases
    (consolidation/sweep/expansion), and behavior divergences. Deduplicated
    by `(timestamp, event_type)`, keeping the higher-priority source
    (`manipulation_cycle` > `behavior_divergence` > `market_structure`).
  - **Anomalies**: `list[NarrativeAnomaly]` detecting pattern contradictions:
    expansion + exhaustion (HIGH), accumulation + distribution (MEDIUM),
    concentrated liquidity on one side (MEDIUM/HIGH), unconfirmed CHoCH
    (MEDIUM), BOS without sustained VD (MEDIUM).
  - **Phase**: the `ManipulationPhase` of the latest active cycle, or `None`.
  - **Summary**: phase-dependent institutional tone incorporating retail bias,
    HTF alignment, and VD context. Phases: neutral, accumulation
    ("smart money absorbing supply"), manipulation ("stops swept, cascading
    liquidation, retail trapped"), expansion ("impulsive move, sustained VD"),
    failed ("expansion failed to materialize, cycle invalidated").
  - **Confluence**: `(count, total)` — how many detection layers agree on
    direction (structure, manipulation cycle, behavior divergence, HTF).

- **`app/liquidity_hunt.py`** — `LiquidityHuntEngine`: composition-level
  synthesizer that builds a `LiquidityHuntState` from a completed
  `DashboardData` (like `NarrativeEngine`, it lives in `app/` because it
  cross-references structure, liquidity, and psychology outputs).
  `build(data) -> LiquidityHuntState`:
  - **Current-TF trend**: replays `internal_structure_events`
    (non-provisional BOS/CHoCH set the trend, `CHOCH_FAILED` reverts it;
    pivot labels/sweeps ignored) into a trend + flip timestamp (the event
    that started the current corrective leg). Counter-trend = that trend
    opposes `higher_timeframe_direction` (the existing
    `_HIGHER_TIMEFRAME_MAP` pair) → `hunted_side` SHORT under a bullish HTF,
    LONG under a bearish one; aligned/neutral → phase `NONE`.
  - **Targets**: equal-highs zones (hunted shorts) / equal-lows (hunted
    longs) — intact if unmitigated and beyond price, captured if
    `invalidated_at >= flip` (older sweeps are excluded, they belong to prior
    legs) — plus `LiquidationBand`s on the hunted side (`BUY_SIDE` above for
    shorts), live (`end_time=None`, beyond price) = intact, `end_time >= flip`
    = captured; bands clustered within 0.4% are one pool (strongest member
    represents it, intact while any member is live). The "nearby" bound is
    **volatility-normalized** (as of 2026-07-06): `proximity_atr` (wired
    **2.0** via `_HUNT_PROXIMITY_ATR`) × the visible series' mean true-range%
    of price, falling back to `proximity_pct` (default `0.02`) when unset or
    the series is under 2 candles. The fixed 2% was ~6 ATR on a calm BTC 15m
    (mapping too many pools for the strict all-captured gate to ever clear)
    but under 0.5 ATR on a volatile daily (mapping none — AAVE 4h sat at
    "hunting 0/0" forever; with N=2 it reads an honest captured 3/3, and ETH
    1d gets a map at all). N=3 measured worse (pulled a ~3-ATR pool into SOL
    4h and regressed its conclusion).
  - **Evidence**: `last_flush_timestamp` = latest `OIQualifiedEvent` with
    `participation=FLUSH` in the capture direction since the flip;
    capture-side `LIQUIDITY_SWEEP` since the flip; `oi_unwinding` =
    `current_regime` is `SHORT_COVERING` (hunted shorts) /
    `LONG_LIQUIDATION` (hunted longs).
  - **Phase**: `CAPTURED` when all mapped pools are captured (`captured_at` =
    last capture); any capture / flush / sweep / unwinding →
    `HUNT_IN_PROGRESS`; else `COUNTER_TREND`. With zero mapped pools the state
    never reaches `CAPTURED` (conservative). Captures are confirmed on **closed
    candles only** (a pool swept by the still-forming last candle stays pending
    until it closes), and `oi_unwinding` is **evidence only** — it no longer
    gates `CAPTURED`, so a live OI regime flickering between polls can't
    un-capture a structurally finished hunt (fixed 2026-07-21; the
    CAPTURED ⇄ HUNT_IN_PROGRESS churn seen live). It still keeps a
    not-yet-captured leg in `HUNT_IN_PROGRESS`.
  - **Capture signals** (`_collect_capture_signals`, shared by the
    counter-trend hunt and the aligned continuation stream): weighted
    `(timestamp, weight, source)` evidence clustered by proximity, where a
    cluster reaching the layer's threshold is a grab. Besides `sweep`, `vsa`,
    `oi_flush`, `zone`, `raid` and `realignment`, a **`supertrend`** source
    (weight `_WEIGHT_SUPERTREND` = 3) fires on a capture-direction
    `SupertrendBreak` with `quality=STOP_RUN` — the band's stops taken and
    handed back, the raid signature against the population resting on the
    Supertrend rather than on equal levels. It is a *floor signature*
    (`_FLOOR_SIGNATURE_SOURCES`), so it satisfies the continuation stream's
    `require_vsa` gate, and unlike `raid` it is **not** disabled there: the
    band moves with price (no stale-level problem) and the analyzer already
    gates it on a 1-ATR excursion. Two raid-*shaped* sources
    (`_RAID_SHAPED_SOURCES` = `raid` + `supertrend`) in one cluster still need
    a partner from another family — near a moving band a stale equal level is
    usually the same wick told twice. Measured 2026-07-24 across
    BTC/ETH/SOL/NEAR × 15m/1h/4h: continuation episodes **105 → 119** (+13%,
    gains in 10 of 12 combos, none removed), hunt episodes 133 → 134, live
    `phase` unchanged in all 12. The continuation stream gains most by
    construction — its floor signature was VSA-only (`require_vsa=True` with
    `allow_raid=False`), the documented narrowness that left whole legs
    unmarked.

- **`app/overview.py`** — multi-timeframe structural overview (the sidebar
  "Structure Ladder", as of 2026-07-11). Split in two stages so the API can
  cache each timeframe independently:
  - **`load_timeframe_structure(provider, symbol, timeframe, limit,
    confluence_filter)`** — the cacheable I/O unit for one timeframe. Runs the
    exact production internal-structure pipeline `load_dashboard_data` uses
    (the shared **`dashboard_data._run_internal_structure`** helper: buffered
    fetch, structural anchor, per-TF detector wiring via
    `_build_internal_detector`, both composition passes) plus equal-level zone
    detection + `mark_swept_zones`, returning a `TimeframeStructureSnapshot`
    (candles, visible events, `final_trend`, EQL zones).
  - **`build_overview(symbol, snapshots)`** — pure assembly into a
    `core.domain.MarketOverview` of `TimeframeOverview` entries: per timeframe
    the detector's `final_trend` (**exactly the trend the chart renders** —
    same pipeline), the last non-provisional BOS/CHoCH/`CHOCH_FAILED` (+
    direction, timestamp, candles-ago), any provisional live-edge
    `BOS?`/`CHoCH?` as `forming_event` (the fizzle `CHOCH_FAILED` marker is
    excluded — provisional but not "forming"), and a `LiquidityHuntEngine`
    summary (phase, hunted side, captured/total) computed against the
    `_HIGHER_TIMEFRAME_MAP` anchor's trend **from the same snapshot batch**
    (no duplicate HTF fetches; W1 or a missing anchor degrades to the entry's
    own trend = "aligned", the `load_dashboard_data` fallback). The hunt runs
    on a slim `DashboardData` (EQL zones + events only; `liquidation_map` and
    `oi_analysis` deliberately `None` — the documented graceful degradation,
    so ladder hunt phases are structure+EQL-based; the full OI-qualified hunt
    stays on `/api/dashboard`).
  - **`load_overview(provider, symbol, timeframes, limit, confluence_filter)`**
    composes both over the default ladder `OVERVIEW_TIMEFRAMES` (M5→W1).
  Purely descriptive throughout: a state reading per timeframe, not a signal.

`default_ohlcv_provider()` and `default_futures_provider()` are **memoized**
(`lru_cache`), so the whole process shares one instance of each. A fresh
`BinanceFuturesDataProvider` re-downloads Binance's ~1MB `fapi/v1/exchangeInfo`
the first time a ccxt *unified* method loads its markets, and the futures state
is fetched per dashboard load: measured 2026-08-20, the futures fetch costs
1.1-1.5s with a per-request provider versus 0.60s warm on a shared one, and a
cold overview (seven timeframes) plus a dashboard load fired several of those
1MB downloads concurrently. The klines path is unaffected either way (the
implicit `publicGetKlines`/`fapiPublicGetKlines` endpoints never load markets —
measured, the ladder costs ~1s both ways). The providers hold no per-request
state, only stateless public GETs, the same property that lets the prefetch
pool share them across threads.

`load_dashboard_data`, `_run_internal_structure` and `load_timeframe_structure`
also accept **`anchor_hint`** (default `None`): the structural anchor a previous
call for this symbol/timeframe used, which `_structural_anchor_index` keeps
while that candle is still inside its region. The anchor the run chose comes
back as `DashboardData.structural_anchor` /
`TimeframeStructureSnapshot.structural_anchor`. The state itself lives in
`api.anchors` — with no hint every one of these is byte-identical to the
stateless pipeline, so a replay or a fixture reproduces exactly.

`load_dashboard_data` also accepts **`compute_narrative`** (default `True`;
`False` skips the `NarrativeEngine` synthesis entirely, `narrative=None`) and
its buffered-fetch + internal-detection front half now lives in
`_run_internal_structure` (returning an `InternalStructureRun`), shared with
the overview and the HTF-trend run so all three stay byte-identical;
`default_ohlcv_provider()` builds the production fallback provider chain.

`DashboardData`, `LiquidityHuntEngine`, `NarrativeEngine`,
`ScoredLiquidityZone`, `TimeframeStructureSnapshot`, `OVERVIEW_TIMEFRAMES`,
`build_overview`, `load_overview`, and `load_timeframe_structure` are
re-exported from `liquidity_hunter.app`.

