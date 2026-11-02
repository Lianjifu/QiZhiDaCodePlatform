/**
 * 少量内联图标。
 *
 * 只放真正会用到的 —— 界面上的小图标用 emoji 会跟着系统字体变样式，深浅色下
 * 也对不齐，所以统一用 SVG。
 */

interface IconProps {
  className?: string
}

function Svg({
  className,
  children,
}: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className ?? 'h-4 w-4'}
      aria-hidden="true"
    >
      {children}
    </svg>
  )
}

export function PlusIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M8 3.5v9M3.5 8h9" />
    </Svg>
  )
}

export function PaperclipIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M11.5 7.5 6.8 12.2a2.6 2.6 0 0 1-3.7-3.7l5-5a1.9 1.9 0 0 1 2.7 2.7l-5 5a1.2 1.2 0 0 1-1.7-1.7l4.4-4.4" />
    </Svg>
  )
}

export function AtIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M10.4 5.9a2.9 2.9 0 1 0 0 4.2c.9 0 1.5-.7 1.5-1.6V8a3.9 3.9 0 1 0-1.2 2.9" />
      <path d="M10.4 5.9v4.2" />
    </Svg>
  )
}

export function SlashIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M5.5 2.5 4 13.5M11.5 2.5 10 13.5M2.6 5.8h11M2.4 10.2h11" />
    </Svg>
  )
}

export function FolderIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M2.5 4.5A1 1 0 0 1 3.5 3.5h2.4l1.4 1.8h5.2a1 1 0 0 1 1 1v5.2a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1z" />
    </Svg>
  )
}

export function FileIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M9 2.5H4.5a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1h7a1 1 0 0 0 1-1V6z" />
      <path d="M9 2.5V6h3.5" />
    </Svg>
  )
}

export function ChevronIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4.5 6.5 8 10l3.5-3.5" />
    </Svg>
  )
}

export function ArrowUpIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M8 12.5v-9M4 7.5 8 3.5l4 4" />
    </Svg>
  )
}

export function StopIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="4.5" y="4.5" width="7" height="7" rx="1" fill="currentColor" stroke="none" />
    </Svg>
  )
}
