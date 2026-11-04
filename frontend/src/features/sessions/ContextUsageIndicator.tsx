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
import { ContextBreakdown } from './ContextBreakdown'
import { formatTokens } from './contextFormat'
import { useRefreshOnIdle, useSessionStatus } from './useSessionStatus'

export default function ContextUsageIndicator({
  threadId,
  busy,
  open,
  onToggle,
  onOpenDetail,
}: {
  threadId: string
  /** 这一轮还在跑吗 —— 跑完了要重新读一次上下文 */
  busy: boolean
  /** 展开与否由外层管 —— 输入框里同时只能开一个浮层 */
  open: boolean
  onToggle: () => void
  /** 打开右侧完整面板 */
  onOpenDetail?: () => void
}) {
  // **跟右侧的完整面板共用一份数据** —— 同一个接口读两遍，数字还可能对不上
  const { status, refresh } = useSessionStatus(threadId)

  /**
   * 这一轮跑完了就重新读一次。
   *
   * 光靠 30 秒的轮询太慢 —— 一轮对话刚结束，占用数字还停在上一轮，看着像没变。
   */
  useRefreshOnIdle(busy, refresh)

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
