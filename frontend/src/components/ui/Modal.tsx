/**
 * 弹窗。
 *
 * 平台上所有"新增 / 编辑 / 详情"都用它 —— 一套框，行为一致：
 * 点遮罩关闭、Esc 关闭、内容区自己滚、打开时背景不跟着滚。
 *
 * **窄屏是底部抽屉** —— 居中的对话框在手机上会顶天立地，两边贴边，
 * 输入框被键盘顶起来就够不着了。抽屉从底部升起、贴底，往下划就能关。
 */
import { useEffect, type ReactNode } from 'react'

const SIZES = {
  sm: 'sm:max-w-md',
  md: 'sm:max-w-xl',
  lg: 'sm:max-w-3xl',
} as const

export default function Modal({
  title,
  onClose,
  children,
  footer,
  size = 'md',
  description,
}: {
  title: string
  onClose: () => void
  children: ReactNode
  /** 底部按钮区（取消 / 保存） */
  footer?: ReactNode
  /** 宽窄 */
  size?: keyof typeof SIZES
  /** 标题下面那行小字 */
  description?: string
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    // **背景别跟着滚** —— 弹窗内容长的时候，背景滚走了回来就找不着北
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = previous
    }
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 backdrop-blur-[2px] sm:items-start sm:overflow-y-auto sm:p-8"
      onMouseDown={(event) => {
        // 点自己（不是里面的内容）才算关
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={[
          'flex w-full flex-col overflow-hidden border border-line bg-overlay shadow-3',
          // 窄屏：贴底的抽屉；宽屏：居中的对话框
          'max-h-[92dvh] rounded-t-2xl',
          'sm:max-h-[calc(100dvh-4rem)] sm:rounded-2xl',
          SIZES[size],
        ].join(' ')}
      >
        {/* 窄屏抽屉的抓手 —— 提示这里能往下划 */}
        <div className="flex justify-center pt-2 sm:hidden">
          <span className="h-1 w-9 rounded-full bg-line-strong" />
        </div>

        <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-ink">{title}</h2>
            {description ? (
              <p className="mt-0.5 text-xs text-ink-muted">{description}</p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            title="关闭"
            className="-mr-1 shrink-0 rounded-lg px-1.5 py-1 text-sm text-ink-faint transition-colors hover:bg-hover hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--ring)]"
          >
            ×
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">{children}</div>

        {footer ? (
          <div className="flex items-center justify-end gap-2 border-t border-line bg-base px-4 py-3">
            {footer}
          </div>
        ) : null}
      </div>
    </div>
  )
}
