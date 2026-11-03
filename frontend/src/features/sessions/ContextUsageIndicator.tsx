/**
 * 输入框底栏里的「上下文占用」指示器。
 *
 * **它就在底栏里，模型选择的左边** —— 不是输入框上方单独一根条。
 * 上下文是这一轮对话的事，就该待在输入框这一行，而不是跟内容区分开放。
 *
 * 点一下展开明细（各占多少），再点收起；里面还有一条「在右侧打开完整面板」。
 *
 * 多少刷一次：进会话时读一次，之后每 30 秒一次（标签页不可见时不读）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { sessionStatus } from '../../api/resources'
import type { SessionStatus } from '../../types/api'
import { ContextBreakdown } from './ContextBreakdown'
import { formatTokens } from './contextFormat'

const REFRESH_MS = 30_000

export default function ContextUsageIndicator({
  threadId,
  open,
  onToggle,
  onOpenDetail,
}: {
  threadId: string
  /** 展开与否由外层管 —— 输入框里同时只能开一个浮层 */
  open: boolean
  onToggle: () => void
  /** 打开右侧完整面板 */
  onOpenDetail?: () => void
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
        if (currentRef.current !== wanted) return
        setData({ threadId: wanted, status })
      })
      .catch(() => {
        // 读不到就保持上一次的值 —— 不该变成一个报错框
      })
  }, [])

  useEffect(() => {
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
    <div className="relative">
      <button
        type="button"
        onClick={onToggle}
        title="上下文占用"
        className="flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs text-ink-faint transition-colors hover:bg-hover hover:text-ink-muted"
      >
        <span className="shrink-0">上下文</span>
        <span className="shrink-0 text-ink-muted">{status ? `${percent}%` : '—'}</span>
        <span className="h-1 w-10 shrink-0 overflow-hidden rounded-full bg-sunken">
          <span
            className="block h-full rounded-full bg-accent transition-[width]"
            style={{ width: `${Math.min(percent, 100)}%` }}
          />
        </span>
      </button>

      {open && status ? (
        <div className="absolute bottom-full right-0 z-20 mb-2 w-80 rounded-xl border border-line bg-overlay p-3 shadow-xl">
          <div className="mb-1.5 flex items-baseline justify-between text-xs">
            <span className="font-medium text-ink">上下文占用</span>
            <span className="text-ink-faint">
              {formatTokens(used)} / {formatTokens(limit)}
            </span>
          </div>
          <ContextBreakdown context={status.context} />
          <p className="mt-2 text-[10px] leading-relaxed text-ink-faint">
            按 4 字符约 1 token 估算，不是精确的 token 数。
          </p>
          {onOpenDetail ? (
            <button
              type="button"
              onClick={onOpenDetail}
              className="mt-1.5 text-[11px] text-accent hover:underline"
            >
              在右侧打开完整面板
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
