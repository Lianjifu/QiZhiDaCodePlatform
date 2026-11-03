/**
 * 上下文占用的明细。
 *
 * 输入框上方那根条展开后是它，右侧面板里的「上下文」卡片也是它 —— 同一份
 * 渲染，两处用。
 */
import { useState } from 'react'

import type { ContextPart, ContextUsage } from '../../types/api'
import { PART_COLORS, formatTokens } from './contextFormat'

/** 可展开的明细（每个 MCP 工具、每个记忆文件）。 */
export function DetailToggle({ items }: { items: string[] }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="ml-4 mt-0.5">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="text-[11px] text-ink-faint transition-colors hover:text-ink-muted"
      >
        {open ? '▾' : '▸'} {items.length} 项
      </button>
      {open ? (
        <ul className="mt-0.5 space-y-0.5">
          {items.map((item) => (
            <li key={item} className="truncate text-[11px] text-ink-faint">
              {item}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

/** 占用明细：一行一块，带颜色点与百分比。 */
export function ContextBreakdown({ context }: { context: ContextUsage }) {
  return (
    <ul className="space-y-1">
      {context.parts.map((part) => (
        <ContextPartRow key={part.key} part={part} />
      ))}
      <li className="flex items-center gap-2 text-xs">
        <span className="h-2 w-2 shrink-0 rounded-sm bg-transparent" />
        <span className="min-w-0 flex-1 truncate text-ink-muted">剩余空间</span>
        <span className="shrink-0 text-ink-faint">{formatTokens(context.free)}</span>
        <span className="w-10 shrink-0 text-right text-ink-faint">
          {Math.max(100 - context.percent, 0).toFixed(1)}%
        </span>
      </li>
    </ul>
  )
}

function ContextPartRow({ part }: { part: ContextPart }) {
  return (
    <li>
      <div className="flex items-center gap-2 text-xs">
        <span
          className={`h-2 w-2 shrink-0 rounded-sm ${PART_COLORS[part.key] ?? 'bg-ink-faint'}`}
        />
        <span className="min-w-0 flex-1 truncate text-ink-muted">{part.label}</span>
        <span className="shrink-0 text-ink-faint">{formatTokens(part.tokens)}</span>
        <span className="w-10 shrink-0 text-right text-ink-faint">{part.percent}%</span>
      </div>
      {/* 有明细的可以展开看看具体是什么 */}
      {part.items && part.items.length > 0 ? <DetailToggle items={part.items} /> : null}
    </li>
  )
}
