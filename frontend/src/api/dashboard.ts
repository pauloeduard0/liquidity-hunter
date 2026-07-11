import type { DashboardData, MarketOverview, TimeFrame } from '../types/dashboard'

export interface DashboardQuery {
  symbol?: string
  timeframe?: TimeFrame
  limit?: number
  swingLookback?: number
}

/** Fetch a `DashboardData` snapshot from `GET /api/dashboard`. */
export async function fetchDashboardData(query: DashboardQuery = {}): Promise<DashboardData> {
  const params = new URLSearchParams()
  if (query.symbol) params.set('symbol', query.symbol)
  if (query.timeframe) params.set('timeframe', query.timeframe)
  if (query.limit) params.set('limit', String(query.limit))
  if (query.swingLookback) params.set('swing_lookback', String(query.swingLookback))

  const response = await fetch(`/api/dashboard?${params.toString()}`)
  if (!response.ok) {
    throw new Error(`GET /api/dashboard failed: ${response.status} ${response.statusText}`)
  }
  return (await response.json()) as DashboardData
}

/** Fetch the multi-timeframe structural ladder from `GET /api/overview`. */
export async function fetchOverview(symbol: string): Promise<MarketOverview> {
  const params = new URLSearchParams({ symbol })
  const response = await fetch(`/api/overview?${params.toString()}`)
  if (!response.ok) {
    throw new Error(`GET /api/overview failed: ${response.status} ${response.statusText}`)
  }
  return (await response.json()) as MarketOverview
}
