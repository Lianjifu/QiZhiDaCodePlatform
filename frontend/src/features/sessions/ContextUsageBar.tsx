/**
 * 输入框上方的「上下文占用」条。
 *
 * 一行摘要 + 一根进度条 —— 点一下展开明细，再点收起。
 *
 * **放在输入框上方**，不放侧栏底部：这是跟"这一轮对话"有关的东西，该待在
 * 对话旁边，而不是跟别的会话列表混在一起。
 *
 * 多少刷一次：进会话时读一次，之后每 30 秒一次（标签页不可见时不读）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { sessionStatus } from '../../api/resources'
import type { SessionStatus } from '../../types/api'
import { ContextBreakdown } from './ContextBreakdown'
import { formatTokens } from './contextFormat'

const REFRESH_MS = 30_000

export default function ContextUsageBar({
  threadId,
  onOpenDetail,
}: {
  threadId: string
  /** 打开右侧完整面板 */
  onOpenDetail?: () => void
}) {
  const [data, setData] = useState<{ threadId: string; status: SessionStatus } | null>(null)
  const [open, setOpen] = useState(false)
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
    <div className="mx-auto w-full max-w-4xl px-6 pb-1.5">
      <div className="overflow-hidden rounded-xl border border-line bg-raised">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors hover:bg-hover"
        >
          <span className="text-ink-muted">上下文</span>
          <span className="text-ink-faint">
            {status ? `${percent}% · ${formatTokens(used)}/${formatTokens(limit)}` : '—'}
          </span>
          <span className="h-1 w-16 shrink-0 overflow-hidden rounded-full bg-sunken">
            <span
              className="block h-full rounded-full bg-accent transition-[width]"
              style={{ width: `${Math.min(percent, 100)}%` }}
            />
          </span>
          <span className="flex-1" />
          <span className="shrink-0 text-ink-faint">{open ? '▾ 收起' : '▸ 明细'}</span>
        </button>

        {open && status ? (
          <div className="border-t border-line px-3 py-2">
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
    </div>
  )
}
