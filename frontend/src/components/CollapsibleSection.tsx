import { useState, type ReactNode } from 'react'

interface CollapsibleSectionProps {
  title: string
  children: ReactNode
  defaultOpen?: boolean
  count?: number
  trailing?: ReactNode
}

export function CollapsibleSection({
  title,
  children,
  defaultOpen = true,
  count,
  trailing,
}: CollapsibleSectionProps) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <div className="flex flex-col">
      <button
        onClick={() => setOpen((v) => !v)}
        className="group mb-2 flex w-full items-center gap-1.5 text-left"
      >
        <svg
          viewBox="0 0 10 10"
          className="h-2.5 w-2.5 flex-none text-[#3d4455] transition-transform duration-200 group-hover:text-[#5d6477]"
          style={{ transform: open ? 'rotate(90deg)' : 'rotate(0deg)' }}
        >
          <path d="M3 1 L7 5 L3 9" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span className="text-[10px] font-semibold uppercase tracking-[0.15em] text-[#5d6477] group-hover:text-[#9ca3b4] transition-colors duration-200">
          {title}
        </span>
        {count != null && count > 0 && (
          <span className="rounded-full bg-[#1a1f2e] px-1.5 py-[1px] text-[8px] font-bold tabular-nums text-[#5d6477]">
            {count}
          </span>
        )}
        {trailing && <div className="ml-auto">{trailing}</div>}
      </button>
      {open && children}
    </div>
  )
}
