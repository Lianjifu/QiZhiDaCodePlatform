/**
 * 按钮。
 *
 * 四档：默认（描边）、主操作（实心强调）、危险（删除）、文字（次要动作）。
 */
import type { ButtonHTMLAttributes } from 'react'

const VARIANTS = {
  default: 'border border-line bg-base text-ink-muted hover:bg-hover hover:text-ink',
  primary: 'bg-accent text-accent-text hover:bg-accent-hover',
  danger: 'border border-danger/40 bg-danger-soft text-danger hover:bg-danger/15',
  ghost: 'text-ink-faint hover:bg-hover hover:text-ink',
}

const SIZES = {
  sm: 'px-2.5 py-1 text-xs',
  md: 'px-3.5 py-1.5 text-sm',
}

export default function Button({
  variant = 'default',
  size = 'md',
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: keyof typeof VARIANTS
  size?: keyof typeof SIZES
}) {
  return (
    <button
      {...props}
      className={[
        'rounded-lg font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50',
        VARIANTS[variant],
        SIZES[size],
        className ?? '',
      ].join(' ')}
    />
  )
}
