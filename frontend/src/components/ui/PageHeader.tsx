/**
 * 页面头。
 *
 * 标题 + 一句话说明 + 右侧动作。**窄屏上竖着排** —— 横排的话按钮会被挤成
 * 两行字，或者把标题压扁。
 */
import type { ReactNode } from 'react'

export default function PageHeader({
  title,
  description,
  actions,
  meta,
}: {
  title: string
  description?: ReactNode
  /** 右上角的动作（按钮、链接） */
  actions?: ReactNode
  /** 标题下面那行小字里的补充 */
  meta?: ReactNode
}) {
  return (
    <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight text-ink sm:text-lg">{title}</h1>
        {description ? (
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-ink-muted">{description}</p>
        ) : null}
        {meta ? <div className="mt-2 text-xs text-ink-faint">{meta}</div> : null}
      </div>
      {actions ? (
        <div className="flex shrink-0 flex-wrap items-center gap-2 sm:justify-end">{actions}</div>
      ) : null}
    </header>
  )
}
