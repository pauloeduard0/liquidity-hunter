export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      {/* Outer ring */}
      <circle cx="16" cy="16" r="14" stroke="#2962ff" strokeWidth="1.5" opacity="0.5" />
      <circle cx="16" cy="16" r="10" stroke="#2962ff" strokeWidth="1" opacity="0.3" />

      {/* Crosshair lines */}
      <line x1="16" y1="2" x2="16" y2="10" stroke="#2962ff" strokeWidth="1.5" strokeLinecap="round" />
      <line x1="16" y1="22" x2="16" y2="30" stroke="#2962ff" strokeWidth="1.5" strokeLinecap="round" />
      <line x1="2" y1="16" x2="10" y2="16" stroke="#2962ff" strokeWidth="1.5" strokeLinecap="round" />
      <line x1="22" y1="16" x2="30" y2="16" stroke="#2962ff" strokeWidth="1.5" strokeLinecap="round" />

      {/* Center diamond (liquidity target) */}
      <path
        d="M16 12L20 16L16 20L12 16Z"
        fill="#2962ff"
        fillOpacity="0.25"
        stroke="#2962ff"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />

      {/* Inner dot */}
      <circle cx="16" cy="16" r="1.5" fill="#2962ff" />
    </svg>
  )
}
