// Magnitude-aware price formatting so low-priced pairs (ETHBTC ~0.03,
// ENAUSDT sub-1) keep meaningful decimals instead of collapsing onto a fixed
// 2-decimal grid. Mirrors the chart series precision (MainChart.priceFormatFor):
// ~5 significant digits, precision = 4 - floor(log10(ref)), clamped to [2, 8].
export function priceDecimals(ref: number): number {
  if (!Number.isFinite(ref) || ref <= 0) return 2
  const exponent = Math.floor(Math.log10(ref))
  return Math.min(8, Math.max(2, 4 - exponent))
}

// Format a price with a decimal count derived from its own magnitude (or from
// `reference`, when a stable window magnitude is preferred over the value's own).
export function formatPrice(price: number, reference?: number): string {
  const decimals = priceDecimals(reference ?? price)
  return price.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}
