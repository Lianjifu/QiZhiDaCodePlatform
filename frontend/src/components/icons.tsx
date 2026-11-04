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

export function TerminalIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="2.5" y="3.5" width="11" height="9" rx="1.5" />
      <path d="M5 7l2 1.5L5 10M8.5 10.5H11" />
    </Svg>
  )
}

export function SearchIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="7" cy="7" r="3.75" />
      <path d="M10 10l3 3" />
    </Svg>
  )
}

export function PencilIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M11 2.8l2.2 2.2-7.6 7.6-3 .8.8-3z" />
      <path d="M9.4 4.4l2.2 2.2" />
    </Svg>
  )
}

export function CheckIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M3 8.5 6.5 12 13 4.5" />
    </Svg>
  )
}

export function AlertIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M8 2.8 14 13H2z" />
      <path d="M8 6.6v3M8 11.4h.01" />
    </Svg>
  )
}

export function CopyIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" />
      <path d="M10.5 5.5v-1a1 1 0 0 0-1-1h-5a1 1 0 0 0-1 1v5a1 1 0 0 0 1 1h1" />
    </Svg>
  )
}

export function ChatIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M13.5 9.5a1.5 1.5 0 0 1-1.5 1.5H6l-3 2.5v-9A1.5 1.5 0 0 1 4.5 3h7.5A1.5 1.5 0 0 1 13.5 4.5z" />
    </Svg>
  )
}

export function LayersIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M8 2.5 14 5.5 8 8.5 2 5.5z" />
      <path d="M2.8 8.8 8 11.5l5.2-2.7M2.8 11.6 8 14.3l5.2-2.7" />
    </Svg>
  )
}

export function PlugIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M6 2.5v3M10 2.5v3" />
      <path d="M4.5 5.5h7v3a3.5 3.5 0 0 1-7 0z" />
      <path d="M8 12v1.5" />
    </Svg>
  )
}

export function SunIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="8" cy="8" r="3" />
      <path d="M8 1.5v1.2M8 13.3v1.2M14.5 8h-1.2M2.7 8H1.5M12.6 3.4l-.85.85M4.25 11.75l-.85.85M12.6 12.6l-.85-.85M4.25 4.25l-.85-.85" />
    </Svg>
  )
}

export function MoonIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M13.5 9.6A5.8 5.8 0 0 1 6.4 2.5a5.9 5.9 0 1 0 7.1 7.1z" />
    </Svg>
  )
}

export function AutoIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="8" cy="8" r="5.5" />
      <path d="M8 2.5v11" />
    </Svg>
  )
}

export function ShieldIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M8 1.8 2.8 3.6v4.1c0 3 2.2 5.6 5.2 6.5 3-0.9 5.2-3.5 5.2-6.5V3.6z" />
      <path d="M5.9 8 7.4 9.5l3-3.4" />
    </Svg>
  )
}

export function RefreshIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M13.5 8a5.5 5.5 0 1 1-1.7-3.9" />
      <path d="M13.5 2.5V5H11" />
    </Svg>
  )
}

export function BranchIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="4.5" cy="3.5" r="1.8" />
      <circle cx="4.5" cy="12.5" r="1.8" />
      <circle cx="11.5" cy="6" r="1.8" />
      <path d="M4.5 5.3v5.4M11.5 7.8c0 2.4-1.7 3.4-3.6 4" />
    </Svg>
  )
}

export function KeyboardIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="1.5" y="4" width="13" height="8" rx="1.5" />
      <path d="M4 6.5h.01M6.5 6.5h.01M9 6.5h.01M11.5 6.5h.01M4.5 9.5h7" />
    </Svg>
  )
}

export function CommandIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M5.5 2.5a1.75 1.75 0 1 0 1.75 1.75h1.5a1.75 1.75 0 1 0 1.75-1.75A1.75 1.75 0 1 0 8.75 4.25v7.5A1.75 1.75 0 1 0 10.5 13.5a1.75 1.75 0 1 0-1.75-1.75h-1.5a1.75 1.75 0 1 0-1.75 1.75A1.75 1.75 0 1 0 7.25 11.75v-7.5" />
    </Svg>
  )
}

export function SparkIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M8 2.5 9.4 6.6 13.5 8l-4.1 1.4L8 13.5 6.6 9.4 2.5 8l4.1-1.4z" />
    </Svg>
  )
}

/** 侧栏：左边一栏 + 一条竖线（折叠 / 展开都用它，靠外面的容器调转方向）。 */
export function PanelLeftIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="1.75" y="2.75" width="12.5" height="10.5" rx="1.75" />
      <path d="M6.25 2.75v10.5" />
    </Svg>
  )
}
