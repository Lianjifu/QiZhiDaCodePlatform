/**
 * 侧栏底部的会话状态条。
 *
 * 一根「上下文占用」的进度条 —— 点开就是 {@link SessionStatusPanel}。
 *
 * **多久刷一次**：进会话时读一次，之后每 30 秒读一次（标签页不可见时不读）。
 * 面板上的数字是估算值，差个几百 token 无所谓 —— 但也不能一直不更新，
 * 那根条就骗人了。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { sessionStatus } from '../../api/resources'
import type { SessionStatus } from '../../types/api'
import SessionStatusPanel from './SessionStatusPanel'

const REFRESH_MS = 30_000

export default function SessionStatusWidget({ threadId }: { threadId: string | null }) {
  const [data, setData] = useState<{ threadId: string; status: SessionStatus } | null>(null)
  const [open, setOpen] = useState(false)
  const wrapperRef = useRef<HTMLDivElement>(null)

  const load = useCallback(() => {
    if (!threadId) return
    sessionStatus(threadId)
      .then((status) => setData({ threadId, status }))
      .catch(() => {
        // 读不到就保持上一次的值 —— 状态条不该变成一个报错框
      })
  }, [threadId])

  useEffect(() => {
    if (!threadId) return
    load()
    const timer = window.setInterval(() => {
      if (!document.hidden) load()
    }, REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [threadId, load])

  // 点外面收起面板
  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: MouseEvent) => {
      if (!wrapperRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [open])

  const status = data?.threadId === threadId ? data.status : null
  const percent = status?.context.percent ?? 0
  const used = status?.context.used ?? 0
  const limit = status?.context.limit ?? 0

  return (
    <div ref={wrapperRef} className="relative">
      {open && threadId ? (
        <div className="absolute bottom-full left-0 z-30 mb-2">
          <SessionStatusPanel threadId={threadId} />
        </div>
      ) : null}

      <button
        type="button"
        disabled={!threadId}
        onClick={() => setOpen((value) => !value)}
        title={threadId ? '会话状态' : '还没有会话'}
        className="w-full rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-hover disabled:opacity-60"
      >
        <div className="flex items-baseline justify-between text-[11px]">
          <span className="text-ink-muted">{threadId ? '上下文' : '上下文'}</span>
          <span className="text-ink-faint">
            {status ? `${percent}%` : '—'}
            {status ? ` · ${formatTokens(used)}/${formatTokens(limit)}` : ''}
          </span>
        </div>
        <div className="mt-1 h-1 overflow-hidden rounded-full bg-sunken">
          <div
            className="h-full rounded-full bg-accent transition-[width]"
            style={{ width: `${Math.min(percent, 100)}%` }}
          />
        </div>
      </button>
    </div>
  )
}

function formatTokens(value: number): string {
  if (value >= 1000) return `${(value / 1000).toFixed(0)}k`
  return String(value)
}
