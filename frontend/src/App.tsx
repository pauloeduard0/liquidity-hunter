import { useEffect, useState } from 'react'

import { fetchDashboardData } from './api/dashboard'
import { KpiRow } from './components/KpiRow'
import { MainChart } from './components/MainChart'
import { ManipulationCyclesPanel } from './components/ManipulationCyclesPanel'
import type { DashboardData, TimeFrame } from './types/dashboard'

const SYMBOL = 'BTCUSDT'

const REFRESH_INTERVAL_MS = 5_000

const TIMEFRAME_OPTIONS: { value: TimeFrame; label: string }[] = [
  { value: '5m', label: '5M' },
  { value: '15m', label: '15M' },
  { value: '1h', label: '1H' },
  { value: '4h', label: '4H' },
]

function App() {
  const [timeframe, setTimeframe] = useState<TimeFrame>('1h')
  const [data, setData] = useState<DashboardData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [manipChartVisible, setManipChartVisible] = useState(true)

  const switchTimeframe = (tf: TimeFrame) => {
    setData(null)
    setError(null)
    setTimeframe(tf)
  }

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

  return (
    <div className="flex h-screen flex-col bg-[#0e1117] px-3 py-2 text-[#d1d4dc]">
      <header className="mb-2 flex-none">
        <h1 className="text-lg font-semibold">Liquidity Hunter</h1>
      </header>

      {error && (
        <div className="rounded-lg border border-[#ef5350] bg-[#161a25] p-4 text-[#ef5350]">
          Failed to load dashboard data: {error}
        </div>
      )}

      {!error && !data && <p className="text-[#8a8f9c]">Loading...</p>}

      {data && (
        <div className="flex min-h-0 flex-1 flex-col gap-2">
          <KpiRow data={data} />
          <div className="flex min-h-0 flex-1 gap-2">
            {/* Chart area */}
            <div className="flex min-h-0 min-w-0 flex-1 flex-col rounded-lg border border-[#1f2430] bg-[#161a25] p-2">
              <div className="mb-1 flex gap-1">
                {TIMEFRAME_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    onClick={() => switchTimeframe(opt.value)}
                    className={`rounded px-3 py-1 text-sm font-medium transition-colors ${
                      timeframe === opt.value
                        ? 'bg-[#2962ff] text-white'
                        : 'bg-[#1f2430] text-[#8a8f9c] hover:text-[#d1d4dc]'
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
              <MainChart key={timeframe} data={data} showManipulationBoxes={manipChartVisible} />
            </div>

            {/* Sidebar */}
            <div className="w-72 flex-none overflow-y-auto rounded-lg border border-[#1f2430] bg-[#161a25] p-3">
              <ManipulationCyclesPanel
                cycles={data.manipulation_cycles}
                chartVisible={manipChartVisible}
                onToggleChart={() => setManipChartVisible((v) => !v)}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default App
