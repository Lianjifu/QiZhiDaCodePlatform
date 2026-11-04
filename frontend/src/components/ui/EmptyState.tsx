/**
 * 空状态。
 *
 * 「这里还没有东西」不只是一句话 —— 说清楚这是什么地方、接下来能干什么。
 */
import type { ReactNode } from 'react'

export default function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string
  description?: string
  action?: ReactNode
  icon?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      {icon ? (
        <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-xl bg-sunken text-ink-faint">
          {icon}
        </div>
      ) : null}
      <div className="text-sm font-medium text-ink">{title}</div>
      {description ? (
        <p className="mt-1 max-w-sm text-xs leading-relaxed text-ink-faint">{description}</p>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  )
}
