/**
 * 侧栏底部的会话状态条。
 *
 * 一根「上下文占用」的进度条 —— 点一下就把**右侧**的状态面板拉开
 * （参考 Claude 桌面端：面板占右侧一整列，中间是对话区）。
 *
 * **多久刷一次**：进会话时读一次，之后每 30 秒读一次（标签页不可见时不读）。
 * 面板上的数字是估算值，差个几百 token 无所谓 —— 但也不能一直不更新，
 * 那根条就骗人了。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { sessionStatus } from '../../api/resources'
import type { SessionStatus } from '../../types/api'

const REFRESH_MS = 30_000

export default function SessionStatusWidget({
  threadId,
  open,
  onToggle,
}: {
  threadId: string | null
  /** 右侧面板是不是开着 */
  open: boolean
  onToggle: () => void
}) {
  const [data, setData] = useState<{ threadId: string; status: SessionStatus } | null>(null)
  /** 当前该显示哪个会话 —— 用来挡掉"上一个会话的慢响应" */
  const currentRef = useRef(threadId)
  useEffect(() => {
    currentRef.current = threadId
  }, [threadId])

  const load = useCallback(() => {
    const wanted = currentRef.current
    if (!wanted) return
    sessionStatus(wanted)
      .then((status) => {
        // 响应回来时会话可能已经切走了 —— 这次的结果作废
        if (currentRef.current !== wanted) return
        setData({ threadId: wanted, status })
      })
      .catch(() => {
        // 读不到就保持上一次的值 —— 状态条不该变成一个报错框
      })
  }, [])

  useEffect(() => {
    if (!threadId) return
    load()
    const timer = window.setInterval(() => {
      if (!document.hidden) load()
    }, REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [threadId, load])

  const status = data?.threadId === threadId ? data.status : null
  const percent = status?.context.percent ?? 0
  const used = status?.context.used ?? 0
  const limit = status?.context.limit ?? 0

  return (
    <button
      type="button"
      disabled={!threadId}
      onClick={onToggle}
      title={threadId ? '会话状态' : '还没有会话'}
      className="w-full rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-hover disabled:opacity-60"
    >
      <div className="flex items-baseline justify-between text-[11px]">
        <span className="text-ink-muted">上下文</span>
        <span className="text-ink-faint">
          {status ? `${percent}%` : '—'}
          {status ? ` · ${formatTokens(used)}/${formatTokens(limit)}` : ''}
          {open ? ' · 收起' : ''}
        </span>
      </div>
      <div className="mt-1 h-1 overflow-hidden rounded-full bg-sunken">
        <div
          className="h-full rounded-full bg-accent transition-[width]"
          style={{ width: `${Math.min(percent, 100)}%` }}
        />
      </div>
    </button>
  )
}

function formatTokens(value: number): string {
  if (value >= 1000) return `${(value / 1000).toFixed(0)}k`
  return String(value)
}
