/**
 * 按钮。
 *
 * 五档：默认（描边）、主操作（实心强调）、次要（淡主色底）、危险（删除）、
 * 文字（次要动作）。
 *
 * **高度是定死的** —— 同一个页面里大小不一的按钮看着像没做完。
 */
import type { ButtonHTMLAttributes } from 'react'

const VARIANTS = {
  default: 'border border-line bg-base text-ink-muted hover:bg-hover hover:text-ink',
  primary: 'bg-accent text-accent-text hover:bg-accent-hover shadow-1',
  subtle: 'bg-accent-soft text-accent hover:bg-accent-muted',
  danger: 'border border-danger/40 bg-danger-soft text-danger hover:bg-danger/15',
  ghost: 'text-ink-faint hover:bg-hover hover:text-ink',
} as const

const SIZES = {
  sm: 'h-7 gap-1 px-2.5 text-xs',
  md: 'h-9 gap-1.5 px-3.5 text-sm',
  lg: 'h-10 gap-2 px-4 text-sm',
} as const

export default function Button({
  variant = 'default',
  size = 'md',
  loading = false,
  className,
  children,
  disabled,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: keyof typeof VARIANTS
  size?: keyof typeof SIZES
  /** 正在做 —— 置灰并转个小圈 */
  loading?: boolean
}) {
  return (
    <button
      {...props}
      disabled={disabled || loading}
      className={[
        'inline-flex items-center justify-center rounded-lg font-medium whitespace-nowrap transition-colors',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]',
        'disabled:cursor-not-allowed disabled:opacity-50',
        VARIANTS[variant],
        SIZES[size],
        className ?? '',
      ].join(' ')}
    >
      {loading ? (
        <span
          aria-hidden="true"
          className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent opacity-70"
        />
      ) : null}
      {children}
    </button>
  )
}
