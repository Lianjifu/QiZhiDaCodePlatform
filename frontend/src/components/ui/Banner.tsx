/**
 * 提示条。
 *
 * 错误、成功、说明都用它 —— 页面里别各写各的 colored box。
 */
import type { ReactNode } from 'react'

const TONES = {
  info: 'border-line bg-raised text-ink-muted',
  success: 'border-success/30 bg-success-soft text-success',
  danger: 'border-danger/30 bg-danger-soft text-danger',
  warning: 'border-warning/30 bg-warning-soft text-warning',
} as const

/** 每种语气一个记号 —— 颜色之外还有别的线索（色弱用户也分得清）。 */
const MARKS = {
  info: 'ℹ',
  success: '✓',
  danger: '!',
  warning: '!',
} as const

export default function Banner({
  children,
  tone = 'info',
  className,
}: {
  children: ReactNode
  tone?: keyof typeof TONES
  className?: string
}) {
  return (
    <div
      role={tone === 'danger' ? 'alert' : 'status'}
      className={[
        'flex items-start gap-2 rounded-xl border px-3 py-2.5 text-sm',
        TONES[tone],
        className ?? '',
      ].join(' ')}
    >
      <span aria-hidden="true" className="mt-px shrink-0 text-xs font-semibold opacity-70">
        {MARKS[tone]}
      </span>
      <span className="min-w-0 flex-1">{children}</span>
    </div>
  )
}
