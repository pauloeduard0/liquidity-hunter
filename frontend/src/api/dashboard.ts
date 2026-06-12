import type { DashboardData, TimeFrame } from '../types/dashboard'

export interface DashboardQuery {
  symbol?: string
  timeframe?: TimeFrame
  limit?: number
  swingLookback?: number
  internalSwingLookback?: number
}

/** Fetch a `DashboardData` snapshot from `GET /api/dashboard`. */
export async function fetchDashboardData(query: DashboardQuery = {}): Promise<DashboardData> {
  const params = new URLSearchParams()
  if (query.symbol) params.set('symbol', query.symbol)
  if (query.timeframe) params.set('timeframe', query.timeframe)
  if (query.limit) params.set('limit', String(query.limit))
  if (query.swingLookback) params.set('swing_lookback', String(query.swingLookback))
  if (query.internalSwingLookback) {
    params.set('internal_swing_lookback', String(query.internalSwingLookback))
  }

  const response = await fetch(`/api/dashboard?${params.toString()}`)
  if (!response.ok) {
    throw new Error(`GET /api/dashboard failed: ${response.status} ${response.statusText}`)
  }
  return (await response.json()) as DashboardData
}
