import { useEffect, useState } from 'react'

import { fetchDashboardData, fetchOverview } from './api/dashboard'
import { BehaviorDivergencePanel } from './components/BehaviorDivergencePanel'
import { KpiRow } from './components/KpiRow'
import { Logo } from './components/Logo'
import { MainChart } from './components/MainChart'
import { ManipulationCyclesPanel } from './components/ManipulationCyclesPanel'
import { MultiTimeframePanel } from './components/MultiTimeframePanel'
import { NarrativePanel } from './components/NarrativePanel'
import type { DashboardData, MarketOverview, TimeFrame } from './types/dashboard'
import { chartTimezoneLabel } from './utils/chartTime'
import { formatPrice } from './utils/format'

const REFRESH_INTERVAL_MS = 5_000
// The ladder's readings change at most once per candle (per timeframe), and
// the backend caches each timeframe with a proportional TTL — polling faster
// than this only re-reads caches.
const OVERVIEW_REFRESH_INTERVAL_MS = 30_000

// Snapshots already fetched this session, so switching back to a
// symbol/timeframe renders instantly from cache (then revalidates on the next
// poll). A first-visit switch keeps the previous snapshot on screen, dimmed,
// instead of tearing the dashboard down to the skeleton.
const snapshotCache = new Map<string, DashboardData>()
const overviewCache = new Map<string, MarketOverview>()

const snapshotKey = (symbol: string, timeframe: TimeFrame) => `${symbol}|${timeframe}`

const SYMBOL_OPTIONS: { value: string; label: string }[] = [
  { value: 'BTCUSDT', label: 'BTC' },
  { value: 'ETHUSDT', label: 'ETH' },
  { value: 'SOLUSDT', label: 'SOL' },
  { value: 'NEARUSDT', label: 'NEAR' },
  { value: 'AAVEUSDT', label: 'AAVE' },
  { value: 'DASHUSDT', label: 'DASH' },
  { value: 'XAUUSDT', label: 'XAU' },
  { value: 'AEROUSDT', label: 'AERO' },
  { value: 'ENAUSDT', label: 'ENA' },
  { value: 'HYPEUSDT', label: 'HYPE' },
  { value: 'ETHBTC', label: 'ETH/BTC' },
  { value: 'MUUSDT', label: 'MU' },
  { value: 'ZECUSDT', label: 'ZEC' },
]

const TIMEFRAME_OPTIONS: { value: TimeFrame; label: string }[] = [
  { value: '5m', label: '5M' },
  { value: '15m', label: '15M' },
  { value: '30m', label: '30M' },
  { value: '1h', label: '1H' },
  { value: '4h', label: '4H' },
  { value: '1d', label: '1D' },
  { value: '1w', label: '1W' },
]

function LoadingSkeleton() {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      {/* KPI skeleton */}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="skeleton h-[76px]" />
        ))}
      </div>
      {/* Chart skeleton */}
      <div className="flex min-h-0 flex-1 gap-2">
        <div className="skeleton min-h-0 flex-1" />
        <div className="skeleton w-72 flex-none" />
      </div>
    </div>
  )
}

