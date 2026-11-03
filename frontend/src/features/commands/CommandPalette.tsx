/**
 * 命令面板（⌘K / Ctrl+K）。
 *
 * **全局快捷键入口** —— 页面多起来之后，「切到文件页」「开状态面板」这类动作
 * 藏在侧栏里太深，快捷键更快。
 *
 * 交互跟斜杠命令面板一套：↑↓ 选、Enter 执行、Esc 关闭。
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { CommandIcon } from '../../components/icons'
import { filterCommands } from './filter'

/** 一条命令。 */
export interface PaletteCommand {
  id: string
  title: string
  /** 副标题 —— 说明这条干什么 */
  hint?: string
  /** 快捷键提示（如 ⌘N） */
  shortcut?: string
  /** 搜索用的额外关键词 */
  keywords?: string
  icon?: ReactNode
  action: () => void
}

export default function CommandPalette({
  commands,
  onClose,
}: {
  commands: PaletteCommand[]
  onClose: () => void
}) {
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  const filtered = useMemo(() => filterCommands(commands, query), [commands, query])

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  // 过滤词变了，选中项要回到第一行。
  // **在渲染里改 state**（React 官方的"props 变了就调整 state"写法）——
  // 放 effect 里会多渲染一轮，lint 也会报 set-state-in-effect。
  const [lastQuery, setLastQuery] = useState(query)
  if (lastQuery !== query) {
    setLastQuery(query)
    setActiveIndex(0)
  }

  const run = (command: PaletteCommand | undefined) => {
    if (!command) return
    onClose()
    command.action()
  }

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (filtered.length === 0) return
      const delta = event.key === 'ArrowDown' ? 1 : -1
      setActiveIndex((index) => (index + delta + filtered.length) % filtered.length)
      return
    }
    if (event.key === 'Enter') {
      event.preventDefault()
      run(filtered[activeIndex])
      return
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      onClose()
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 p-4 pt-[12vh] backdrop-blur-[1px]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div className="w-full max-w-xl overflow-hidden rounded-2xl border border-line bg-overlay shadow-3">
        <div className="flex items-center gap-2.5 border-b border-line px-4">
          <CommandIcon className="h-4 w-4 shrink-0 text-ink-faint" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder="输入命令…"
            className="w-full bg-transparent py-3.5 text-sm text-ink outline-none placeholder:text-ink-faint"
          />
          <kbd className="shrink-0 rounded border border-line px-1.5 py-0.5 text-[10px] text-ink-faint">
            Esc
          </kbd>
        </div>

        <div className="max-h-80 overflow-y-auto py-1">
          {filtered.length === 0 ? (
            <div className="px-4 py-6 text-center text-sm text-ink-faint">没有匹配的命令</div>
          ) : (
            filtered.map((command, index) => (
              <button
                key={command.id}
                type="button"
                onMouseEnter={() => setActiveIndex(index)}
                onClick={() => run(command)}
                className={[
                  'flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors',
                  index === activeIndex ? 'bg-hover' : '',
                ].join(' ')}
              >
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-sunken text-ink-faint">
                  {command.icon ?? <CommandIcon className="h-3.5 w-3.5" />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-ink">{command.title}</span>
                  {command.hint ? (
                    <span className="block truncate text-xs text-ink-muted">{command.hint}</span>
                  ) : null}
                </span>
                {command.shortcut ? (
                  <kbd className="shrink-0 rounded border border-line px-1.5 py-0.5 text-[10px] text-ink-faint">
                    {command.shortcut}
                  </kbd>
                ) : null}
              </button>
            ))
          )}
        </div>

        <div className="border-t border-line px-4 py-1.5 text-[11px] text-ink-faint">
          ↑↓ 选择 · Enter 执行 · Esc 关闭
        </div>
      </div>
    </div>
  )
}
