/**
 * 从用户输入里取会话标题。
 *
 * 单独一个文件 —— 纯函数，能在 node 里直接测（store.tsx 是 .tsx，测不了）。
 */

/** 默认标题。消息标题只覆盖它，不覆盖用户自己起的名字。 */
export const DEFAULT_TITLE = '新会话'

/** 从用户输入里取标题。 */
export function titleFromMessage(message: string): string {
  const firstLine = message.trim().split('\n')[0] ?? ''
  // **把 Markdown 记号去掉** —— 正文渲染成 Markdown 了，标题却是裸的
  // `# 标题` 在侧栏里显示成 `# 标题`，像坏了一样。
  const plain = firstLine
    .replace(/^\s{0,3}#{1,6}\s*/, '')
    .replace(/^\s{0,3}>\s*/, '')
    .replace(/^\s*[-+]\s*/, '')
    .replace(/[*_`~]/g, '')
    .trim()
  return plain.slice(0, 30) || DEFAULT_TITLE
}
