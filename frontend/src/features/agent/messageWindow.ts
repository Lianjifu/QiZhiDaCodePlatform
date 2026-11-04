/**
 * 长会话只渲染最近一段 —— 窗口怎么切。
 *
 * **纯函数**，好测（见 `scripts/test-message-window.mjs`）。
 *
 * 为什么要有窗口：几百条消息全渲染，DOM 节点跟着涨，Markdown 解析也跟着拖慢。
 * 这里只渲染**最近的一段**，更早的按需往上加载 —— 消息没丢，只是不在 DOM 里。
 */

/** 默认渲染最近多少条。 */
export const DEFAULT_VISIBLE = 40

/** 一次往上加载多少条。 */
export const PAGE_SIZE = 40

/** 窗口切完的结果。 */
export interface MessageWindow<T> {
  /** 这一轮要渲染的消息 */
  shown: T[]
  /** 还有多少条更早的没渲染 */
  hidden: number
}

/**
 * 取最近 ``visibleCount`` 条。
 *
 * @param items 全部消息（按时间顺序）
 * @param visibleCount 渲染最近多少条
 */
export function messageWindow<T>(items: T[], visibleCount: number): MessageWindow<T> {
  const total = items.length
  const count = Math.max(0, Math.min(visibleCount, total))
  const hidden = total - count
  return {
    shown: hidden > 0 ? items.slice(hidden) : items,
    hidden,
  }
}
