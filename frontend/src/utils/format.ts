// Magnitude-aware price formatting so low-priced pairs (ETHBTC ~0.03,
// ENAUSDT sub-1) keep meaningful decimals instead of collapsing onto a fixed
// 2-decimal grid. Mirrors the chart series precision (MainChart.priceFormatFor):
// ~5 significant digits, precision = 4 - floor(log10(ref)), clamped to [2, 8].
export function priceDecimals(ref: number): number {
  if (!Number.isFinite(ref) || ref <= 0) return 2
  const exponent = Math.floor(Math.log10(ref))
  return Math.min(8, Math.max(2, 4 - exponent))
}

// An on-chain pair is charted by market cap (see `GeckoTerminalDataProvider`),
// so its axis runs in millions and the significant-digit rule above renders
// "7,166,059.96" — eight glyphs of noise for a number nobody reads at that
// resolution. Those values get an abbreviated scale instead: 7.17M, 500.00K.
// Two decimals throughout, which keeps ~10-unit resolution at the 500K end,
// enough to tell two structure levels apart.
//
// Module state rather than a per-call argument for the same reason
// `chartTime` keeps its offset that way: every number on screen — the OHLC
// readout, the KPI cards, the sidebar panels, the chart's own labels — flows
// through `formatPrice`, and a chart mixing "7.17M" with "7,166,059.96" reads
// as two different instruments. `App` sets it during render from the symbol,
// before the children that consume it.
const ONCHAIN_SYMBOL = /^(?:[a-z0-9-]+:)?(?:0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$/

let usesCompactScale = false

export function isOnchainSymbol(symbol: string): boolean {
  return ONCHAIN_SYMBOL.test(symbol.trim())
}

/** Abbreviate values on an on-chain symbol's market-cap axis; plain elsewhere. */
export function setPriceFormatMode(symbol: string): void {
  usesCompactScale = isOnchainSymbol(symbol)
}

export function usesCompactPrices(): boolean {
  return usesCompactScale
}

const COMPACT_UNITS: readonly (readonly [number, string])[] = [
  [1e9, 'B'],
  [1e6, 'M'],
  [1e3, 'K'],
]

/** `7166059.96` -> `"7.17M"`, `500000` -> `"500.00K"`, `842` -> `"842.00"`. */
export function formatCompactPrice(value: number): string {
  if (!Number.isFinite(value)) return '—'
  const magnitude = Math.abs(value)
  for (const [scale, suffix] of COMPACT_UNITS) {
    if (magnitude >= scale) return `${(value / scale).toFixed(2)}${suffix}`
  }
  return value.toFixed(2)
}

// Format a price with a decimal count derived from its own magnitude (or from
// `reference`, when a stable window magnitude is preferred over the value's own).
export function formatPrice(price: number, reference?: number): string {
  if (usesCompactScale) return formatCompactPrice(price)
  const decimals = priceDecimals(reference ?? price)
  return price.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}
