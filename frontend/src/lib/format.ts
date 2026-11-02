/** 展示用的小工具。 */

/** 字节数 → 人看的体积。 */
export function humanSize(size: number): string {
  const units = ['B', 'KB', 'MB', 'GB']
  let value = size
  let index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index += 1
  }
  return `${index === 0 ? value : value.toFixed(1)} ${units[index]}`
}

/** 任意值 → 缩进过的 JSON（用于展示接口原始返回）。 */
export function formatJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

/** 时间戳 → 本地时间字符串。 */
export function formatTime(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}
