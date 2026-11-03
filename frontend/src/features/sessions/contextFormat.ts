/**
 * 上下文占用的显示格式。
 *
 * 单独一个文件 —— 放组件文件里会让"只导出组件"那条规则报警（热更新会整页刷）。
 */

/** 每块上下文占一个颜色 —— 跟参考图一样，一眼能分清谁占了多少。 */
export const PART_COLORS: Record<string, string> = {
  messages: 'bg-accent',
  system_prompt: 'bg-warning',
  tools: 'bg-danger',
  mcp_tools: 'bg-success',
  skills: 'bg-ink-faint',
  memory: 'bg-ink-faint',
}

/** 压缩缓冲（预留）不是一个"占用块"，颜色刻意淡一点。 */
export const RESERVED_COLOR = 'bg-ink-faint/40'

/** token 数显示成 k —— 面板上写「128k」比「128000」好读。 */
export function formatTokens(value: number): string {
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`
  return String(value)
}

/** 占整个窗口的百分之几 —— 窗口是 0 的时候别除出 NaN。 */
export function percentOf(tokens: number, limit: number): string {
  if (!limit) return '0'
  return ((tokens / limit) * 100).toFixed(1)
}
