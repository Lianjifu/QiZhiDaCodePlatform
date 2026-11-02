/**
 * 编码助手 —— 唯一支持多轮对话的页面。
 *
 * 空状态（还没发过消息）：输入框居中，像 Claude 桌面端那样。
 * 有消息之后：消息列表滚动，输入框固定在底部。
 *
 * **会话 ID 是多轮的钥匙** —— 后端靠它串 checkpoint，前端只要一直带着同一个
 * ``thread_id``，历史就接得上。
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router'

import { clearConversation } from '../../api/resources'
import Composer, { type ComposerHandle } from './Composer'
import MessageList from './MessageList'
import { useChat } from './useChat'
import { newThreadId, titleFromMessage, useSessions } from '../sessions/store'

/** 空状态页的推荐问题 —— 点一下填进输入框。 */
const SUGGESTIONS = [
  '列出工作区里的文件',
  '讲讲这个项目是干嘛的',
  '当前能调用哪些能力',
  '先给一个改造方案',
]

export default function ChatPage() {
  const { threadId: routeThreadId } = useParams<{ threadId: string }>()
  const navigate = useNavigate()
  const { touch, rename, sessions } = useSessions()
  const chat = useChat()

  // 没有 URL 里的会话就临时开一个 —— 发出第一条消息时才落到路由上
  const [draftThreadId, setDraftThreadId] = useState(() => newThreadId())
  const threadId = routeThreadId ?? draftThreadId

  const loadedRef = useRef<string>('')
  /** 本次挂载期间已经流式聊过的会话 —— 别用历史覆盖它们的现场状态 */
  const streamedRef = useRef<Set<string>>(new Set())
  /** 上一次的路由会话 —— 用来发现「从某个会话退回 /chat」 */
  const prevRouteRef = useRef<string | undefined>(routeThreadId)

  // 换会话：把那个会话的历史拉回来
  useEffect(() => {
    const prev = prevRouteRef.current
    prevRouteRef.current = routeThreadId
    if (!routeThreadId) {
      chat.reset()
      loadedRef.current = ''
      // 从某个会话退回 /chat（侧栏「新建」）—— 换一个新会话 id，
      // 别把新消息接到刚才那个会话的 checkpoint 上。
      if (prev) setDraftThreadId(newThreadId())
      return
    }
    if (loadedRef.current === routeThreadId) return
    // 这个会话的消息正在屏幕上流着呢 —— 拉历史会把它冲掉
    // （待批卡片、进行中的工具块都在流里，历史里没有）。
    if (streamedRef.current.has(routeThreadId)) return
    loadedRef.current = routeThreadId
    void chat.loadHistory(routeThreadId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeThreadId])

  const empty = chat.messages.length === 0

  /** 开一个新会话。 */
  const startNewSession = () => {
    setDraftThreadId(newThreadId())
    chat.reset()
    loadedRef.current = ''
    if (routeThreadId) navigate('/chat')
  }

  /** 斜杠命令里的本地动作。 */
  const runCommandAction = async (action: string) => {
    if (action === 'new') {
      startNewSession()
      return
    }
    if (action === 'clear') {
      try {
        await clearConversation(threadId)
      } catch (cause) {
        window.alert(`清空失败：${cause instanceof Error ? cause.message : String(cause)}`)
        return
      }
      chat.reset()
      return
    }
    if (action === 'rename') {
      const title = window.prompt('给这个会话起个名字', currentTitle())
      if (title && title.trim()) {
        // 会话可能还没进列表（刚 /new 出来的）—— 先记一笔再改名
        touch(threadId)
        rename(threadId, title.trim())
      }
    }
  }

  const currentTitle = () => {
    // 侧栏里的标题最准 —— 没有就退回第一条用户消息
    const known = sessions.find((item) => item.threadId === threadId)
    if (known) return known.title
    const first = chat.messages.find((item) => item.role === 'user')
    return first ? titleFromMessage(first.content) : '新会话'
  }

  const send = async (text: string, attachments: string[], providerId: number | null) => {
    const isNew = !routeThreadId
    streamedRef.current.add(threadId)
    await chat.send(text, threadId, { attachments, providerId })
    touch(threadId, titleFromMessage(text))
    if (isNew) {
      // 第一条消息发完，把会话固定到路由上（刷新还能回到这儿）
      navigate(`/chat/${threadId}`, { replace: true })
    }
  }

  const scrollRef = useRef<HTMLDivElement>(null)
  const composerRef = useRef<ComposerHandle>(null)

  // 新内容进来就滚到底。
  //
  // **用 layout effect 而不是 effect**：流式时消息更新比帧还快，
  // requestAnimationFrame 每次都被下一次更新取消掉，永远轮不上（页面不动）。
  // layout effect 在 DOM 变更后同步执行，这时 scrollHeight 已经是新高度。
  useLayoutEffect(() => {
    const element = scrollRef.current
    if (!element) return
    element.scrollTop = element.scrollHeight
    // 再补一帧：插入新节点后 flex 布局可能才最终定高，那时再滚一次才到底。
    // （同步那次已经把绝大多数情况覆盖了，这帧只是兜底。）
    const frame = requestAnimationFrame(() => {
      element.scrollTop = element.scrollHeight
    })
    return () => cancelAnimationFrame(frame)
  }, [chat.messages, chat.busy])

  const statusHint = chat.error

  return (
    <div className="flex h-full flex-col">
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        {empty ? (
          <div className="relative flex h-full flex-col items-center justify-center gap-8 px-6">
            {/* 背景：中心光晕 + 点阵（只在这一个空状态页上有） */}
            <div aria-hidden className="hero-glow pointer-events-none absolute inset-0" />
            <div aria-hidden className="hero-grid pointer-events-none absolute inset-0" />

            <div className="relative text-center">
              <h1 className="text-2xl font-semibold tracking-tight text-ink">
                在你的代码工作区里干活
              </h1>
              <p className="mt-2 text-sm text-ink-muted">
                读代码、改代码、跑命令、给结果 —— 或者直接问。
              </p>
            </div>

            <Composer
              ref={composerRef}
              threadId={threadId}
              centered
              busy={chat.busy}
              onSend={(text, attachments, providerId) => void send(text, attachments, providerId)}
              onStop={chat.abort}
              onCommandAction={(action) => void runCommandAction(action)}
            />

            <div className="relative flex max-w-3xl flex-wrap justify-center gap-2">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => composerRef.current?.setText(suggestion)}
                  className="rounded-xl border border-line bg-sunken/70 px-3.5 py-2 text-left text-xs text-ink-muted transition-colors hover:bg-hover hover:text-ink"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <MessageList
            messages={chat.messages}
            busy={chat.busy}
            onApprove={() => void chat.approve(threadId)}
            onReject={() => void chat.reject(threadId)}
          />
        )}
      </div>

      {!empty && (
        <div className="shrink-0">
          {statusHint ? (
            <div className="mx-auto max-w-3xl px-6 pb-1 text-xs text-ink-faint">{statusHint}</div>
          ) : null}
          <Composer
            threadId={threadId}
            busy={chat.busy}
            onSend={(text, attachments, providerId) => void send(text, attachments, providerId)}
            onStop={chat.abort}
            onCommandAction={(action) => void runCommandAction(action)}
          />
        </div>
      )}
    </div>
  )
}
