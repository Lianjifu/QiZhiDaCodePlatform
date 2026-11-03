/**
 * 代码块。
 *
 * 上面一条窄条：左边语言名，右边「复制」。下面是高亮后的代码。
 *
 * **高亮后的内容是原样传进来的**（`children` 是一棵带 `hljs-*` 类的节点树），
 * 不能自己抽成纯文本 —— 抽了就丢了颜色。所以复制用的纯文本另算一份，
 * 但**渲染用的是原树**。
 */
import { useMemo, useState, type ReactNode } from 'react'

import { extractText } from './extractText'

export default function CodeBlock({
  children,
  language,
}: {
  children: ReactNode
  language: string
}) {
  const [copied, setCopied] = useState(false)
  const text = useMemo(() => extractText(children), [children])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      // 剪贴板不可用（非 https / 没授权）—— 不弹错，复制按钮保持原样
    }
  }

  return (
    <div className="md-code">
      <div className="md-code-bar">
        <span className="md-code-lang">{language || '文本'}</span>
        <button type="button" onClick={() => void copy()} className="md-code-copy">
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <pre className="md-code-body">{children}</pre>
    </div>
  )
}
