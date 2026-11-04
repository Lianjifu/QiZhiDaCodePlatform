/**
 * 用共享仓库读会话状态。
 *
 * 底栏的占用指示器和右侧的完整面板都走这里 —— 同一份数据，一个请求。
 */
import { useCallback, useEffect, useRef, useSyncExternalStore } from 'react'

import { sessionStatus } from '../../api/resources'
import type { SessionStatus } from '../../types/api'
import { createStatusStore } from './statusStore'

/** 全局一个就够 —— 仓库是共享的。 */
const store = createStatusStore(sessionStatus)

export function useSessionStatus(threadId: string) {
  const entry = useSyncExternalStore(
    useCallback((onChange: () => void) => store.subscribe(threadId, onChange), [threadId]),
    useCallback(() => store.get(threadId), [threadId]),
  )

  const refresh = useCallback(() => store.refresh(threadId), [threadId])

  // 换个会话就读一次 —— 不然新会话的数字是空的
  useEffect(() => {
    if (!threadId) return
    void refresh()
  }, [threadId, refresh])

  return {
    status: entry.status,
    error: entry.error,
    refresh,
  }
}

/**
 * 这一轮跑完了重新读一次。
 *
 * 光靠三十秒的轮询太慢 —— 一轮对话刚结束，占用数字还停在上一轮，看着像没变。
 */
export function useRefreshOnIdle(busy: boolean, refresh: () => void) {
  const prevBusy = useRef(busy)
  useEffect(() => {
    if (prevBusy.current && !busy) refresh()
    prevBusy.current = busy
  }, [busy, refresh])
}

export type { SessionStatus }
