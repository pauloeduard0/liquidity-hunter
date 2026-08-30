# React frontend

Extracted from `CLAUDE.md` (2026-08-29) to keep that file under its size limit.

### React frontend (`frontend/`)

A React + TypeScript + Vite project (Tailwind CSS, Lightweight Charts v4),
separate from the Python package, that polls `GET /api/dashboard` and renders
the dashboard data.

The React frontend has a professional TradingView-style dark UI with a
`Logo` component, `StatusBar` (live connection indicator, candle/event
counts, clock), `LoadingSkeleton`, and header with symbol badge + timeframe
selector.

- **`frontend/src/components/MainChart.tsx`** — `MainChart` component:
  renders three synced Lightweight Charts panes (main candlestick, volume
  delta histogram, RSI indicator) with synchronized time scales and
  crosshairs. The main pane overlays top-ranked liquidity zone lines, draws
  BOS/CHoCH/SWEEP horizontal lines and labels (plus a grey `CHoCH ✕` line at
  the broken origin for `choch_failed` events), renders POI order block boxes
  via `POIBoxesPrimitive`, and renders manipulation cycle accumulation boxes
  via a second `POIBoxesPrimitive` instance (toggled via
  `showManipulationBoxes` prop). Accumulation boxes are color-coded by
  status: amber (`in_progress`), green (`confirmed`), gray (`failed`).
  Limited to `MAX_MANIP_BOXES = 3` most relevant (in-progress first).

  **Consolidation range boxes**: a third `POIBoxesPrimitive` instance draws
  each `data.consolidation_ranges` entry as a neutral slate `▭ RANGE` box
  (`CONSOLIDATION_BOX_STYLES`, live ranges slightly stronger than resolved
  ones, resolved boxes labeled with the breakout direction arrow); a live
  range extends to the right edge via the far-future-sentinel clamp. Toggled
  by the `▭ Range` toolbar button in `App.tsx` (`showConsolidationRanges`
  prop, default **on**). Range boxes do **not** terminate BOS/CHoCH lines —
  a truncate-at-range-start variant was built and reverted on visual review
  (2026-07-14): the reference lines must keep running through the box, and
  the stale-line problem is solved by the staged breakout event that ends
  them at the range's resolution instead.

  **Behavior divergence markers + arcs**: `distribution`
  divergences draw arrow markers (`buildDivergenceMarkers`,
  `DIVERGENCE_MARKER_SHAPES`; `accumulation` is no longer emitted, see the
  analyzer), but `exhaustion`/`absorption` (`DIVERGENCE_ARC_TYPES`)
  are drawn as **curved arcs** instead (`buildDivergenceArcs` →
  `DivergenceArcPrimitive`): a dome above the candle **high** for a top reading,
  a bowl below the candle **low** for a bottom one. The side is resolved per
  type — `exhaustion.direction` is the fading trend (bullish → top → dome
  above), `absorption.direction` is the net flow (bullish → buyers absorbing at
  support → bowl below), so the two map oppositely. Arc width tracks
  `barSpacing` (`ARC_HALF_BARS = 4` candles each side, clamped 20–90px) so it
  scales with zoom; `GAP`/`ARCH` are fractions of the width. Both marker groups
  toggle with `showDivergenceMarkers` (the arcs ignore `showVsaMarkers`).

  **VSA-confluence reinforcement**: a frontend-only cross of the two volume
  layers — VSA (single-candle anatomy) and behavior divergence (window flow)
  read the same reversal at different time resolutions, so they rarely coincide
  on the exact candle but often within a few bars. When a **same-side** VSA
  reversal pattern (`VSA_PATTERN_SIDE`: climax/thrust/no-supply-demand →
  above/below) sits within `CONFLUENCE_WINDOW_BARS = 3` candles of a divergence
  arc, the arc is flagged `strong` and drawn **reinforced** (thicker stroke,
  translucent halo, a `✦` badge at the apex). Neither base layer is modified —
  the badge surfaces the rare strong agreements while each layer's own noise
  recedes. Computed from `data.volume_spread_signals` regardless of the VSA
  marker toggle.

  **Supertrend band**: `data.supertrend` is drawn on the main pane as one
  `LineSeries` per same-trend run (green floor while bullish, red ceiling while
  bearish, a break at each flip mirroring Pine's `plot.style_linebr`). Lines
  only — the flip reads from the break between runs, so it carries no marker.
  Toggled by the `⌁ ST`
  toolbar button in `App.tsx` (`showSupertrend` prop, default **off**);
  colors/width live in `theme.ts` (`SUPERTREND_*`). Under the same toggle,
  each `data.supertrend_breaks` entry with `quality === 'stop_run'` adds a
  purple `⚠ ST` marker at the flip candle plus a dashed purple segment along
  `broken_level` from the break to its `reclaim_timestamp` — the shape of
  "broke out, took the stops, handed it back". `genuine` and `unknown` breaks
  draw nothing extra: marking the normal case would bury the exceptional one.

  **VWAP**: `data.vwap` is drawn on the main pane as one gold `LineSeries` per
  accumulation (`buildVwapSegments` splits the points on `anchor_timestamp`, so
  the UTC rollover breaks the line instead of drawing a jump nobody paid), with
  its ±1σ/±2σ bands as thin dotted lines. Each `data.anchored_vwaps` entry adds
  a dashed cyan/violet line titled `VWAP <label>` (`VWAP CHoCH ▼`, `VWAP
  Sweep ▲`), with `lastValueVisible` on so the price scale carries that crowd's
  break-even. The `⌀ VWAP` toolbar button in `App.tsx` **cycles the periodic
  VWAP through three states** on plain click (`vwapMode: VwapMode`, default
  `'off'`, walked by `VWAP_MODE_CYCLE`): `off → line → line + bands → off`,
  the band state shown as `⌀ VWAP σ`. The average is the reading (a
  population's break-even); the ±σ bands only describe dispersion, so on a pane
  already carrying the structure staircase, POI boxes and liquidation pools
  they sit behind a deliberate third press instead of riding along with the
  line. Alt/Shift-click still toggles the anchored ones
  (`showAnchoredVwap`, shown as `⌀ VWAP ⚓`) — the same modifier
  pattern as `▤ VP`. Colors in `theme.ts` (`VWAP_*`).

  **Volume delta pane**: histogram bars colored by candle direction
  (`CANDLE_UP_COLOR`/`CANDLE_DOWN_COLOR`), computed as
  `2 * taker_buy_volume - volume` per candle.

  **Control oscillator pane** (CVD×OI): a 4th synced pane (order: main, delta,
  control, rsi — RSI stays the bottom pane carrying the time axis) drawing
  `data.market_control.series` as a signed histogram (`control_score`,
  -100..100), colored by `regime` on **two channels**
  (`CONTROL_REGIME_COLORS`): the *hue* is the aggressor side (green buying /
  red selling), the *fill* is whose money is behind it — solid for the OI-rising
  buildup quadrants, the same hue at ~30% alpha ("hollow", since a histogram
  series has no stroke) for `short_covering`/`long_liquidation`. So a rally
  carried by shorts covering draws as a washed-out green bar: price is being
  pushed, but by participants leaving, not arriving — the exhaustion signature
  that reads identically to a dead tape when colored by `controller` alone.
  Grey `flat` unchanged. Measured 2026-08-18 (BTC/SOL × 15m/1h/4h): the exit
  quadrants are 8-11% of intraday candles, comparable to the buildup ones, so
  the channel roughly doubles the pane's non-grey information (H4 stays ~90%
  grey — Binance's ~30-day OI retention, not a dead channel).
  "Who is in control, how strongly, and with whose money" in one bar. It carves its slice
  out of the main pane (`CONTROL_CHART_RATIO`) and participates in the
  logical-range time sync but not crosshair sync. Toggled by the `⚑ Control`
  toolbar button (`showControlOscillator` prop); turning it on
  also opens the indicator panes (it lives in that group, hidden when they're
  minimized). The button is **disabled, and the pane forced closed, whenever
  `data.market_control` is null** (`controlAvailable` in `App.tsx`) — an
  on-chain pool has no open interest, and neither does a spot-only pair, so
  both axes of the reading are missing and the pane would render empty. The
  gate reads the field rather than the symbol, so it is right for every source;
  the `◈ Tide` toggle honours it too (it no longer opens a pane with nothing in
  it, though the ribbon itself still draws, grey, on the main pane).

  **RSI pane**: RSI(14) line with 70/30 reference lines and regular
  divergence detection (bullish: price LL + RSI HL below 50; bearish:
  price HH + RSI LH above 50). Divergence lines drawn as colored
  `LineSeries` overlays.

  **BOS/CHoCH line rendering**: each event draws a horizontal line to the next
  event that terminates it (`structureLineEndTime`): BOS lines end at the next
  same-direction BOS, opposite-direction (non-failed) CHoCH, or same-direction
  real `choch_failed` (the ✕ that invalidated the leg the BOS extended and
  reverted the trend — a leg that dies by failure has no opposite CHoCH to end
  its BOS, the BTC 1D 2026-05 case); CHoCH lines at
  the next opposite-direction CHoCH (so a reversal clears stale references
  rather than letting them run to the chart edge), at the next
  opposite-direction BOS (a BOS only fires with the standing trend, so it is
  positive proof the reference is spent — without this a *failed* opposite
  CHoCH, excluded as "never took hold", let the old line run through a whole
  counter-move that had already printed real BOS: BTCUSDT H1 2026-07), and also at the next
  same-direction BOS whose reference sits on the *wrong side* of the CHoCH's
  level (below it for a bullish CHoCH, above for bearish): the trend collapsed
  through the level and rebuilt from the other side — an excursion whose
  opposite CHoCHs all failed stays transparent above, yet the old reversal
  reference is stale (the ENA 4H 2026-06 line at 0.086 running to the edge
  across a dive to 0.070). A normal leg's staircase only moves away from the
  CHoCH level, so this never fires mid-trend. **Both BOS and CHoCH lines
  are drawn at `reference_price_level`** (the level that was broken — the formed
  swing extreme for BOS, the validated swing for CHoCH), not `price_level`,
  since the breaking pivot's extreme can be far beyond the confirmed level. Both
  also **start at `reference_timestamp`** (the candle that *formed* the broken
  level — the prior swing extreme for BOS, the promoted LH/HL for CHoCH), so the
  line runs from the level's origin to where it was broken rather than starting
  at the break. SWEEP lines are drawn at `price_level` (the sweep wick's
  extreme), starting at the event `timestamp`. A CHoCH with
  `reference_structural === false` (a weak reference — re-anchor/fallback level
  or wick-only-break promotion, barrier-governed) renders **dotted and dimmed**
  (`SparseDotted`, color + `99` alpha) with a `*` label suffix (`CHoCH* ▼`),
  so a conservative-sequence CHoCH (structural leg origin, solid dashed
  `CHoCH ▼`) is distinguishable at a glance. A BOS **or CHoCH** with
  `provisional === true` (a live-edge continuation whose floor closed-broke, or a
  live-edge reversal whose structural reference was sustained-closed-broken, but
  whose confirming pivots have not formed yet) gets the same dimmed/`SparseDotted`
  treatment with a `?` suffix (`BOS? ▼` / `CHoCH? ▼`), so it reads as "forming"
  until the confirmed event supersedes it (or it vanishes if the move fails).
  Provisional marks are also excluded from line *termination*
  (`!other.provisional` in `structureLineEndTime`): a forming mark never truncates
  a confirmed BOS/CHoCH line — it only draws its own dimmed line to the edge.

  What a provisional BOS *is* was measured and is **deliberately not drawn**: it
  marks extension, not continuation. With an honest entry — the candle the mark
  first appears on, dated by incremental replay, so no lookahead — across six
  symbols × 15m/1h/4h it closes in its own direction 35-45% of the time against
  a direction-matched control's 51%, below control in all six symbols, while its
  MFE/MAE runs 1.27-1.44 against the control's 1.05
  (`research/provisional_edge.py`). Price travels its way and hands it back,
  which is what the mark is built to do — it fires on the close *beyond* the
  staircase floor, the most extended point of the leg. (Magnitude caveat: the
  gap is largest on BTC/ETH and near zero on XRP/LINK, and six majors over one
  ~15-day window are not six independent samples.) A `·extended` label suffix
  was built and **reverted on visual review** (2026-08-21): a word among glyphs
  on a pane already carrying the staircase, POI boxes and sweeps is clutter, and
  the mark reads as information rather than as a call without it. `CHoCH?` never
  had one — it is weak on both axes (37-43% hit rate, MFE/MAE 0.69-1.00) rather
  than asymmetric.

  A mark's **age** is likewise measured and not drawn. A mark carries the
  timestamp of the candle that *broke* the level, but the pipeline only emits it
  once the confirming pivot has formed — a median of **13.5 candles** later for
  a BOS and **9** for a CHoCH, measured by incremental replay
  (`research/event_lag.py`, p90 30 and 19; provisional marks are live-edge by
  construction, median age 1 candle). So the newest mark on the pane is never as
  new as it looks. A `·12c` age suffix on recent confirmed marks was built and
  **reverted on visual review** (2026-08-21, with the `·extended` suffix above):
  an extra chip on every recent label clutters a pane that already carries the
  staircase, POI boxes and sweeps, and the mark's position along the time axis
  already places it. The lag is real and worth knowing when reading structure —
  it is simply not worth ink on the chart.
  A **re-fired (re-activated) CHoCH** — one whose `reference_timestamp` sits
  exactly on a prior same-direction real `choch_failed` (the re-arm pivot
  carries the failure's timestamp by construction) — renders with a `↻`
  suffix (`CHoCH ↻ ▼`), and because its line starts at the failure, a
  CHoCH → ✕ → re-fire cycle draws as consecutive segments along the level
  rather than overlapping full-span lines. A **fizzle marker** (provisional
  `choch_failed`) draws **no line of its own** — label only, anchored at the
  reclaim candle — since the fizzled CHoCH still renders normally and its own
  line already stops at the reclaim (the marker's line would trace the same
  segment twice).

- **`frontend/src/components/ManipulationCyclesPanel.tsx`** —
  `ManipulationCyclesPanel` sidebar component: renders manipulation cycle
  cards sorted by relevance (in-progress first, then confirmed, then failed),
  limited to `MAX_DISPLAY = 5`. Each card shows direction arrow, phase badge
  (`ACC`/`MANIP`/`EXP`), status indicator (`LIVE` with pulse animation,
  `CONFIRMED`, `FAILED`), target zone, consolidation candle count, sweep
  info, expansion BOS info, and volume delta. Includes a `CHART ON`/`OFF`
  toggle button that controls the `showManipulationBoxes` prop on `MainChart`.

- **`frontend/src/components/MultiTimeframePanel.tsx`** — the **Structure
  Ladder** sidebar panel (as of 2026-07-11, first panel in the sidebar): one
  compact row per `TimeframeOverview` entry (M5 → W1) showing the timeframe
  chip, trend (`▲ BULL` / `▼ BEAR` / `◆ FLAT`, directional colors), the last
  structural event with candles-ago (`BOS ▲ ·12c`), a dimmed forming chip for
  provisional marks (`BOS? ▼`), a slate `▭ RANGE ·Nc` chip when the
  timeframe is inside a confirmed consolidation (`in_consolidation` /
  `consolidation_candles`), and a hunt-phase chip (`⚠` counter-trend /
  `⚡ x/y` hunting / `✓` captured). The `CollapsibleSection` header shows an
  alignment summary (`6▲ 1▼`); the full reading is each row's hover title.
  **Clicking a row switches the chart timeframe** (`switchChartTimeframe`,
  the chart-only divergence — global panels stay on the selected timeframe).
  `App.tsx` polls `GET /api/overview` every `OVERVIEW_REFRESH_INTERVAL_MS =
  30s` per symbol (transient failures keep the last ladder rather than
  tearing the dashboard down). The `NarrativePanel` (which exists and renders
  whenever `data.narrative` is non-null) auto-hides now that `/api/dashboard`
  defaults `narrative=false` — re-enabling the query param brings it back
  with zero frontend changes.

- **`frontend/src/charting/POIBoxesPrimitive.ts`** — `POIBoxesPrimitive`
  implements `ISeriesPrimitive` and draws filled canvas rectangles for each
  POI zone. Colors: light blue (`#64b5f6` / `#2979ff`) for bullish demand
  zones, red (`#ef5350`) for supply zones. Box border: 1.5px. Active fill
  opacity: ~18% (`#2979ff2e`). Each box starts at the order block candle
  (`ob_candle_timestamp`) and its right edge extends to `invalidated_at` (the
  candle whose close broke the zone); while the zone is ACTIVE, a far-future
  sentinel timestamp is used so `timeToCoordinate` returns `null` and the
  right edge is clamped to `mediaSize.width` (full pane width).
  `selectVisiblePoiZones` keeps ACTIVE **order blocks** only: breaker/mitigation
  blocks sit a few ticks from the order block of the same MSB and double the ink
  for one observation (they stay in `poi_zones` for the API and the liquidation
  map's entry anchors). It applies no cap and no distance window — the
  detector's queue retirement leaves 0-5 zones per chart, so the chart draws
  exactly what survives rather than filtering the indicator a second time.
  Also reused for manipulation cycle accumulation boxes (second instance) and
  consolidation range boxes (third instance).

- **Leverage liquidation bands are no longer drawn** (removed 2026-08-19).
  `LiquidationBandsPrimitive` and the `⊟ Liq` toolbar button are gone. The
  bands are a *projection*, not observed data: the estimator takes the equal-level
  and order-block anchors already on the chart and offsets each by a fixed
  maintenance-margin distance per leverage tier, so as an overlay the layer is
  largely collinear with the levels it was derived from. Its only objective test
  agrees: reaction framing measured no edge (lift 0.89-0.99) and the target
  framing's 1.22x clustering lift was one symbol over 42 days, never broadened
  (see `docs/` and the backtest in `app/liquidation_backtest.py`). The
  `LeverageLiquidationMap` itself stays — it feeds `LiquidityHuntEngine`'s
  targets (`LiquidityHuntTargetKind.LIQUIDATION_BAND`), the defended-levels
  families, and the API/backtest — it is only the chart layer that was retired.

- **`frontend/src/charting/VolumeProfilePrimitive.ts`** —
  `VolumeProfilePrimitive` draws `data.volume_profile` as a thin-line histogram
  on the **right** of the main pane, growing leftward from an anchor near the
  price scale (`VP_RIGHT_MARGIN`) — the layout of the classic TradingView
  volume-profile studies. Default colouring mirrors the reference study: grey
  outside the value area, blue inside it, red at the POC, with POC/VAH/VAL lines
  running back over the lookback (from the profile's `start_timestamp`) and
  stopping `VP_VA_LINE_GAP` short of the band they point at. Colors in
  `theme.ts` (`VP_*`).

  **Both axes follow the chart's zoom.** Vertically the bands sit at their price
  coordinates, so they track the price scale like any overlay. Horizontally the
  bands' length is measured in chart *bars* (`VP_MAX_LENGTH_BARS` × the time
  scale's `barSpacing`, bounded to `[VP_BAR_MIN_PX, VP_BAR_MAX_PX]` because this
  chart's default window is far wider than a typical TradingView view — 1200
  candles is ~1px per bar), so the profile widens and narrows with horizontal
  zoom the way the reference's bar-unit `scale_volume` does. `barSpacing` is
  read inside `renderer()`, which the library calls on every repaint, so this
  needs no zoom subscription. The profile's *scope* is unaffected: it stays the
  backend's fixed recent lookback and does not re-derive itself from whatever is
  on screen.

  **Adaptive band merging** (`mergeToVisibleBands`): the profile covers only its
  lookback's price range while the pane's scale spans the whole visible series,
  so on a chart showing far more history than the profile all 200 bands round to
  one pixel and the histogram reads as a solid block. Adjacent bands are merged
  until each renders at least `VP_MIN_BAND_PX` (2.5) tall — volumes add, and a
  merged band inherits value-area/POC membership from any member so the POC is
  never swallowed. On BTC/NEAR H4 with the dashboard's 1200-candle window this
  groups 4 buckets per band; at 300 visible candles, 2; on M5, none.

  A second **delta mode** colours bands by the aggressor side instead
  (`VP_DELTA_*`). That split is inferred per candle rather than observed per
  trade (`VolumeProfile.delta_estimated`), so it sits behind a modifier-click
  rather than being the default picture. The `▤ VP` toolbar button in `App.tsx`
  toggles visibility on plain click (`showVolumeProfile`, default **off**) and
  swaps to delta colouring on Alt/Shift-click (shown as `▤ VP Δ`), the same
  pattern the retired `⊟ Liq` button used.

  The anchor is the pane's right edge rather than a bar offset into the future
  (the reference study's `vp_right_offset`): the panes here are synced by
  logical range, so reserving future space would have to move every pane.

- **`frontend/src/utils/tideRibbon.ts`** + **`frontend/src/charting/RibbonPrimitive.ts`**
  — the **Tide** reading (as of 2026-07-30), the project's own composite,
  derived entirely client-side from data `/api/dashboard` already returns.
  The layers are of three different natures — structure is a *state*, control
  a *windowed measurement*, VWAP a *place* — so they are never averaged; each
  takes a visual channel on one geometry (the Saty pattern):
  the **envelope** (VWAP ±1σ) is the shape, the **hue** is the internal
  detector's standing trend (replayed by `structureTrendByCandle`, the same
  rule the backend uses: provisional marks never mutate it, `CHOCH_FAILED`
  reverts), and the **saturation** is `market_control`. A coloured but washed-out
  ribbon is the reading: price trending structurally with nobody paying for it.
  Drawn at `zOrder 'bottom'`, behind the candles; the midline (the VWAP itself)
  is dashed while no side is *credited* with control.
  Saturation reads `control_score` normalized against the **window's own p90**
  (`convictionScale`), not `controller !== 'balanced'`: the credited-controller
  flag fires on 13% of BTC 15m candles but 1% of BTC 4h and 4% of SOL 1h — a
  channel that never varies carries nothing — while the score has usable range
  (median |score| 6-13, p90 21-39). The cost is that saturation is *relative to
  the visible window*, not comparable across symbols. Where there is no OI at
  all the ribbon is grey — honest, but note Binance's ~30-day OI retention
  leaves only ~15% of a 1200-candle H4 window covered (measured 2026-07-30;
  within covered candles conviction is a uniform 0.26-0.34 median on every
  timeframe, so the greyness is missing data, not a dead channel).
  `buildPhase` feeds a **phase line** drawn over the control histogram on the
  existing control pane — the "two overlaid" geometry, where the *gap* between
  a stretched line and short grey bars is an extension nobody is funding.
  0 = VWAP, ±50 = ±1σ, measured against the band on the side price actually
  sits (a volume-weighted deviation over a skewed accumulation is not centred).
  Clamped at **±150**: |phase| > 100 happens on 9-13% of candles (pinning one
  bar in eight throws away resolution) while > 150 is 0.9-2.6%, a real tail.
  `MIN_SPAN_FRAC` skips candles whose accumulation has near-zero dispersion —
  a fresh anchor divides by σ≈0 and produced readings of 4.3e6 on BTC 15m, one
  of which flattens the pane's autoscale.
  Toggled by the `◈ Tide` toolbar button in `App.tsx` (`showRibbon`, default
  **off**); turning it on also opens the control pane, since half the reading
  lives there. Purely descriptive — a measurement of this project's own
  sweep/raid events across 16 symbols × 3 timeframes found no entry trigger
  worth encoding (`research/raid_reversal.py`), so Tide describes state and
  never signals.

- **`frontend/src/utils/format.ts`** — every price on screen (the OHLC
  readout, KPI cards, sidebar panels, chart labels) goes through `formatPrice`,
  which by default keeps ~5 significant digits so a low-priced pair (ETHBTC
  ~0.03) doesn't collapse onto a 2-decimal grid. An **on-chain symbol is
  charted by market cap**, though, where that rule renders `7,166,059.96` — so
  `setPriceFormatMode(symbol)` (called by `App` during render, driven by the
  symbol rather than the data so it is right while a snapshot loads) switches
  those to an abbreviated scale: `7.17M` / `500.00K` / `1.23B`, two decimals
  throughout (~10 market-cap units of resolution at the 500K end, enough to
  separate two structure levels). Module state, for the same reason
  `chartTime` keeps its offset that way — a chart mixing `7.17M` with
  `7,166,059.96` reads as two instruments. `MainChart.seriesPriceFormat`
  declares the same scale on the candlestick series (`type: 'custom'`), since
  the price axis and crosshair label are formatted by the series, not by
  `formatPrice`. `isOnchainSymbol` mirrors the backend's `is_onchain_symbol`.

- **`frontend/src/utils/chartTime.ts`** — the chart's timezone (as of
  2026-07-18). Lightweight Charts has no timezone support and renders every
  `UTCTimestamp` in UTC, so a 15m candle printed at 21:30 in São Paulo labeled
  00:30 the *next* day — a silent three-hour offset that twice sent a structure
  review chasing the wrong candle. `toChartTime(iso)` (the single conversion
  point for *every* chart time — candles, overlays, primitives, and the pure
  helpers in `MainChart`; hence the name, the result is a chart coordinate, not
  a real UTC timestamp) shifts each timestamp by **its own** local UTC offset, so
  candles either side of a DST transition each get the right one. **Daily and
  weekly are exempt** (`setChartTimezoneMode`): their timestamp *is* the exchange
  day (00:00 UTC), and shifting would relabel the 14 Jul daily bar as
  "13 Jul 21:00". The mode is module state because all chart times must share one
  offset to stay mutually consistent (the helpers' time comparisons are
  shift-invariant only while the shift is uniform); `MainChart` sets it during
  render and `App` remounts the chart on every symbol/timeframe change.
  `chartTimezoneLabel(timeframe)` feeds the toolbar chip next to the OHLC
  readout (`UTC-3` intraday, `UTC` on D1/W1), so which clock the chart speaks is
  never a guess.

- **`frontend/src/types/dashboard.ts`** — TypeScript types mirroring the API
  schema; includes `POIZone`, `MarketStructure` (with
  `reference_timestamp`, `reference_structural`, `provisional`),
  `ManipulationCycle`, `ManipulationPhase`,
  `ManipulationCycleStatus`, `BehaviorDivergence`, `DivergenceType`,
  `LiquidityHeatmap`, `HeatmapBucket`, `LeverageLiquidationMap`,
  `LiquidationBand`, `MarketNarrative`, `NarrativeEvent`, `NarrativeAnomaly`,
  `NarrativeEventType`, `AnomalySeverity`, `OIAnalysis`, `OIRegimeReading`,
  `OIQualifiedEvent`, `OIRegime`, `OIParticipation`, `LiquidityHuntState`,
  `LiquidityHuntTarget`, `LiquidityHuntPhase`, `LiquidityHuntTargetKind`,
  `ConsolidationRange`, `ConsolidationStatus`, `VolumeProfile`,
  `VolumeProfileBucket`, `VolumeNode`, `VWAPSeries`, `VWAPPoint`,
  `VWAPAnchor`;
  `DashboardData.higher_timeframe` (`TimeFrame | null`) and
  `DashboardData.consolidation_ranges`; `TimeframeOverview.in_consolidation`
  / `consolidation_candles`.

- **Liquidity Hunt KPI card** (frontend, as of 2026-07-06): the KPI row reads
  left-to-right as a story ending in the hunt "conclusion" card — the
  **Price card was removed** (price remains visible in the chart toolbar
  OHLC). The grid is `md:grid-cols-6` (`LoadingSkeleton` matches): Retail
  Bias, Dominant Liquidity, HTF Trend, OI Regime, **Who's in Control**,
  **Liquidity Hunt**. The **Who's in Control** card (`controlCardProps`, from
  `data.market_control`) is the CVD×OI read: `buyers` → `▲ Buyers` (green,
  badge `⊕ NEW LONGS`), `sellers` → `▼ Sellers` (red, `⊕ NEW SHORTS`),
  `balanced` → `▲ Shorts covering` / `▼ Longs exiting` (amber, badge
  `⊖ UNWINDING`) in the unwinding quadrants, plain slate `◆ Balanced` only when
  genuinely flat — the domain `controller` stays `BALANCED` (no fresh money =
  no control), the direction is named in presentation only; sub-line `CVD ±x% · OI ±y% · conviction N`, full `description` on
  hover. `huntCardProps` in
  `KpiRow.tsx` maps `data.liquidity_hunt.phase` to presentation:
  `none` → `◆ —` / "structure aligned with HTF"; `counter_trend` →
  `Shorts = liquidity` (red, badge `⚠ INTACT`); `hunt_in_progress` →
  `Hunting shorts` (amber, badge `⚡ ACTIVE`); `captured` →
  `Shorts captured` (green, badge `✓ CLEARED`, capture time in the
  The badges **describe the observation and no longer instruct** (changed
  2026-08-21; they read `⚠ DON'T FADE` / `⚠ UNWIND` before). The instruction was
  measured and did not hold: across eight symbols × 15m/1h/4h, at horizons of 5
  to 100 candles, a break with new money behind it never beat one carried by
  exit flow on the direction hit rate, and both sat at or below a control
  matched on symbol, timeframe and direction — `research/control_continuation.py`,
  entering at the candle where the mark actually becomes visible rather than at
  the timestamp it carries. The quadrant remains an accurate description of what
  just happened; it is not evidence about what happens next, and a badge phrased
  as an instruction reads as authority the number cannot back.

  sub-line). Sub-line shows `captured/total pools swept` plus
  `· OI unwinding` while the regime still burns the hunted side; the full
  engine `description` is the card's hover title. **Anchor chips** (as of
  2026-07-06, from `data.higher_timeframe`): the HTF Trend card label reads
  `HTF Trend · 4H` with sub `4H internal structure` (`top timeframe — own
  trend` when null), and every hunt sub-line ends in `· vs 4H` (the `none`
  phase reads `structure aligned with 4H`) — so an M15 card saying
  `Hunting longs · vs 1H` reads as the pair's fractal handoff (the bounce's
  buyers are the H1 correction's fuel), not a contradiction of the 4H story.

- **Hunt window chart shading** (frontend, as of 2026-07-06):
  `frontend/src/charting/HuntWindowPrimitive.ts` shades the liquidity-hunt
  window as a **full-pane-height vertical band** on the main pane (modeled on
  `POIBoxesPrimitive`, but a time span rather than a price box, and rendered
  at `zOrder 'bottom'` so it paints *behind* the candles and every overlay).
  `MainChart` fills it from `data.liquidity_hunt`: the band runs from
  `counter_structure_timestamp` (the counter-trend flip candle, dashed
  vertical edge) to `captured_at` when `phase === 'captured'`, or to the
  right edge via the far-future-sentinel clamp while the hunt is still
  running. Amber (`#ff9800`, ~5% fill) with a `⚡ hunting shorts|longs` label
  at the top while active; green (`#26a69a`) with `✓ shorts|longs captured`
  once concluded; nothing when `phase === 'none'`. Only the *current* hunt is
  drawn (the state is a live snapshot, not a history of past windows).
  Toggled by the `⚡ Hunt` toolbar button in `App.tsx` (`huntWindowVisible` →
  the `showHuntWindow` prop on `MainChart`), **off by default**. Independently
  of the toggle, the structure label of the **flip event itself** — the
  non-provisional BOS/CHoCH/`CHOCH_FAILED` whose timestamp equals
  `counter_structure_timestamp` while the hunt phase is not `none` — gets a
  `⚠` suffix (`CHoCH ▼ ⚠`): the entrants of that break are the resting
  liquidity being hunted. Only the *standing* flip is marked; historical
  events would need the HTF trend as of their own time, which a snapshot
  does not carry.

- **OI regime surfaces** (frontend): `KpiRow` renders an **"OI Regime"**
  card (grid is `md:grid-cols-5`; the `LoadingSkeleton` in `App.tsx` matches)
  from `data.oi_analysis.current_regime` — regime label + price-direction
  icon, directional colors for the buildup regimes and amber for the
  unwinding ones, sub-line `OI ±x% · Px ±y%`, and a badge: `✓ CONFLUENT` /
  `⚠ DIVERGENT` compares a buildup regime's conviction direction against the
  HTF trend, `⚠ UNWIND` flags covering/liquidation regimes; `—` when
  `oi_analysis` is null (spot-only symbol). `MainChart` appends an OI
  participation suffix to structure event labels via
  `OI_PARTICIPATION_SUFFIX` (`⊕` new money, `⊖` covering, `⚡` flush; FLAT
  adds nothing), keyed by `event_timestamp|event_type` from
  `oi_analysis.qualified_events`.
- **`frontend/src/theme.ts`** — color constants for POI zones, structure
  events, manipulation cycle boxes (`MANIPULATION_BOX_STYLES`), volume delta,
  RSI, consolidation range boxes (`CONSOLIDATION_BOX_STYLES`, neutral slate),
  the liquidity heatmap gradient, leverage-liquidation bands
  (`LIQUIDATION_LEVERAGE_COLORS`, warm gradient by tier), and other chart
  elements.

The KPI row, main chart (with volume delta and RSI sub-panes), and
manipulation cycles sidebar panel are implemented. The liquidity targets,
retail trap, and market structure sidebar panels are not yet implemented
in the React frontend.

