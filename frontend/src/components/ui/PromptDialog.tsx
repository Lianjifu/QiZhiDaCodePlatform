/**
 * 输入一个值的弹窗。
 *
 * **为什么不用 `window.prompt`**：浏览器原生框的样式跟平台没关系，按钮还是
 * 系统语言，而且它会**阻塞整个页面** —— 弹窗期间什么都点不了。
 *
 * 输入框的状态放在组件里 —— 每次打开都是新的一次，旧的内容不该留着。
 */
import { useState } from 'react'

import Button from './Button'
import Modal from './Modal'

export interface PromptRequest {
  title: string
  /** 说明文字 */
  description?: string
  /** 输入框里的默认值 */
  defaultValue?: string
  placeholder?: string
  /** 确认按钮上的字 */
  confirmText?: string
}

export default function PromptDialog({
  request,
  onSubmit,
  onCancel,
}: {
  request: PromptRequest
  /** 提交（值已经去过空白） */
  onSubmit: (value: string) => void
  onCancel: () => void
}) {
  const [value, setValue] = useState(request.defaultValue ?? '')

  const submit = () => {
    const trimmed = value.trim()
    // 空名字没什么意义 —— 当作没填
    if (!trimmed) return
    onSubmit(trimmed)
  }

  return (
    <Modal
      title={request.title}
      onClose={onCancel}
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onCancel}>
            取消
          </Button>
          <Button variant="primary" size="sm" onClick={submit}>
            {request.confirmText ?? '确定'}
          </Button>
        </>
      }
    >
      {request.description ? (
        <p className="mb-3 text-sm leading-relaxed text-ink-muted">{request.description}</p>
      ) : null}
      <input
        autoFocus
        value={value}
        placeholder={request.placeholder}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault()
            submit()
          }
        }}
        className="w-full rounded-lg border border-line bg-base px-2.5 py-1.5 text-sm text-ink outline-none transition-colors placeholder:text-ink-faint focus:border-accent focus:shadow-[0_0_0_3px_var(--ring)]"
      />
    </Modal>
  )
}
