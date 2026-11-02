/**
 * 主题切换：跟随系统 / 浅色 / 深色。
 *
 * 放在侧栏底部 —— 设置类的东西就该在那儿。
 */
import { useEffect, useRef, useState } from 'react'

import { useTheme, type ThemeMode } from '../../theme/ThemeProvider'

const OPTIONS: { mode: ThemeMode; label: string }[] = [
  { mode: 'system', label: '跟随系统' },
  { mode: 'light', label: '浅色' },
  { mode: 'dark', label: '深色' },
]

const ICONS: Record<ThemeMode, string> = {
  system: '◐',
  light: '☀',
  dark: '☾',
}

export default function ThemeToggle() {
  const { mode, setMode } = useTheme()
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  // 点外面就收起
  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [open])

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        title="切换主题"
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-ink-muted transition-colors hover:bg-hover hover:text-ink"
      >
        <span className="w-4 text-center">{ICONS[mode]}</span>
        <span>{OPTIONS.find((option) => option.mode === mode)?.label}</span>
      </button>

      {open && (
        <div className="absolute bottom-full left-0 mb-1 w-40 overflow-hidden rounded-lg border border-line bg-overlay shadow-lg">
          {OPTIONS.map((option) => (
            <button
              key={option.mode}
              type="button"
              onClick={() => {
                setMode(option.mode)
                setOpen(false)
              }}
              className={[
                'flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-hover',
                option.mode === mode ? 'text-ink' : 'text-ink-muted',
              ].join(' ')}
            >
              <span className="w-4 text-center">{ICONS[option.mode]}</span>
              <span className="flex-1">{option.label}</span>
              {option.mode === mode && <span className="text-xs">✓</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
