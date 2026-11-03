/**
 * 弹窗。
 *
 * 平台上所有"新增 / 编辑 / 详情"都用它 —— 一套框，行为一致：
 * 点遮罩关闭、Esc 关闭、内容区自己滚。
 */
import { useEffect, type ReactNode } from 'react'

export default function Modal({
  title,
  onClose,
  children,
  footer,
  wide,
}: {
  title: string
  onClose: () => void
  children: ReactNode
  /** 底部按钮区（取消 / 保存） */
  footer?: ReactNode
  /** 宽一点（详情、JSON 编辑） */
  wide?: boolean
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/30 p-4 backdrop-blur-[1px] sm:p-8"
      onMouseDown={(event) => {
        // 点自己（不是里面的内容）才算关
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        className={[
          'w-full rounded-2xl border border-line bg-overlay shadow-2xl',
          wide ? 'max-w-3xl' : 'max-w-xl',
        ].join(' ')}
      >
        <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <span className="text-sm font-medium text-ink">{title}</span>
          <button
            type="button"
            onClick={onClose}
            title="关闭"
            className="rounded px-1.5 text-sm text-ink-faint transition-colors hover:bg-hover hover:text-ink"
          >
            ×
          </button>
        </div>

        <div className="max-h-[70vh] overflow-y-auto px-4 py-4">{children}</div>

        {footer ? (
          <div className="flex items-center justify-end gap-2 border-t border-line px-4 py-3">
            {footer}
          </div>
        ) : null}
      </div>
    </div>
  )
}
