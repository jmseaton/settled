/**
 * The Settled mark: a price line that swings, then settles onto a level,
 * with the trade ticked off beneath it.
 *
 * The same artwork ships as `public/favicon.svg` — the two are kept in step
 * by hand, so any change to the path data belongs in both. The colours are
 * deliberately literal rather than `var(--accent)`/`var(--green)`: a logo
 * that restyles itself with the theme is no longer a logo.
 */
export function Logo({ size = 30, className }: { size?: number; className?: string }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 512 512"
      role="img"
      aria-label="Settled"
    >
      <defs>
        <linearGradient id="settled-tile" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#262b36" />
          <stop offset="1" stopColor="#1d212b" />
        </linearGradient>
      </defs>
      <rect width="512" height="512" rx="116" fill="url(#settled-tile)" />
      <rect
        x="1"
        y="1"
        width="510"
        height="510"
        rx="115"
        fill="none"
        stroke="#ffffff"
        strokeOpacity="0.07"
        strokeWidth="2"
      />
      <path
        d="M40 285 L86 285 C106 285 116 225 136 225 C156 225 212 315 232 315 C252 315 300 227 320 227 L472 227"
        fill="none"
        stroke="#3b82f6"
        strokeWidth="24"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M420 287 L440 305 L474 268"
        fill="none"
        stroke="#3ecf5f"
        strokeWidth="20"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
