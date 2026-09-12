import { useEffect, useRef, useState } from 'react'

// One switchable chart layer. `color` is the layer's own hue on the chart, so
// the row lights up in the colour the user will look for in the pane; the
// on/off *pattern* (filled dot + tinted row + bright label) is shared by every
// row, which is what keeps 18 layers readable in one list.
export type IndicatorItem = {
  id: string
  label: string
  glyph: string
  color: string
  active: boolean
  onToggle: () => void
  /** Appended to the label when on, e.g. the VSA mode or the VWAP band state. */
  badge?: string
  title?: string
  disabled?: boolean
  /** A modifier-click action the row exposes as its own small chip. */
  secondary?: {
    glyph: string
    title: string
    active: boolean
    onToggle: () => void
  }
}

export type IndicatorGroup = { label: string; items: IndicatorItem[] }

function Row({ item }: { item: IndicatorItem }) {
  const { active, disabled, color } = item
  return (
    <div
      className={`flex items-center gap-2 rounded px-2 py-1 transition-colors ${
        disabled ? 'opacity-40' : 'hover:bg-[#161b28]'
      }`}
      style={active && !disabled ? { backgroundColor: `${color}14` } : undefined}
    >
      <button
        type="button"
        disabled={disabled}
        onClick={item.onToggle}
        title={item.title}
        aria-pressed={active}
        className={`flex min-w-0 flex-1 items-center gap-2 text-left ${
          disabled ? 'cursor-not-allowed' : ''
        }`}
      >
        {/* The state dot: filled in the layer's colour when on, hollow when off */}
        <span
          className="h-[7px] w-[7px] flex-none rounded-full border transition-colors"
          style={{
            borderColor: active && !disabled ? color : '#3d4455',
            backgroundColor: active && !disabled ? color : 'transparent',
          }}
        />
        <span
          className="w-[14px] flex-none text-center text-[10px] leading-none"
          style={{ color: active && !disabled ? color : '#5d6477' }}
        >
          {item.glyph}
        </span>
        <span
          className="truncate text-[11px] font-medium"
          style={{ color: active && !disabled ? '#e1e4ec' : '#8b92a5' }}
        >
          {item.label}
          {active && item.badge ? (
            <span className="ml-1 font-mono text-[9px]" style={{ color }}>
              {item.badge}
            </span>
          ) : null}
        </span>
      </button>

      {item.secondary && (
        <button
          type="button"
          disabled={disabled}
          onClick={item.secondary.onToggle}
          title={item.secondary.title}
          aria-pressed={item.secondary.active}
          className={`flex-none rounded px-1.5 py-0.5 text-[9px] font-semibold transition-colors ${
            disabled ? 'cursor-not-allowed' : 'hover:brightness-125'
          }`}
          style={
            item.secondary.active
              ? { backgroundColor: `${color}28`, color }
              : { backgroundColor: '#1a1f2e', color: '#5d6477' }
          }
        >
          {item.secondary.glyph}
        </button>
      )}
    </div>
  )
}

// The chart's layer switches, as a dropdown instead of a row of 18 chips in
// the toolbar. Selecting does *not* close the panel: turning layers on is
// usually done a few at a time.
export function IndicatorMenu({
  groups,
  onReset,
}: {
  groups: IndicatorGroup[]
  onReset?: () => void
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  const activeCount = groups.reduce(
    (n, g) => n + g.items.filter((i) => i.active && !i.disabled).length,
    0,
  )

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="true"
        aria-expanded={open}
        title="Camadas do gráfico"
        className={`flex items-center gap-1.5 rounded px-2 py-1 text-[10px] font-semibold transition-colors ${
          open
            ? 'bg-[#1a1f2e] text-[#e1e4ec]'
            : 'bg-[#1a1f2e] text-[#8b92a5] hover:text-[#e1e4ec]'
        }`}
      >
        <span className="font-serif text-[11px] italic leading-none">fx</span>
        <span>Indicators</span>
        {activeCount > 0 && (
          <span className="rounded-full bg-[#2962ff26] px-1.5 font-mono text-[9px] text-[#5b8dff]">
            {activeCount}
          </span>
        )}
        <span className="text-[8px] leading-none text-[#5d6477]">▾</span>
      </button>

      {open && (
        <div className="absolute left-0 top-[calc(100%+4px)] z-40 w-64 overflow-hidden rounded-md border border-[#1a1f2e] bg-[#0f1319] shadow-xl shadow-black/40">
          <div className="max-h-[70vh] overflow-y-auto p-1.5">
            {groups.map((group, gi) => (
              <div key={group.label} className={gi > 0 ? 'mt-2' : undefined}>
                <div className="px-2 pb-1 text-[9px] font-semibold uppercase tracking-[0.15em] text-[#4a5164]">
                  {group.label}
                </div>
                <div className="flex flex-col gap-px">
                  {group.items.map((item) => (
                    <Row key={item.id} item={item} />
                  ))}
                </div>
              </div>
            ))}
          </div>
          {onReset && (
            <button
              type="button"
              onClick={onReset}
              disabled={activeCount === 0}
              className="w-full border-t border-[#1a1f2e] px-2.5 py-1.5 text-left text-[10px] font-medium text-[#5d6477] transition-colors hover:text-[#9ca3b4] disabled:cursor-not-allowed disabled:text-[#3a4051]"
            >
              Desligar tudo
            </button>
          )}
        </div>
      )}
    </div>
  )
}
