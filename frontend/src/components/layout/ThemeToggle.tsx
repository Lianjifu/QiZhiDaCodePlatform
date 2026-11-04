/**
 * 主题切换：跟随系统 / 浅色 / 深色。
 *
 * 放在侧栏底部 —— 设置类的东西就该在那儿。
 */
import { useEffect, useRef, useState } from 'react'

import { AutoIcon, MoonIcon, SunIcon } from '../../components/icons'
import { useTheme, type ThemeMode } from '../../theme/ThemeProvider'

const OPTIONS: { mode: ThemeMode; label: string }[] = [
  { mode: 'system', label: '跟随系统' },
  { mode: 'light', label: '浅色' },
  { mode: 'dark', label: '深色' },
]

function Icon({ mode }: { mode: ThemeMode }) {
  const className = 'h-4 w-4'
  if (mode === 'light') return <SunIcon className={className} />
  if (mode === 'dark') return <MoonIcon className={className} />
  return <AutoIcon className={className} />
}

export default function ThemeToggle({ compact = false }: { compact?: boolean }) {
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
        className={[
          'flex items-center gap-2.5 rounded-lg text-sm text-ink-muted transition-colors hover:bg-hover hover:text-ink',
          // **紧凑模式** —— 侧栏折起来的时候只剩一个图标
          compact ? 'mx-auto h-9 w-9 justify-center' : 'w-full px-2.5 py-2',
        ].join(' ')}
      >
        <Icon mode={mode} />
        {compact ? null : <span>{OPTIONS.find((option) => option.mode === mode)?.label}</span>}
      </button>

      {open && (
        <div
          className={[
            'absolute bottom-full left-0 mb-1 w-40 overflow-hidden rounded-lg border border-line bg-overlay shadow-lg',
          ].join(' ')}
        >
          {OPTIONS.map((option) => (
            <button
              key={option.mode}
              type="button"
              onClick={() => {
                setMode(option.mode)
                setOpen(false)
              }}
              className={[
                'flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm transition-colors hover:bg-hover',
                option.mode === mode ? 'text-ink' : 'text-ink-muted',
              ].join(' ')}
            >
              <Icon mode={option.mode} />
              <span className="flex-1">{option.label}</span>
              {option.mode === mode ? <span className="text-xs">✓</span> : null}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
