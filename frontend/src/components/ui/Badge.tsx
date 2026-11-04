/**
 * 小标签。
 *
 * 状态、类别、内置标记都用它 —— 圆角小片 + 淡底色，跟正文拉开距离。
 */
import type { ReactNode } from 'react'

const TONES = {
  neutral: 'bg-sunken text-ink-muted',
  accent: 'bg-accent-soft text-accent',
  success: 'bg-success-soft text-success',
  danger: 'bg-danger-soft text-danger',
  warning: 'bg-warning-soft text-warning',
} as const

export default function Badge({
  children,
  tone = 'neutral',
  className,
}: {
  children: ReactNode
  tone?: keyof typeof TONES
  className?: string
}) {
  return (
    <span
      className={[
        'inline-flex shrink-0 items-center rounded-md px-1.5 py-0.5 text-[10px] font-medium leading-4 whitespace-nowrap',
        TONES[tone],
        className ?? '',
      ].join(' ')}
    >
      {children}
    </span>
  )
}
