/**
 * 审批卡片。
 *
 * agent 要调一个需要人批的工具时，流里推来 `pending_approval` 事件，
 * 这里弹卡片让人批。批准后图继续跑，agent 接着往下做。
 *
 * **一个调用一条决定**：模型一轮里可以并行发好几个工具调用，它们会**一起**
 * 挂起。全部批准还是逐条挑，用户说了算 —— 逐条挑的时候每条旁边给一个
 * 「批准 / 拒绝」开关，默认全批准。
 */
import { useState } from 'react'

import { AlertIcon } from '../../components/icons'
import type { PendingApproval } from '../../types/api'
import { riskLabel, riskTier, tierLabel } from '../capabilities/risk'
import { toolLabel } from './toolSummary'

/** 一条挂起调用的处置：批准还是拒绝。 */
export type ApprovalDecision = 'approve' | 'reject'

export default function ApprovalCard({
  pending,
  busy,
  decision,
  onApprove,
  onReject,
}: {
  pending: PendingApproval[]
  busy: boolean
  /** 已经批过了（批准 / 拒绝）—— 卡片换成结果，别让用户对着灰按钮干等 */
  decision?: 'approved' | 'rejected' | null
  /** 全部批准（或按逐条勾选提交） */
  onApprove: (decisions?: ApprovalDecision[]) => void
  /** 全部拒绝 */
  onReject: (decisions?: ApprovalDecision[]) => void
}) {
  // 逐条勾选的结果 —— 下标对应 pending 的下标。
  // **null = 还没选**，用默认的批准。
  const [picks, setPicks] = useState<(ApprovalDecision | null)[]>([])

  // 待批清单换了（新的一轮挂起）就重新开始选 —— 旧的勾选不该带过去。
  //
  // **在渲染里调整 state**，不用 effect：effect 里 setState 会多跑一次渲染
  // （React 官方 lint 也会拦）。pending 是消息里的数组引用，换一轮就是新引用。
  const [lastPending, setLastPending] = useState(pending)
  if (lastPending !== pending) {
    setLastPending(pending)
    setPicks([])
  }

  if (pending.length === 0) return null

  // **批过了就换成结果** —— 后端 resume 是同步跑完整张图的，可能几十秒。
  // 光把按钮置灰，用户会以为页面卡死了。
  if (decision) {
    return (
      <div className="my-3 rounded-xl border border-line bg-sunken/60 px-4 py-3 text-sm text-ink-muted">
        {decision === 'approved' ? (
          <span>已批准，agent 继续执行中…</span>
        ) : (
          <span>已拒绝，这个调用不会再执行。</span>
        )}
      </div>
    )
  }

  // 模型一轮里可以并行发好几个工具调用，它们会**一起**挂起等人批。
  const many = pending.length > 1

  /** 逐条勾选时，提交的决定列表。 */
  const submitDecisions = () => {
    const decisions = pending.map((_, index) => picks[index] ?? 'approve')
    onApprove(decisions)
  }

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
              {/* **工具名用人话** —— 卡片上写 write_file、工具块上写「写入文件」，
                  两处对不上用户会以为是两个东西。 */}
              <span className="rounded-md bg-base px-1.5 py-0.5 text-ink">
                {toolLabel(item.tool)}
              </span>
              {item.risk ? (
                <span className="rounded-md bg-warning/15 px-1.5 py-0.5 text-warning">
                  风险：{tierLabel(riskTier(item.risk))}档 · {riskLabel(item.risk)}
                </span>
              ) : null}
              {/* **逐条开关** —— 只在有多个调用时出现：一条一个决定，
                  用户可以只批想批的那条。 */}
              {many ? (
                <span className="ml-auto flex items-center gap-1">
                  <PickButton
                    active={picks[index] !== 'reject'}
                    onClick={() =>
                      setPicks((prev) => {
                        const next = [...prev]
                        next[index] = 'approve'
                        return next
                      })
                    }
                  >
                    批准
                  </PickButton>
                  <PickButton
                    active={picks[index] === 'reject'}
                    onClick={() =>
                      setPicks((prev) => {
                        const next = [...prev]
                        next[index] = 'reject'
                        return next
                      })
                    }
                  >
                    拒绝
                  </PickButton>
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
          onClick={many ? submitDecisions : () => onApprove()}
          className="rounded-lg bg-accent px-3.5 py-1.5 text-xs font-medium text-accent-text transition-colors hover:bg-accent-hover disabled:opacity-50"
        >
          {many ? '按上面的选择提交' : '批准'}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onReject()}
          className="rounded-lg border border-line bg-base px-3.5 py-1.5 text-xs font-medium text-ink-muted transition-colors hover:bg-hover disabled:opacity-50"
        >
          {many ? '全部拒绝' : '拒绝'}
        </button>
      </div>
    </div>
  )
}

/** 逐条审批的小开关。 */
function PickButton({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={
        active
          ? 'rounded-md bg-accent px-1.5 py-0.5 font-medium text-accent-text'
          : 'rounded-md border border-line bg-base px-1.5 py-0.5 text-ink-faint hover:bg-hover'
      }
    >
      {children}
    </button>
  )
}
