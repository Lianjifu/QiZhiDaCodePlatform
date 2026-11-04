/**
 * 确认弹窗的 hook。
 *
 * **为什么不用 `window.confirm`**：浏览器原生的框样式跟平台没关系，按钮还是
 * 系统语言，而且它会**阻塞整个页面** —— 弹窗期间什么都点不了。
 *
 * 用法：页面里挂一次 ``dialog``，删除前 ``await confirm(...)``：
 *
 * ```tsx
 * const { confirm, dialog } = useConfirm()
 * if (!(await confirm({ title: '删掉它？', description: '…', danger: true }))) return
 * …
 * return <>{…}{dialog}</>
 * ```
 */
import { useCallback, useState, type ReactNode } from 'react'

import ConfirmDialog from './ConfirmDialog'

export interface ConfirmRequest {
  title: string
  /** 说明文字 */
  description: string
  /** 确认按钮上的字 */
  confirmText?: string
  /** 危险操作：按钮与图标用危险色 */
  danger?: boolean
}

type Pending = ConfirmRequest & { resolve: (ok: boolean) => void }

export function useConfirm(): {
  /** 弹一个确认框，等人回答（true = 确认） */
  confirm: (request: ConfirmRequest) => Promise<boolean>
  /** 要渲染在页面里的那个弹窗 */
  dialog: ReactNode
} {
  const [pending, setPending] = useState<Pending | null>(null)

  const confirm = useCallback(
    (request: ConfirmRequest) =>
      new Promise<boolean>((resolve) => {
        setPending({ ...request, resolve })
      }),
    [],
  )

  const dialog = pending ? (
    <ConfirmDialog
      title={pending.title}
      description={pending.description}
      confirmText={pending.confirmText}
      danger={pending.danger}
      onConfirm={() => {
        pending.resolve(true)
        setPending(null)
      }}
      onCancel={() => {
        pending.resolve(false)
        setPending(null)
      }}
    />
  ) : null

  return { confirm, dialog }
}
