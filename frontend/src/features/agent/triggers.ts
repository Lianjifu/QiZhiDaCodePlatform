/**
 * 输入框的触发符规则 —— 纯函数，不碰 React。
 *
 * 拆出来是为了能单独测：这套规则（什么时候弹斜杠命令面板、什么时候弹 @ 面板）
 * 全是边界条件，靠手点很难验。
 */

/** 正在生效的触发符（斜杠命令面板 / @ 提及面板）。 */
export interface Trigger {
  kind: 'slash' | 'mention'
  query: string
  /** 触发符在正文里的位置 */
  start: number
  end: number
}

/**
 * 从光标往前找触发符。
 *
 * 规则：看光标所在的**那个词**（到上一个空白为止）。
 *
 * - 词以 ``@`` 开头 → @ 提及面板（``@`` 后面可以直接跟路径）；
 * - 词以 ``/`` 开头且**没有第二个斜杠** → 斜杠命令面板；
 * - 其它 → 不触发。
 *
 * 按词来判断而不是按字符往后扫 —— 否则 ``@/uploads/a.png`` 里路径中间的斜杠
 * 会先把扫描打断，@ 面板就弹不出来了。
 */
export function detectTrigger(value: string, caret: number): Trigger | null {
  let index = caret - 1
  while (index >= 0 && !/\s/.test(value[index])) index -= 1
  const start = index + 1
  const token = value.slice(start, caret)
  if (!token) return null

  if (token.startsWith('@')) {
    return { kind: 'mention', query: token.slice(1), start, end: caret }
  }
  if (token.startsWith('/')) {
    // 词里还有斜杠 → 这是路径，不是命令
    if (token.slice(1).includes('/')) return null
    return { kind: 'slash', query: token.slice(1), start, end: caret }
  }
  return null
}

/** 正文里被 @ 提到的路径。 */
export function mentionedPaths(text: string): string[] {
  const matches = text.match(/@(\/[^\s@]+)/g) ?? []
  return [...new Set(matches.map((item) => item.slice(1)))]
}

/**
 * @ 提及面板的排序：文件在前，目录在后。
 *
 * **顺序只能有一处定义** —— 面板显示的顺序和回车选中的顺序必须用同一份，
 * 否则会出现"高亮的是文件、按回车选中的却是目录"。
 *
 * 文件排前面是因为提及文件远比提及目录常见，默认高亮的第一项应该是用户想要的。
 */
export function orderEntries<T extends { is_dir: boolean }>(entries: T[]): T[] {
  return [
    ...entries.filter((item) => !item.is_dir),
    ...entries.filter((item) => item.is_dir),
  ]
}
