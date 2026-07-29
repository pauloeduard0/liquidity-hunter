/**
 * Shared "the user is currently manipulating the chart" flag.
 *
 * Applying a fresh dashboard snapshot is a synchronous main-thread burst
 * (four panes of `setData` plus the overlay rebuild). Landing that burst in
 * the middle of a drag or a zoom is exactly when it is felt as a stutter, so
 * the pollers skip an apply while the chart is being handled and pick the next
 * tick up instead. Live-ness costs at most one refresh interval, and only
 * while the user's hand is on the chart.
 *
 * Module state rather than context: the producer (`MainChart`'s DOM listeners)
 * and the consumers (`App`'s poll timers) are in different subtrees, and this
 * flag must never trigger a React re-render.
 */

let dragging = false
let busyUntil = 0

/** A wheel/zoom gesture: stays busy for a short tail after the last event. */
export function markChartGesture(tailMs = 700): void {
  busyUntil = Date.now() + tailMs
}

export function markChartDragStart(): void {
  dragging = true
}

export function markChartDragEnd(tailMs = 500): void {
  dragging = false
  busyUntil = Date.now() + tailMs
}

export function isChartBusy(): boolean {
  return dragging || Date.now() < busyUntil
}
