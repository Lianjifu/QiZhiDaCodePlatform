/**
 * 会话元数据（本地）。
 *
 * **后端没有会话表** —— 消息在 checkpoint 里，会话本身（标题、置顶、列表）
 * 只存在浏览器。这是已知缺口（见 backend/docs/plans/file-storage.md 的
 * 「会话管理模块」），第一版先用 localStorage 顶着。
 *
 * 清了浏览器数据会话列表就没了，但消息还在后端 —— 两者不完全一致，接受。
 */
/*
 * provider 与 hook 放同一个文件是 React 的常见写法（hook 要用同一个
 * Context 对象）。这条规则只影响热更新时的局部刷新，关掉。
 */
/* oxlint-disable react/only-export-components */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

const STORAGE_KEY = 'qzd.sessions'

/** 默认标题。消息标题只覆盖它，不覆盖用户自己起的名字。 */
const DEFAULT_TITLE = '新会话'

export interface SessionMeta {
  threadId: string
  /** 标题（取第一条用户消息的前若干字） */
  title: string
  pinned: boolean
  updatedAt: number
}

function readAll(): SessionMeta[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as SessionMeta[]) : []
  } catch {
    return []
  }
}

function writeAll(sessions: SessionMeta[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions))
  } catch {
    // 存不了就算了，本次会话内仍然可用
  }
}

/** 新建一个会话 ID。 */
export function newThreadId(): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

/** 从用户输入里取标题。 */
export function titleFromMessage(message: string): string {
  const firstLine = message.trim().split('\n')[0] ?? ''
  return firstLine.slice(0, 30) || DEFAULT_TITLE
}

interface SessionsValue {
  sessions: SessionMeta[]
  pinned: SessionMeta[]
  others: SessionMeta[]
  /** 新建一个会话，立刻进列表（不是等用户发了第一条消息才进） */
  create: (title?: string) => string
  /** 记一笔（会话不存在就建，存在就更新标题与时间） */
  touch: (threadId: string, title?: string) => void
  /** 改标题 */
  rename: (threadId: string, title: string) => void
  togglePin: (threadId: string) => void
  remove: (threadId: string) => void
}

const SessionsContext = createContext<SessionsValue | null>(null)

export function SessionsProvider({ children }: { children: ReactNode }) {
  const [sessions, setSessions] = useState<SessionMeta[]>(readAll)

  useEffect(() => {
    writeAll(sessions)
  }, [sessions])

  const create = useCallback((title = DEFAULT_TITLE): string => {
    const threadId = newThreadId()
    setSessions((prev) => [
      { threadId, title, pinned: false, updatedAt: Date.now() },
      ...prev,
    ])
    return threadId
  }, [])

  const touch = useCallback((threadId: string, title?: string) => {
    setSessions((prev) => {
      const existing = prev.find((item) => item.threadId === threadId)
      const next: SessionMeta = {
        threadId,
        // **用户改过的标题不覆盖** —— 只有默认的「新会话」才让消息标题顶掉它。
        // 否则每发一条消息，用户起的名字就被冲回第一条消息的内容。
        title:
          existing && existing.title !== DEFAULT_TITLE
            ? existing.title
            : (title ?? existing?.title ?? DEFAULT_TITLE),
        pinned: existing?.pinned ?? false,
        updatedAt: Date.now(),
      }
      return [next, ...prev.filter((item) => item.threadId !== threadId)]
    })
  }, [])

  const rename = useCallback((threadId: string, title: string) => {
    setSessions((prev) =>
      prev.map((item) =>
        item.threadId === threadId ? { ...item, title, updatedAt: Date.now() } : item,
      ),
    )
  }, [])

  const togglePin = useCallback((threadId: string) => {
    setSessions((prev) =>
      prev.map((item) =>
        item.threadId === threadId ? { ...item, pinned: !item.pinned } : item,
      ),
    )
  }, [])

  const remove = useCallback((threadId: string) => {
    setSessions((prev) => prev.filter((item) => item.threadId !== threadId))
  }, [])

  const value = useMemo<SessionsValue>(
    () => ({
      sessions,
      pinned: sessions.filter((item) => item.pinned),
      others: sessions.filter((item) => !item.pinned),
      create,
      touch,
      rename,
      togglePin,
      remove,
    }),
    [sessions, create, touch, rename, togglePin, remove],
  )

  return <SessionsContext.Provider value={value}>{children}</SessionsContext.Provider>
}

export function useSessions(): SessionsValue {
  const context = useContext(SessionsContext)
  if (!context) {
    throw new Error('useSessions 必须在 SessionsProvider 里用')
  }
  return context
}
