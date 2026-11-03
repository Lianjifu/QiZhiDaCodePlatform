/**
 * 从 React 节点里抽出纯文本。
 *
 * 代码块复制用 —— 高亮后的代码是一棵带 `hljs-*` 类的节点树，渲染时原样用，
 * 但复制要的是纯文本，所以另抽一份。
 */
import { isValidElement, type ReactNode } from 'react'

export function extractText(node: ReactNode): string {
  if (node === null || node === undefined || node === false) return ''
  if (typeof node === 'string') return node
  if (typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(extractText).join('')
  if (isValidElement(node)) {
    const props = node.props as { children?: ReactNode }
    return extractText(props.children)
  }
  return ''
}
