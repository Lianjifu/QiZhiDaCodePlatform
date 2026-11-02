/**
 * 审批卡片。
 *
 * agent 要调一个需要人批的工具时，流里推来 `pending_approval` 事件，
 * 这里弹卡片让人批。批准后图继续跑，agent 接着往下做。
 */
import type { PendingApproval } from '../../types/api'

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

  return (
    <div className="my-2 rounded-lg border border-warning bg-warning-soft px-3 py-3">
      <div className="text-sm font-medium text-ink">需要你批准</div>
      {pending.map((item, index) => (
        <div key={`${item.tool}-${index}`} className="mt-2 text-xs text-ink-muted">
          <div className="flex items-center gap-2">
            <span className="rounded bg-base px-1.5 py-0.5 font-mono">{item.tool}</span>
            {item.risk ? <span className="text-warning">风险：{item.risk}</span> : null}
          </div>
          {item.message ? <div className="mt-1 text-ink">{item.message}</div> : null}
          {item.reasons.length > 0 && (
            <ul className="mt-1 list-disc pl-4">
              {item.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          )}
          <details className="mt-1">
            <summary className="cursor-pointer text-ink-faint">调用参数</summary>
            <pre className="mt-1 max-h-32 overflow-auto rounded bg-base p-2 text-[11px]">
              {JSON.stringify(item.arguments, null, 2)}
            </pre>
          </details>
        </div>
      ))}
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={onApprove}
          className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-accent-text transition-colors hover:bg-accent-hover disabled:opacity-50"
        >
          批准
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onReject}
          className="rounded-lg border border-line bg-base px-3 py-1.5 text-xs font-medium text-ink-muted transition-colors hover:bg-hover disabled:opacity-50"
        >
          拒绝
        </button>
      </div>
    </div>
  )
}
