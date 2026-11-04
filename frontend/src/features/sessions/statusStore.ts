/**
 * 会话状态的共享缓存。
 *
 * **同一份状态有两个地方要**：底栏的「上下文占用」指示器，和右侧那栏
 * 完整的状态面板。它们都打同一个接口（``/agent/{id}/status``），各拉一份
 * 就是同一件事干两遍 —— 面板开着的时候，每三十秒两个请求，面板里的数字
 * 和底栏的还可能对不上。
 *
 * 这里做成一个共享的小仓库：
 *
 * - **同一时刻只有一个请求在飞** —— 两边同时要，第二个等第一个的结果；
 * - **30 秒一轮的定时器只有一个** —— 订阅的人多了也不多开；
 * - **面板打开就是现成的数据** —— 底栏早就拉过了，不用再等一次。
 *
 * 跟别的工具函数分开：这个仓库带状态、有定时器，所以单测用
 * ``createStatusStore`` 造一个假的来测（见 ``scripts/test-status-store.mjs``）。
 */

import type { SessionStatus } from '../../types/api'

/** 一条状态：数据 + 上次读失败的提示。 */
export type StatusEntry = {
  status: SessionStatus | null
  error: string
}

/** 拉状态的那个函数 —— 传进来才能单测。 */
export type StatusFetcher = (threadId: string) => Promise<SessionStatus>

export interface StatusStore {
  /** 读当前值（引用稳定 —— 可以直接给 useSyncExternalStore 用）。 */
  get(threadId: string): StatusEntry
  /** 订阅变化，返回退订函数。 */
  subscribe(threadId: string, onChange: () => void): () => void
  /** 拉一次。已经在飞就等那一次，不重复发。 */
  refresh(threadId: string): Promise<void>
  /** 停掉定时器（会话组件卸载时用不着，但测试要）。 */
  dispose(): void
}

/** 多久刷一次。 */
export const POLL_MS = 30_000

/** 空的条目 —— 所有没数据的会话共用这一个，引用得稳定。 */
const EMPTY: StatusEntry = { status: null, error: '' }

/**
 * 造一个状态仓库。
 *
 * @param fetchStatus 拉状态的函数。
 * @param pollMs 轮询间隔（毫秒）。
 */
export function createStatusStore(fetchStatus: StatusFetcher, pollMs = POLL_MS): StatusStore {
  const entries = new Map<string, StatusEntry>()
  const listeners = new Map<string, Set<() => void>>()
  const inflight = new Map<string, Promise<void>>()
  let timer: ReturnType<typeof setInterval> | null = null

  function emit(threadId: string) {
    const set = listeners.get(threadId)
    if (!set) return
    for (const fn of set) fn()
  }

  function refresh(threadId: string): Promise<void> {
    if (!threadId) return Promise.resolve()
    const pending = inflight.get(threadId)
    if (pending) return pending
    const request = fetchStatus(threadId)
      .then((status) => {
        entries.set(threadId, { status, error: '' })
        emit(threadId)
      })
      .catch((cause: unknown) => {
        // **读失败就保留上一次的数据** —— 面板上的数字不该因为一次网络
        // 抖动就变成空白。只把错误记下来，让完整面板能说出来。
        const previous = entries.get(threadId)
        entries.set(threadId, {
          status: previous?.status ?? null,
          error: cause instanceof Error ? cause.message : String(cause),
        })
        emit(threadId)
      })
      .finally(() => {
        inflight.delete(threadId)
      })
    inflight.set(threadId, request)
    return request
  }

  function ensureTimer() {
    if (timer !== null) return
    timer = setInterval(() => {
      // 标签页不可见时不读 —— 后台放着也没人看
      if (typeof document !== 'undefined' && document.hidden) return
      for (const threadId of listeners.keys()) void refresh(threadId)
    }, pollMs)
  }

  function stopTimer() {
    if (timer === null) return
    clearInterval(timer)
    timer = null
  }

  return {
    get(threadId) {
      return entries.get(threadId) ?? EMPTY
    },
    subscribe(threadId, onChange) {
      if (!threadId) return () => {}
      let set = listeners.get(threadId)
      if (!set) {
        set = new Set()
        listeners.set(threadId, set)
      }
      set.add(onChange)
      ensureTimer()
      return () => {
        set.delete(onChange)
        if (set.size === 0) listeners.delete(threadId)
        // 没人看了就停掉定时器 —— 别在后台空转
        if (listeners.size === 0) stopTimer()
      }
    },
    refresh,
    dispose() {
      stopTimer()
      listeners.clear()
      inflight.clear()
      entries.clear()
    },
  }
}
