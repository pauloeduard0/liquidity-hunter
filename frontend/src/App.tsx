import { useEffect, useState } from 'react'

import { fetchDashboardData } from './api/dashboard'
import { KpiRow } from './components/KpiRow'
import { MainChart } from './components/MainChart'
import type { DashboardData } from './types/dashboard'

const SYMBOL = 'BTCUSDT'
const TIMEFRAME = '1h'

// How often to poll `/api/dashboard` for fresh candles/price -- kept close
// to the backend's cache TTL so the chart/price update near-live.
const REFRESH_INTERVAL_MS = 5_000

function App() {
  const [data, setData] = useState<DashboardData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    const load = () => {
      fetchDashboardData({ symbol: SYMBOL, timeframe: TIMEFRAME })
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
  }, [])

  return (
    <div className="min-h-screen bg-[#0e1117] p-4 text-[#d1d4dc] md:p-6">
      <header className="mb-4">
        <h1 className="text-2xl font-semibold">Liquidity Hunter</h1>
        <p className="text-sm text-[#8a8f9c]">
          Liquidity intelligence and retail crowd psychology research -- descriptive only, not
          trading advice.
        </p>
      </header>

      {error && (
        <div className="rounded-lg border border-[#ef5350] bg-[#161a25] p-4 text-[#ef5350]">
          Failed to load dashboard data: {error}
        </div>
      )}

      {!error && !data && <p className="text-[#8a8f9c]">Loading...</p>}

      {data && (
        <div className="flex flex-col gap-4">
          <KpiRow data={data} />
          <div className="rounded-lg border border-[#1f2430] bg-[#161a25] p-2">
            <MainChart data={data} />
          </div>
        </div>
      )}
    </div>
  )
}

export default App
