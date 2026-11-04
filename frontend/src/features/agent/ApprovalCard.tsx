/**
 * 审批卡片。
 *
 * agent 要调一个需要人批的工具时，流里推来 `pending_approval` 事件，
 * 这里弹卡片让人批。批准后图继续跑，agent 接着往下做。
 */
import { AlertIcon } from '../../components/icons'
import type { PendingApproval } from '../../types/api'
import { riskLabel, riskTier, tierLabel } from '../capabilities/risk'

export default function ApprovalCard({
  pending,
  busy,
  onApprove,
  onReject,
}: {
  pending: PendingApproval[]
  busy: boolean
  onApprove: () => void
  onReject: () => void
}) {
  if (pending.length === 0) return null

  // 模型一轮里可以并行发好几个工具调用，它们会**一起**挂起等人批。
  // 批准按钮一次管全部 —— 所以数量要说清楚，别让人以为只批了一个。
  const many = pending.length > 1

  return (
    <div className="my-3 overflow-hidden rounded-xl border border-warning/60 bg-warning-soft">
      <div className="flex items-center gap-2 border-b border-warning/30 px-4 py-2.5">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-warning/15">
          <AlertIcon className="h-3.5 w-3.5 text-warning" />
        </span>
        <span className="text-sm font-medium text-ink">
          需要你批准
          {many ? `（${pending.length} 个调用）` : ''}
        </span>
      </div>

      <div className="px-4 py-3">
        {pending.map((item, index) => (
          <div
            key={`${item.tool}-${index}`}
            className={index > 0 ? 'mt-3 border-t border-warning/25 pt-3' : ''}
          >
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="rounded-md bg-base px-1.5 py-0.5 font-mono text-ink">
                {item.tool}
              </span>
              {item.risk ? (
                <span className="rounded-md bg-warning/15 px-1.5 py-0.5 text-warning">
                  风险：{tierLabel(riskTier(item.risk))}档 · {riskLabel(item.risk)}
                </span>
              ) : null}
            </div>
            {item.message ? (
              <div className="mt-1.5 text-sm leading-relaxed text-ink">{item.message}</div>
            ) : null}
            {item.reasons.length > 0 && (
              <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-xs text-ink-muted">
                {item.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            )}
            <details className="mt-2">
              <summary className="cursor-pointer text-xs text-ink-faint">调用参数</summary>
              <pre className="mt-1 max-h-32 overflow-auto rounded-lg bg-base p-2 text-[11px]">
                {JSON.stringify(item.arguments, null, 2)}
              </pre>
            </details>
          </div>
        ))}
      </div>

      <div className="flex gap-2 border-t border-warning/30 bg-base/40 px-4 py-2.5">
        <button
          type="button"
          disabled={busy}
          onClick={onApprove}
          className="rounded-lg bg-accent px-3.5 py-1.5 text-xs font-medium text-accent-text transition-colors hover:bg-accent-hover disabled:opacity-50"
        >
          {many ? '全部批准' : '批准'}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onReject}
          className="rounded-lg border border-line bg-base px-3.5 py-1.5 text-xs font-medium text-ink-muted transition-colors hover:bg-hover disabled:opacity-50"
        >
          {many ? '全部拒绝' : '拒绝'}
        </button>
      </div>
    </div>
  )
}
