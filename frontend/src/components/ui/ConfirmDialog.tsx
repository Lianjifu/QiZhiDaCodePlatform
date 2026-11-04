/**
 * 确认弹窗。
 *
 * **为什么不用 `window.confirm`**：那是浏览器原生的框 —— 样式跟平台没关系，
 * 按钮还是系统语言，中文界面上很突兀；而且它会**阻塞整个页面**，弹窗期间
 * 什么都点不了。这里用自己的弹窗：Esc / 点遮罩取消，焦点在框里。
 *
 * 危险操作（删除、放行破坏性操作）用 ``danger`` 那一档按钮。
 */
import { AlertIcon } from '../icons'
import Button from './Button'
import Modal from './Modal'

export default function ConfirmDialog({
  title,
  description,
  confirmText = '确定',
  cancelText = '取消',
  danger = false,
  onConfirm,
  onCancel,
}: {
  title: string
  /** 说明文字（可以是一段话） */
  description: string
  confirmText?: string
  cancelText?: string
  /** 危险操作：按钮用危险色，图标也换 */
  danger?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <Modal
      title={title}
      onClose={onCancel}
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onCancel}>
            {cancelText}
          </Button>
          <Button
            variant={danger ? 'danger' : 'primary'}
            size="sm"
            onClick={onConfirm}
            autoFocus
          >
            {confirmText}
          </Button>
        </>
      }
    >
      <div className="flex gap-3">
        <span
          className={[
            'flex h-8 w-8 shrink-0 items-center justify-center rounded-full',
            danger ? 'bg-danger-soft text-danger' : 'bg-sunken text-ink-muted',
          ].join(' ')}
        >
          <AlertIcon className="h-4 w-4" />
        </span>
        <p className="pt-1 text-sm leading-relaxed text-ink-muted">{description}</p>
      </div>
    </Modal>
  )
}
