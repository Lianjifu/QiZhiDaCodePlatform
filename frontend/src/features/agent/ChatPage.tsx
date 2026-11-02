/**
 * 编码助手 —— 唯一支持多轮对话的页面。
 *
 * 空状态（还没发过消息）：输入框居中，像 Claude 桌面端那样。
 * 有消息之后：消息列表滚动，输入框固定在底部。
 *
 * **会话 ID 是多轮的钥匙** —— 后端靠它串 checkpoint，前端只要一直带着同一个
 * ``thread_id``，历史就接得上。
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router'

import Composer from './Composer'
import MessageList from './MessageList'
import { useChat } from './useChat'
import { newThreadId, titleFromMessage, useSessions } from '../sessions/store'

export default function ChatPage() {
  const { threadId: routeThreadId } = useParams<{ threadId: string }>()
  const navigate = useNavigate()
  const { touch } = useSessions()
  const chat = useChat()

  // 没有 URL 里的会话就临时开一个 —— 发出第一条消息时才落到路由上
  const [draftThreadId] = useState(() => newThreadId())
  const threadId = routeThreadId ?? draftThreadId

  const loadedRef = useRef<string>('')
  /** 本次挂载期间已经流式聊过的会话 —— 别用历史覆盖它们的现场状态 */
  const streamedRef = useRef<Set<string>>(new Set())

  // 换会话：把那个会话的历史拉回来
  useEffect(() => {
    if (!routeThreadId) {
      chat.reset()
      loadedRef.current = ''
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

  const statusHint = useMemo(() => {
    if (chat.error) return chat.error
    if (chat.busy) return '思考中…'
    return ''
  }, [chat.error, chat.busy])

  return (
    <div className="flex h-full flex-col">
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        {empty ? (
          <div className="flex h-full flex-col items-center justify-center gap-6 px-6">
            <div className="text-center">
              <h1 className="text-2xl font-semibold tracking-tight text-ink">企智搭·代码平台</h1>
              <p className="mt-2 text-sm text-ink-muted">
                在你的代码工作区里干活 —— 读代码、改代码、跑命令、给结果。
              </p>
            </div>
            <Composer
              threadId={threadId}
              centered
              busy={chat.busy}
              onSend={(text, attachments, providerId) => void send(text, attachments, providerId)}
              onStop={chat.abort}
            />
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
          />
        </div>
      )}
    </div>
  )
}
