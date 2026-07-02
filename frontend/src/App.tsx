import { useEffect, useState } from 'react'

import { fetchDashboardData } from './api/dashboard'
import { BehaviorDivergencePanel } from './components/BehaviorDivergencePanel'
import { KpiRow } from './components/KpiRow'
import { Logo } from './components/Logo'
import { MainChart } from './components/MainChart'
import { ManipulationCyclesPanel } from './components/ManipulationCyclesPanel'
import { NarrativePanel } from './components/NarrativePanel'
import type { DashboardData, TimeFrame } from './types/dashboard'

const SYMBOL = 'BTCUSDT'
const REFRESH_INTERVAL_MS = 5_000

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

function StatusBar({ data }: { data: DashboardData | null }) {
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
        <span className="font-mono text-[10px] text-[#5d6477]">{SYMBOL}</span>
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
  const [timeframe, setTimeframe] = useState<TimeFrame>('1h')
  const [chartTimeframe, setChartTimeframe] = useState<TimeFrame>('1h')
  const [data, setData] = useState<DashboardData | null>(null)
  const [chartData, setChartData] = useState<DashboardData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [manipChartVisible, setManipChartVisible] = useState(true)
  const [divChartVisible, setDivChartVisible] = useState(true)
  const [heatmapVisible, setHeatmapVisible] = useState(true)
  const [liquidationVisible, setLiquidationVisible] = useState(false)
  const [liquidationLiveOnly, setLiquidationLiveOnly] = useState(false)
  const [sweptZonesVisible, setSweptZonesVisible] = useState(false)
  const [, setTick] = useState(0)

  const chartDiverged = chartTimeframe !== timeframe

  const switchTimeframe = (tf: TimeFrame) => {
    setData(null)
    setChartData(null)
    setError(null)
    setTimeframe(tf)
    setChartTimeframe(tf)
  }

  const switchChartTimeframe = (tf: TimeFrame) => {
    if (tf === chartTimeframe) return
    setChartData(null)
    setChartTimeframe(tf)
  }

  // Fetch global data (sidebar panels + chart when synced)
  useEffect(() => {
    let cancelled = false

    const load = () => {
      fetchDashboardData({ symbol: SYMBOL, timeframe })
        .then((result) => {
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
  }, [timeframe])

  // Fetch chart-only data when chart timeframe diverges from global
  useEffect(() => {
    if (chartTimeframe === timeframe) return

    let cancelled = false

    const load = () => {
      fetchDashboardData({ symbol: SYMBOL, timeframe: chartTimeframe })
        .then((result) => {
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
  }, [chartTimeframe, timeframe])

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
          {/* Symbol badge */}
          <div className="mr-2 flex items-center gap-1.5 rounded-md border border-[#1a1f2e] bg-[#0f1319] px-3 py-1.5">
            <span className="text-[10px] font-bold tracking-wider text-[#5d6477]">SYM</span>
            <span className="font-mono text-xs font-semibold text-[#d1d4dc]">{SYMBOL}</span>
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
      <main className="flex min-h-0 flex-1 flex-col px-3 py-2">
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
          <div className="flex min-h-0 flex-1 flex-col gap-2">
            <KpiRow data={data} />
            <div className="flex min-h-0 flex-1 gap-2">
              {/* Chart area */}
              <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-lg border border-[#1a1f2e] bg-[#0f1319]">
                {/* Chart toolbar */}
                <div className="flex items-center justify-between border-b border-[#1a1f2e] px-3 py-1.5">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[11px] font-semibold text-[#9ca3b4]">
                      {SYMBOL}
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
                      onClick={() => setHeatmapVisible((v) => !v)}
                      className={`ml-1 rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider transition-colors ${
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
                  </div>
                  <div className="flex items-center gap-2 text-[10px] text-[#3d4455]">
                    {(() => {
                      const d = chartData ?? data
                      const last = d.candles.at(-1)
                      return last ? (
                        <>
                          <span>O <span className="font-mono text-[#9ca3b4]">{last.open.toFixed(2)}</span></span>
                          <span>H <span className="font-mono text-[#26a69a]">{last.high.toFixed(2)}</span></span>
                          <span>L <span className="font-mono text-[#ef5350]">{last.low.toFixed(2)}</span></span>
                          <span>C <span className="font-mono text-[#9ca3b4]">{last.close.toFixed(2)}</span></span>
                        </>
                      ) : null
                    })()}
                  </div>
                </div>
                <div className="flex min-h-0 flex-1 flex-col p-1">
                  <MainChart key={chartTimeframe} data={chartData ?? data} showManipulationBoxes={manipChartVisible} showDivergenceMarkers={divChartVisible} showHeatmap={heatmapVisible} showLiquidationBands={liquidationVisible} liquidationLiveOnly={liquidationLiveOnly} showSweptZones={sweptZonesVisible} />
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
      <StatusBar data={data} />
    </div>
  )
}

export default App
