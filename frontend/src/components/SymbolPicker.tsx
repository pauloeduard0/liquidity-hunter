import { useEffect, useRef, useState } from 'react'

export type SymbolOption = { value: string; label: string }

// The symbol lives next to the chart's own timeframe row, so the picker is
// rendered as a dropdown anchored to the symbol label rather than as a row of
// buttons in the header (which did not scale past a dozen pairs).
export function SymbolPicker({
  options,
  value,
  onChange,
}: {
  options: SymbolOption[]
  value: string
  onChange: (symbol: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
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

  const current = options.find((o) => o.value === value)
  const needle = query.trim().toLowerCase()
  const filtered = needle
    ? options.filter(
        (o) =>
          o.label.toLowerCase().includes(needle) ||
          o.value.toLowerCase().includes(needle),
      )
    : options

  const select = (symbol: string) => {
    setOpen(false)
    if (symbol !== value) onChange(symbol)
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => {
          setQuery('')
          setOpen((v) => !v)
        }}
        title="Trocar de ativo"
        aria-haspopup="listbox"
        aria-expanded={open}
        className={`flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[11px] font-semibold transition-colors ${
          open
            ? 'bg-[#1a1f2e] text-[#e1e4ec]'
            : 'text-[#9ca3b4] hover:bg-[#1a1f2e] hover:text-[#e1e4ec]'
        }`}
      >
        <span>{current?.label ?? value}</span>
        <span className="text-[8px] leading-none text-[#5d6477]">▾</span>
      </button>

      {open && (
        <div className="absolute left-0 top-[calc(100%+4px)] z-40 w-56 overflow-hidden rounded-md border border-[#1a1f2e] bg-[#0f1319] shadow-xl shadow-black/40">
          <div className="border-b border-[#1a1f2e] p-1.5">
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && filtered.length > 0) select(filtered[0].value)
              }}
              placeholder="Buscar ativo…"
              className="w-full rounded border border-[#1a1f2e] bg-[#0a0d14] px-2 py-1 font-mono text-[11px] text-[#d1d4dc] placeholder:text-[#3d4455] focus:border-[#2962ff60] focus:outline-none"
            />
          </div>
          <div className="max-h-72 overflow-y-auto py-1" role="listbox">
            {filtered.length === 0 && (
              <div className="px-2.5 py-2 text-[11px] text-[#5d6477]">
                Nenhum ativo encontrado
              </div>
            )}
            {filtered.map((opt) => {
              const active = opt.value === value
              return (
                <button
                  key={opt.value}
                  type="button"
                  role="option"
                  aria-selected={active}
                  onClick={() => select(opt.value)}
                  className={`flex w-full items-center justify-between gap-2 px-2.5 py-1.5 text-left font-mono text-[11px] transition-colors ${
                    active
                      ? 'bg-[#2962ff18] text-[#e1e4ec]'
                      : 'text-[#9ca3b4] hover:bg-[#1a1f2e] hover:text-[#e1e4ec]'
                  }`}
                >
                  <span className="font-semibold">{opt.label}</span>
                  <span className="truncate text-[9px] text-[#5d6477]">
                    {/* On-chain pairs are `<network>:<address>`; show only the network */}
                    {opt.value.includes(':') ? opt.value.split(':')[0] : opt.value}
                  </span>
                </button>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