function StatusBar({ data, symbol }: { data: DashboardData | null; symbol: string }) {
  const now = new Date()
  const time = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`

  return (
    <div className="flex items-center justify-between border-t border-[#1a1f2e] px-1 py-1">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1.5">
          <span className="relative flex h-[6px] w-[6px]">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#26a69a] opacity-40" />
            <span className="relative inline-flex h-[6px] w-[6px] rounded-full bg-[#26a69a]" />
          </span>
          <span className="text-[10px] font-medium text-[#26a69a]">LIVE</span>
        </div>
        <span className="text-[10px] text-[#3d4455]">|</span>
        <span className="font-mono text-[10px] text-[#5d6477]">{symbol}</span>
      </div>
      <div className="flex items-center gap-3">
        {data && (
          <>
            <span className="text-[10px] text-[#3d4455]">
              {data.candles.length} candles
            </span>
            <span className="text-[10px] text-[#3d4455]">|</span>
            <span className="text-[10px] text-[#3d4455]">
              {data.market_structure_events.length + data.internal_structure_events.length} events
            </span>
            <span className="text-[10px] text-[#3d4455]">|</span>
          </>
        )}
        <span className="font-mono text-[10px] text-[#5d6477]">{time}</span>
      </div>
    </div>
  )
}

function App() {
  const [symbol, setSymbol] = useState<string>('BTCUSDT')
  const [timeframe, setTimeframe] = useState<TimeFrame>('1h')
  const [chartTimeframe, setChartTimeframe] = useState<TimeFrame>('1h')
  const [data, setData] = useState<DashboardData | null>(null)
  const [chartData, setChartData] = useState<DashboardData | null>(null)
  const [overview, setOverview] = useState<MarketOverview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [manipChartVisible, setManipChartVisible] = useState(false)
  const [divChartVisible, setDivChartVisible] = useState(false)
  const [vsaVisible, setVsaVisible] = useState(true)
  const [heatmapVisible, setHeatmapVisible] = useState(false)
  const [liquidationVisible, setLiquidationVisible] = useState(false)
  const [liquidationLiveOnly, setLiquidationLiveOnly] = useState(false)
  const [sweptZonesVisible, setSweptZonesVisible] = useState(false)
  const [huntWindowVisible, setHuntWindowVisible] = useState(false)
  const [continuationWindowVisible, setContinuationWindowVisible] = useState(false)
  // On by default: a confirmed range is the "why is the chart silent here"
  // answer, the whole point of detecting it.
  const [rangeBoxesVisible, setRangeBoxesVisible] = useState(true)
  const [obVisible, setObVisible] = useState(false)
  const [sweepVisible, setSweepVisible] = useState(false)
  const [eqlVisible, setEqlVisible] = useState(false)
  const [volumeVisible, setVolumeVisible] = useState(true)
  const [rsiDivVisible, setRsiDivVisible] = useState(false)
  const [indicatorsVisible, setIndicatorsVisible] = useState(false)
  const [, setTick] = useState(0)

  const chartDiverged = chartTimeframe !== timeframe

  // The rendered snapshot lags the selection while a first-visit combo loads;
  // dim the dashboard and show the loading pill instead of a skeleton.
  const dataStale = data !== null && (data.symbol !== symbol || data.timeframe !== timeframe)

  // Switching keeps the current snapshot on screen (dimmed via the staleness
  // check below) instead of tearing down to the skeleton; if the target combo
  // was already visited this session, the fetch effect renders it instantly
  // from `snapshotCache` while revalidating.
  const switchTimeframe = (tf: TimeFrame) => {
    const cached = snapshotCache.get(snapshotKey(symbol, tf))
    if (cached) setData(cached)
    setChartData(null)
    setError(null)
    setTimeframe(tf)
    setChartTimeframe(tf)
  }

  const switchSymbol = (sym: string) => {
    if (sym === symbol) return
    const cached = snapshotCache.get(snapshotKey(sym, timeframe))
    if (cached) setData(cached)
    setChartData(null)
    setOverview(overviewCache.get(sym) ?? null)
    setError(null)
    setSymbol(sym)
  }

  const switchChartTimeframe = (tf: TimeFrame) => {
    if (tf === chartTimeframe) return
    // Synced back to the global timeframe: fall through to the live-polled
    // `data` rather than pinning a cached snapshot the diverged-chart effect
    // would never refresh.
    setChartData(tf === timeframe ? null : (snapshotCache.get(snapshotKey(symbol, tf)) ?? null))
    setChartTimeframe(tf)
  }

  // Fetch global data (sidebar panels + chart when synced)
  useEffect(() => {
    let cancelled = false

    const load = () => {
      fetchDashboardData({ symbol, timeframe })
        .then((result) => {
          snapshotCache.set(snapshotKey(symbol, timeframe), result)
          if (!cancelled) setData(result)
        })
        .catch((err: unknown) => {
          if (!cancelled) setError(err instanceof Error ? err.message : String(err))
        })
    }

    load()
    const interval = setInterval(load, REFRESH_INTERVAL_MS)

    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [symbol, timeframe])

  // Fetch chart-only data when chart timeframe diverges from global
  useEffect(() => {
    if (chartTimeframe === timeframe) return

    let cancelled = false

    const load = () => {
      fetchDashboardData({ symbol, timeframe: chartTimeframe })
        .then((result) => {
          snapshotCache.set(snapshotKey(symbol, chartTimeframe), result)
          if (!cancelled) setChartData(result)
        })
        .catch((err: unknown) => {
          if (!cancelled) setError(err instanceof Error ? err.message : String(err))
        })
    }

    load()
    const interval = setInterval(load, REFRESH_INTERVAL_MS)

    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [symbol, chartTimeframe, timeframe])

  // Fetch the multi-timeframe structure ladder (sidebar)
  useEffect(() => {
    let cancelled = false

    const load = () => {
      fetchOverview(symbol)
        .then((result) => {
          overviewCache.set(symbol, result)
          if (!cancelled) setOverview(result)
        })
        .catch(() => {
          // Secondary panel: keep the last ladder on transient errors rather
          // than tearing down the whole dashboard.
        })
    }

    load()
    const interval = setInterval(load, OVERVIEW_REFRESH_INTERVAL_MS)

    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [symbol])

  // Tick the clock in the status bar
  useEffect(() => {
    const interval = setInterval(() => setTick((t) => t + 1), 1000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="flex h-screen flex-col bg-[#0a0d14] text-[#d1d4dc]">
      {/* ── Header ───────────────────────────────────────────── */}
      <header className="flex flex-none items-center justify-between border-b border-[#1a1f2e] px-4 py-2.5">
        <div className="flex items-center gap-3">
          <Logo size={26} />
          <div className="flex items-baseline gap-2">
            <h1 className="text-sm font-bold tracking-tight text-[#e1e4ec]">
              LIQUIDITY HUNTER
            </h1>
            <span className="text-[10px] font-medium tracking-widest text-[#2962ff]">
              RESEARCH
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* Symbol selector */}
          <div className="mr-2 flex rounded-md border border-[#1a1f2e] bg-[#0f1319] p-0.5">
            {SYMBOL_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => switchSymbol(opt.value)}
                className="relative rounded-[5px] px-3 py-1.5 font-mono text-xs font-semibold tracking-wide transition-all duration-200"
                style={{
                  color: symbol === opt.value ? '#e1e4ec' : '#5d6477',
                  backgroundColor: symbol === opt.value ? '#1a1f2e' : 'transparent',
                  boxShadow: symbol === opt.value ? '0 1px 3px rgba(0,0,0,0.3)' : 'none',
                }}
              >
                {opt.label}
              </button>
            ))}
          </div>

          {/* Timeframe selector */}
          <div className="flex rounded-md border border-[#1a1f2e] bg-[#0f1319] p-0.5">
            {TIMEFRAME_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => switchTimeframe(opt.value)}
                className="relative rounded-[5px] px-3 py-1.5 text-[11px] font-bold tracking-wide transition-all duration-200"
                style={{
                  color: timeframe === opt.value ? '#e1e4ec' : '#5d6477',
                  backgroundColor: timeframe === opt.value ? '#1a1f2e' : 'transparent',
                  boxShadow: timeframe === opt.value ? '0 1px 3px rgba(0,0,0,0.3)' : 'none',
                }}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      </header>

      {/* ── Content ──────────────────────────────────────────── */}
      <main className="relative flex min-h-0 flex-1 flex-col px-3 py-2">
        {dataStale && (
          <div className="pointer-events-none absolute left-1/2 top-4 z-20 flex -translate-x-1/2 items-center gap-2 rounded-full border border-[#1a1f2e] bg-[#0f1319f0] px-3 py-1.5 text-[11px] font-medium text-[#9ca3b4] shadow-lg">
            <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-[#2962ff]" />
            Loading {symbol} · {timeframe.toUpperCase()}…
          </div>
        )}
        {error && (
          <div className="flex items-center gap-3 rounded-lg border border-[#ef535030] bg-[#ef53500a] p-4">
            <span className="text-sm text-[#ef5350]">⬡</span>
            <div>
              <div className="text-xs font-medium text-[#ef5350]">Connection Error</div>
              <div className="mt-0.5 text-[11px] text-[#9ca3b4]">{error}</div>
            </div>
          </div>
        )}

        {!error && !data && <LoadingSkeleton />}

        {data && (
          <div
            className={`flex min-h-0 flex-1 flex-col gap-2 transition-opacity duration-150 ${
              dataStale ? 'pointer-events-none opacity-40' : ''
            }`}
          >
            <KpiRow data={data} />
            <div className="flex min-h-0 flex-1 gap-2">
              {/* Chart area */}
              <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-lg border border-[#1a1f2e] bg-[#0f1319]">
                {/* Chart toolbar */}
                <div className="flex items-center justify-between border-b border-[#1a1f2e] px-3 py-1.5">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[11px] font-semibold text-[#9ca3b4]">
                      {symbol}
                    </span>
                    <span className="text-[10px] text-[#3d4455]">•</span>
                    <div className="flex items-center rounded border border-[#1a1f2e] bg-[#0a0d14] p-px">
                      {TIMEFRAME_OPTIONS.map((opt) => (
                        <button
                          key={opt.value}
                          onClick={() => switchChartTimeframe(opt.value)}
                          className="rounded-[3px] px-1.5 py-0.5 text-[9px] font-bold tracking-wide transition-all duration-150"
                          style={{
                            color: chartTimeframe === opt.value ? '#e1e4ec' : '#5d6477',
                            backgroundColor: chartTimeframe === opt.value
                              ? (chartDiverged ? '#2962ff30' : '#1a1f2e')
                              : 'transparent',
                            boxShadow: chartTimeframe === opt.value ? '0 1px 2px rgba(0,0,0,0.2)' : 'none',
                          }}
                        >
                          {opt.label}
                        </button>
                      ))}
                    </div>
                    {chartDiverged && (
                      <button
                        onClick={() => switchChartTimeframe(timeframe)}
                        className="rounded px-1 py-0.5 text-[9px] font-medium text-[#2962ff] hover:bg-[#2962ff15] transition-colors"
                        title="Sync chart back to global timeframe"
                      >
                        SYNC
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => setObVisible((v) => !v)}
                      className={`ml-1 rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider transition-colors ${
                        obVisible
                          ? 'bg-[#2979ff22] text-[#2979ff]'
                          : 'bg-[#1a1f2e] text-[#5d6477] hover:text-[#9ca3b4]'
                      }`}
                      title="Toggle order block (POI) zones"
                    >
                      ▦ OB
                    </button>
                    <button
                      type="button"
                      onClick={() => setSweepVisible((v) => !v)}
                      className={`rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider transition-colors ${
                        sweepVisible
                          ? 'bg-[#ab47bc22] text-[#ab47bc]'
                          : 'bg-[#1a1f2e] text-[#5d6477] hover:text-[#9ca3b4]'
                      }`}
                      title="Toggle liquidity sweep (SWEEP + RTO) markers"
                    >
                      ⌇ Sweep
                    </button>
                    <button
                      type="button"
                      onClick={() => setEqlVisible((v) => !v)}
                      className={`rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider transition-colors ${
                        eqlVisible
                          ? 'bg-[#26a69a22] text-[#26a69a]'
                          : 'bg-[#1a1f2e] text-[#5d6477] hover:text-[#9ca3b4]'
                      }`}
                      title="Toggle liquidity target zone lines (EQH/EQL, OB, FVG, swings)"
                    >
                      ═ EQL
                    </button>
                    <button
                      type="button"
                      onClick={() => setVolumeVisible((v) => !v)}
                      className={`rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider transition-colors ${
                        volumeVisible
                          ? 'bg-[#26c6da22] text-[#26c6da]'
                          : 'bg-[#1a1f2e] text-[#5d6477] hover:text-[#9ca3b4]'
                      }`}
                      title="Toggle raw volume bars (base of the main pane)"
                    >
                      ▬ Vol
                    </button>
                    <button
                      type="button"
                      onClick={() => setHeatmapVisible((v) => !v)}
                      className={`rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider transition-colors ${
                        heatmapVisible
                          ? 'bg-[#ef535022] text-[#ef5350]'
                          : 'bg-[#1a1f2e] text-[#5d6477] hover:text-[#9ca3b4]'
                      }`}
                      title="Toggle liquidity heatmap strip"
                    >
                      ▮ Heatmap
                    </button>
                    <button
                      type="button"
                      onClick={(e) => {
                        // Alt/Shift-click toggles "live pools only"; plain click toggles visibility.
                        if (e.altKey || e.shiftKey) setLiquidationLiveOnly((v) => !v)
                        else setLiquidationVisible((v) => !v)
                      }}
                      className={`rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider transition-colors ${
                        liquidationVisible
                          ? 'bg-[#26c6da22] text-[#26c6da]'
                          : 'bg-[#1a1f2e] text-[#5d6477] hover:text-[#9ca3b4]'
                      }`}
                      title="Click: toggle liquidation bands · Alt/Shift-click: live pools only"
                    >
                      ⊟ Liq{liquidationLiveOnly ? ' •' : ''}
                    </button>
                    <button
                      type="button"
                      onClick={() => setSweptZonesVisible((v) => !v)}
                      className={`rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider transition-colors ${
                        sweptZonesVisible
                          ? 'bg-[#ff980022] text-[#ff9800]'
                          : 'bg-[#1a1f2e] text-[#5d6477] hover:text-[#9ca3b4]'
                      }`}
                      title="Toggle swept EQH/EQL zones"
                    >
                      ⊟ Swept
                    </button>
                    <button
                      type="button"
                      onClick={() => setRangeBoxesVisible((v) => !v)}
                      className={`rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider transition-colors ${
                        rangeBoxesVisible
                          ? 'bg-[#90a4ae22] text-[#90a4ae]'
                          : 'bg-[#1a1f2e] text-[#5d6477] hover:text-[#9ca3b4]'
                      }`}
                      title="Toggle consolidation (lateral range) boxes"
                    >
                      ▭ Range
                    </button>
                    <button
                      type="button"
                      onClick={() => setVsaVisible((v) => !v)}
                      className={`rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider transition-colors ${
                        vsaVisible
                          ? 'bg-[#e040fb22] text-[#e040fb]'
                          : 'bg-[#1a1f2e] text-[#5d6477] hover:text-[#9ca3b4]'
                      }`}
                      title="Toggle VSA volume-spread signals (climax / thrust / no-supply)"
                    >
                      ≈ VSA
                    </button>
                    <button
                      type="button"
                      onClick={() => setHuntWindowVisible((v) => !v)}
                      className={`rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider transition-colors ${
                        huntWindowVisible
                          ? 'bg-[#ffb30022] text-[#ffb300]'
                          : 'bg-[#1a1f2e] text-[#5d6477] hover:text-[#9ca3b4]'
                      }`}
                      title="Toggle the liquidity-hunt window shading (counter-trend flip → capture)"
                    >
                      ⚡ Hunt
                    </button>
                    <button
                      type="button"
                      onClick={() => setContinuationWindowVisible((v) => !v)}
                      className={`rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider transition-colors ${
                        continuationWindowVisible
                          ? 'bg-[#42a5f522] text-[#42a5f5]'
                          : 'bg-[#1a1f2e] text-[#5d6477] hover:text-[#9ca3b4]'
                      }`}
                      title="Toggle aligned trend-continuation liquidity grabs (pullback swept internal liquidity, then resumed)"
                    >
                      ↗ Cont
                    </button>
                    <button
                      type="button"
                      onClick={() => setRsiDivVisible((v) => !v)}
                      className={`rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider transition-colors ${
                        rsiDivVisible
                          ? 'bg-[#ab47bc22] text-[#ab47bc]'
                          : 'bg-[#1a1f2e] text-[#5d6477] hover:text-[#9ca3b4]'
                      }`}
                      title="Toggle RSI divergence trendlines mirrored onto the price structure"
                    >
                      ∿ RSI Div
                    </button>
                    <button
                      type="button"
                      onClick={() => setIndicatorsVisible((v) => !v)}
                      className={`ml-1 rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider transition-colors ${
                        indicatorsVisible
                          ? 'bg-[#42a5f522] text-[#42a5f5]'
                          : 'bg-[#1a1f2e] text-[#5d6477] hover:text-[#9ca3b4]'
                      }`}
                      title="Toggle volume delta / RSI indicator panes"
                    >
                      {indicatorsVisible ? '▾' : '▸'} Vol/RSI
                    </button>
                  </div>
                  <div className="flex items-center gap-2 text-[10px] text-[#3d4455]">
                    {(() => {
                      const d = chartData ?? data
                      const tz = chartTimezoneLabel(d.timeframe)
                      const last = d.candles.at(-1)
                      return last ? (
                        <>
                          <span>O <span className="font-mono text-[#9ca3b4]">{formatPrice(last.open, last.close)}</span></span>
                          <span>H <span className="font-mono text-[#26a69a]">{formatPrice(last.high, last.close)}</span></span>
                          <span>L <span className="font-mono text-[#ef5350]">{formatPrice(last.low, last.close)}</span></span>
                          <span>C <span className="font-mono text-[#9ca3b4]">{formatPrice(last.close, last.close)}</span></span>
                          <span
                            className="rounded bg-[#1a1f2e] px-1 py-0.5 font-mono text-[9px] text-[#5d6477]"
                            title={
                              tz === 'UTC'
                                ? 'Chart times are exchange time (UTC)'
                                : `Chart times are your local time (${tz})`
                            }
                          >
                            {tz}
                          </span>
                        </>
                      ) : null
                    })()}
                  </div>
                </div>
                <div className="flex min-h-0 flex-1 flex-col p-1">
                  {/* Keyed by the *snapshot's* identity, not the selection:
                      the mounted chart keeps rendering the previous snapshot
                      while a switch loads, and remounts only when the new
                      combo's data actually arrives. */}
                  <MainChart key={`${(chartData ?? data).symbol}-${(chartData ?? data).timeframe}`} data={chartData ?? data} showConsolidationRanges={rangeBoxesVisible} showManipulationBoxes={manipChartVisible} showDivergenceMarkers={divChartVisible} showVsaMarkers={vsaVisible} showHeatmap={heatmapVisible} showLiquidationBands={liquidationVisible} liquidationLiveOnly={liquidationLiveOnly} showSweptZones={sweptZonesVisible} showOrderBlocks={obVisible} showSweeps={sweepVisible} showEqlZones={eqlVisible} showIndicators={indicatorsVisible} showHuntWindow={huntWindowVisible} showContinuationWindow={continuationWindowVisible} showVolume={volumeVisible} showRsiDivergence={rsiDivVisible} />
                </div>
              </div>

              {/* Sidebar */}
              <div className="flex w-72 flex-none flex-col overflow-hidden rounded-lg border border-[#1a1f2e] bg-[#0f1319]">
                <div className="border-b border-[#1a1f2e] px-3 py-2">
                  <span className="text-[10px] font-semibold uppercase tracking-[0.15em] text-[#5d6477]">
                    Analysis
                  </span>
                </div>
                <div className="flex-1 overflow-y-auto p-3">
                  <div className="flex flex-col gap-4 divide-y divide-[#1a1f2e] [&>*:not(:first-child)]:pt-4">
                    {overview && (
                      <MultiTimeframePanel
                        overview={overview}
                        activeTimeframe={chartTimeframe}
                        onSelectTimeframe={switchChartTimeframe}
                      />
                    )}
                    {data.narrative && (
                      <NarrativePanel narrative={data.narrative} />
                    )}
                    <ManipulationCyclesPanel
                      cycles={data.manipulation_cycles}
                      chartVisible={manipChartVisible}
                      onToggleChart={() => setManipChartVisible((v) => !v)}
                    />
                    {data.behavior_divergences.length > 0 && (
                      <BehaviorDivergencePanel
                        divergences={data.behavior_divergences}
                        chartVisible={divChartVisible}
                        onToggleChart={() => setDivChartVisible((v) => !v)}
                      />
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}
      </main>

      {/* ── Status Bar ───────────────────────────────────────── */}
      <StatusBar data={data} symbol={symbol} />
    </div>
  )
}

export default App
