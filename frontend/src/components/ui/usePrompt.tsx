/**
 * 输入一个值的弹窗 hook。
 *
 * **为什么不用 `window.prompt`**：浏览器原生框的样式跟平台没关系，按钮还是
 * 系统语言，而且它会**阻塞整个页面** —— 弹窗期间什么都点不了。
 *
 * `useConfirm` 只能问「要不要」，问不出「改成什么」—— 改名这类动作要一个
 * 输入框，所以另起一个 hook，弹窗用平台自己的 {@link PromptDialog}。
 *
 * 用法：页面里挂一次 ``dialog``，要名字时 ``await prompt(...)``：
 *
 * ```tsx
 * const { prompt, dialog } = usePrompt()
 * const name = await prompt({ title: '给这个会话起个名字', defaultValue: current })
 * if (!name) return
 * …
 * return <>{…}{dialog}</>
 * ```
 */
import { useCallback, useState, type ReactNode } from 'react'

import PromptDialog, { type PromptRequest } from './PromptDialog'

type Pending = PromptRequest & { resolve: (value: string | null) => void }

export function usePrompt(): {
  /** 弹一个输入框，等人填（null = 取消） */
  prompt: (request: PromptRequest) => Promise<string | null>
  /** 要渲染在页面里的那个弹窗 */
  dialog: ReactNode
} {
  const [pending, setPending] = useState<Pending | null>(null)

  const prompt = useCallback(
    (request: PromptRequest) =>
      new Promise<string | null>((resolve) => {
        setPending({ ...request, resolve })
      }),
    [],
  )

  const dialog = pending ? (
    <PromptDialog
      request={pending}
      onSubmit={(value) => {
        pending.resolve(value)
        setPending(null)
      }}
      onCancel={() => {
        pending.resolve(null)
        setPending(null)
      }}
    />
  ) : null

  return { prompt, dialog }
}
